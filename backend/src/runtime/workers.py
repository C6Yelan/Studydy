from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass, field
from functools import partial
import logging
import os
from threading import Event, Thread
from time import monotonic

from .material_processing import (
    claim_next_material_processing_run,
    execute_claimed_material_processing_run,
    recover_interrupted_material_runs,
)
from . import knowledge_chapters, luna_calls
from .material_discard import finish_material_discards
from .media_sources import cleanup_stale_uploads
from .storage_usage import release_stale_upload_reservations
from .export_cards import cleanup_stale_uploads as cleanup_exports

from . import material_notes, material_review, version_moves
from .source_normalization import normalize_next
from .storage.artifacts import reconcile_new_artifacts
from .storage.analysis_archive import (
    reconcile_removed_material_analysis,
    reconcile_published_checkpoints,
)
from learning_adaptation.assessment_sets import run_next_set

_IDLE_WAIT_SECONDS = 0.1
_STARTUP_WAIT_SECONDS = 5
_SHUTDOWN_WAIT_SECONDS = 10


class BackgroundWorker:
    """共用執行緒啟停；卡片與研究持續推進到無工作，其餘每秒輪詢。"""

    def __init__(self, step, *, name, error_code, drain=False):
        self.step = step
        self.error_code = error_code
        self.drain = drain
        self.stop_event = Event()
        self.thread = Thread(target=self.loop, name=name, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=2)
        if self.thread.is_alive():
            raise RuntimeError(self.error_code.removesuffix("_FAILED") + "_STOP_FAILED")

    def is_alive(self):
        return not self.stop_event.is_set() and self.thread.is_alive()

    def loop(self):
        delay = 0 if self.drain else 1
        while not self.stop_event.wait(delay):
            try:
                progressed = self.step()
            except Exception as error:
                logging.getLogger(__name__).warning(
                    "%s error_type=%s", self.error_code, type(error).__name__
                )
                progressed = False
            delay = (0 if progressed else 2) if self.drain else 1


@contextmanager
def run_workers(dsn, local_config):
    """集中管理啟停順序；部分啟動或關閉失敗仍須清理其他工作。"""
    from . import card_generation, podcast_scenes, podcast_videos, podcast_worker
    from . import knowledge_chapters, research, topics, voice

    counts = {}
    for key in ("research", "card", "podcast"):
        try:
            counts[key] = int(os.environ.get(f"STUDYDY_{key.upper()}_WORKERS", "1"))
            if counts[key] < 1:
                raise ValueError
        except ValueError:
            raise ValueError(f"{key.upper()}_WORKER_COUNT_INVALID") from None

    research_config = deepcopy(local_config)

    def research_step():
        progressed = topics.step(dsn=dsn)
        # 兩者都須執行，不能讓短路求值跳過 research。
        return research.step(dsn=dsn, config=research_config) or progressed

    active = {"runtime": RuntimeWorkers(dsn, deepcopy(local_config))}
    try:
        active["runtime"].start()
        for key, step, name in (
            ("voice", partial(voice.step, dsn=dsn), "studydy-voice"),
            ("scene", partial(podcast_scenes.step, dsn=dsn), "studydy-scenes"),
            ("video", partial(podcast_videos.step, dsn=dsn), "podcast-video-worker"),
            ("research", research_step, "studydy-research"),
            ("card", partial(card_generation.step, dsn=dsn), "studydy-cards"),
            ("podcast", partial(podcast_worker.step, dsn=dsn, workers=counts["podcast"]), "studydy-podcast-worker"),
        ):
            # 卡組、Podcast、研究以 SKIP LOCKED／教材列鎖加 lease 領取工作，可多條並行。
            for index in range(1 if key == "podcast" else counts.get(key, 1)):
                suffix = f"-{index + 1}" if index else ""
                active[key + suffix] = BackgroundWorker(
                    step,
                    name=name + suffix,
                    error_code=f"{key.upper()}_WORKER_FAILED",
                    drain=key in {"research", "card"},
                ).start()
        # 章節分區：一次一份，教材分析進行中時等待；關閉時把進行中的工作放回佇列。
        chapters = BackgroundWorker(
            lambda: knowledge_chapters.step(dsn=dsn, stop=chapters.stop_event),
            name="studydy-chapters",
            error_code="CHAPTERS_WORKER_FAILED",
        )
        active["chapters"] = chapters.start()
        yield lambda: all(worker.is_alive() for worker in active.values())
    finally:
        # ExitStack 確保一個 stop 拋錯時仍呼叫其餘 stop。
        with ExitStack() as shutdown:
            for key in reversed(
                ("chapters", "podcast", "scene", "video", "research", "card", "voice", "runtime")
            ):
                for name in active:
                    if name == key or name.startswith(key + "-"):
                        shutdown.callback(active[name].stop)


@dataclass
class RuntimeWorkers:
    dsn: str | None = field(repr=False)
    local_config: dict = field(repr=False)
    _stop: Event = field(default_factory=Event, init=False, repr=False)
    _started: Event = field(default_factory=Event, init=False, repr=False)
    _thread: Thread | None = field(default=None, init=False, repr=False)
    _material_threads: list[Thread] = field(default_factory=list, init=False, repr=False)
    _assessment_thread: Thread | None = field(default=None, init=False, repr=False)
    _maintenance_thread: Thread | None = field(default=None, init=False, repr=False)
    _startup_error: Exception | None = field(default=None, init=False, repr=False)

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("RUNTIME_WORKERS_ALREADY_STARTED")
        try:
            count = int(os.environ.get("STUDYDY_MATERIAL_WORKERS", "1"))
            if count < 1:
                raise ValueError
        except ValueError:
            raise ValueError("MATERIAL_WORKER_COUNT_INVALID") from None
        material_notes.chapter_concurrency()
        material_review.review_concurrency()
        # lease／checkpoint 負責中斷恢復，阻塞的 I/O 不應卡住程序退出。
        self._thread = Thread(
            target=self._loop, name="studydy-material-worker", daemon=True
        )
        try:
            self._thread.start()
        except Exception:
            self._thread = None
            raise RuntimeError("RUNTIME_WORKERS_START_FAILED") from None
        if not self._started.wait(_STARTUP_WAIT_SECONDS):
            self._stop.set()
            # 保留執行緒 reference，避免尚未退出時重複啟動 worker。
            raise RuntimeError("RUNTIME_WORKERS_START_FAILED")
        if self._startup_error is not None:
            startup_error = self._startup_error
            self._thread.join()
            self._thread = None
            raise startup_error
        # 排程不依賴模型或測試旗標；長教材工作不能阻塞評量與資料復原。
        for index in range(1, count):
            thread = Thread(target=lambda: self._loop(startup=False),
                            name=f"studydy-material-worker-{index + 1}", daemon=True)
            try:
                thread.start()
                self._material_threads.append(thread)
            except Exception:
                self.stop()
                raise RuntimeError("RUNTIME_WORKERS_START_FAILED") from None
        for attribute, target, name in (
            ("_assessment_thread", self._assessment_loop, "studydy-assessment-worker"),
            ("_maintenance_thread", self._maintenance_loop, "studydy-maintenance-worker"),
        ):
            thread = Thread(
                target=target,
                name=name,
                daemon=True,
            )
            try:
                thread.start()
                setattr(self, attribute, thread)
            except Exception:
                try:
                    self.stop()
                except RuntimeError:
                    logging.getLogger(__name__).warning("RUNTIME_WORKERS_STOP_FAILED")
                raise RuntimeError("RUNTIME_WORKERS_START_FAILED") from None

    def stop(self) -> None:
        self._stop.set()
        threads = (self._thread, *self._material_threads, self._assessment_thread, self._maintenance_thread)
        for thread in threads:
            if thread is not None:
                thread.join(timeout=_SHUTDOWN_WAIT_SECONDS)
        if any(thread is not None and thread.is_alive() for thread in threads):
            raise RuntimeError("RUNTIME_WORKERS_STOP_FAILED")

    def is_alive(self):
        return self._started.is_set() and not self._stop.is_set() and all(
            thread is not None and thread.is_alive()
            for thread in (self._thread, *self._material_threads, self._assessment_thread, self._maintenance_thread)
        )

    def _assessment_loop(self):
        while not self._stop.is_set():
            try:
                run_next_set(dsn=self.dsn, shutdown=self._stop)
            except Exception as error:
                logging.getLogger(__name__).warning(
                    "ASSESSMENT_WORKER_ITERATION_FAILED error_type=%s", type(error).__name__
                )
            self._stop.wait(_IDLE_WAIT_SECONDS)

    def _maintenance_loop(self):
        next_recovery = monotonic() + 10
        while not self._stop.is_set():
            try:
                luna_calls.cleanup()
                if monotonic() >= next_recovery:
                    for recover in (
                        reconcile_new_artifacts,
                        recover_interrupted_material_runs,
                        reconcile_published_checkpoints,
                    ):
                        if self._stop.is_set():
                            return
                        recover(dsn=self.dsn)
                    next_recovery = monotonic() + 10
                    try:
                        # 暫存清理失敗不能擋住其他維護工作。
                        cleanup_stale_uploads()
                        release_stale_upload_reservations(dsn=self.dsn)
                        cleanup_exports()
                    except Exception as error:
                        logging.getLogger(__name__).warning(
                            "UPLOAD_CLEANUP_FAILED error_type=%s", type(error).__name__
                        )
                if self._stop.is_set():
                    return
                finish_material_discards(dsn=self.dsn)
            except Exception as error:
                logging.getLogger(__name__).warning(
                    "MAINTENANCE_WORKER_ITERATION_FAILED error_type=%s", type(error).__name__
                )
            self._stop.wait(_IDLE_WAIT_SECONDS)

    def _loop(self, *, startup=True) -> None:
        is_starting = startup
        while not self._stop.is_set():
            try:
                if is_starting:
                    for recover in (
                        reconcile_new_artifacts,
                        reconcile_removed_material_analysis,
                        reconcile_published_checkpoints,
                        recover_interrupted_material_runs,
                        finish_material_discards,
                    ):
                        if self._stop.is_set():
                            return
                        recover(dsn=self.dsn)
                    if self._stop.is_set():
                        return
                    self._started.set()
                    is_starting = False
                if self._stop.is_set():
                    return
                normalize_next(dsn=self.dsn)
                if self._stop.is_set():
                    return
                # 等進行中的章節請求結束才領取分析，避免兩者同時使用語意服務。
                with knowledge_chapters.request_gate:
                    claim = claim_next_material_processing_run(dsn=self.dsn)
                if claim is not None:
                    execute_claimed_material_processing_run(
                        claim,
                        deepcopy(self.local_config),
                        dsn=self.dsn,
                        shutdown=self._stop,
                    )
                if not self._stop.is_set():
                    version_moves.run_next(dsn=self.dsn)
                if not self._stop.is_set():
                    material_notes.step(dsn=self.dsn, stop=self._stop)
            except Exception as error:
                if is_starting:
                    self._startup_error = error
                    self._started.set()
                    return
                logging.getLogger(__name__).warning(
                    "RUNTIME_WORKER_ITERATION_FAILED error_type=%s", type(error).__name__
                )
            self._stop.wait(_IDLE_WAIT_SECONDS)
