from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import logging
from threading import Event, Thread
from time import monotonic

from .material_processing import (
    claim_next_material_processing_run,
    execute_claimed_material_processing_run,
    recover_interrupted_material_runs,
)
from .material_discard import finish_material_discards
from .source_normalization import normalize_next
from .storage.source_artifacts import reconcile_new_artifacts
from .storage.analysis_archive import reconcile_removed_material_analysis, reconcile_published_checkpoints
from learning_adaptation.assessment_sets import run_next_set

_IDLE_WAIT_SECONDS = 0.1
_STARTUP_WAIT_SECONDS = 5
_SHUTDOWN_WAIT_SECONDS = 10


@dataclass
class RuntimeWorkers:
    dsn: str | None = field(repr=False)
    local_config: dict = field(repr=False)
    _stop: Event = field(default_factory=Event, init=False, repr=False)
    _started: Event = field(default_factory=Event, init=False, repr=False)
    _thread: Thread | None = field(default=None, init=False, repr=False)
    _startup_error: Exception | None = field(default=None, init=False, repr=False)

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("RUNTIME_WORKERS_ALREADY_STARTED")
        # 中斷時由既有 lease／checkpoint 恢復；不能讓卡住的 I/O 阻止程序退出。
        self._thread = Thread(target=self._loop, name="studydy-material-worker", daemon=True)
        try:
            self._thread.start()
        except Exception:
            self._thread = None
            raise RuntimeError("RUNTIME_WORKERS_START_FAILED") from None
        if not self._started.wait(_STARTUP_WAIT_SECONDS):
            self._stop.set()
            # 保留執行緒 reference，禁止在舊 worker 尚未退出時重複 start。
            raise RuntimeError("RUNTIME_WORKERS_START_FAILED")
        if self._startup_error is not None:
            startup_error = self._startup_error
            self._thread.join()
            self._thread = None
            raise startup_error

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=_SHUTDOWN_WAIT_SECONDS)
            if self._thread.is_alive():
                raise RuntimeError("RUNTIME_WORKERS_STOP_FAILED")

    def _loop(self) -> None:
        is_starting = True
        next_recovery = 0.0
        while not self._stop.is_set():
            try:
                if is_starting:
                    for recover in (
                        reconcile_new_artifacts, reconcile_removed_material_analysis,
                        reconcile_published_checkpoints, recover_interrupted_material_runs,
                        finish_material_discards,
                    ):
                        if self._stop.is_set():
                            return
                        recover(dsn=self.dsn)
                    if self._stop.is_set():
                        return
                    next_recovery = monotonic() + 10
                    self._started.set()
                    is_starting = False
                if monotonic() >= next_recovery:
                    reconcile_new_artifacts(dsn=self.dsn)
                    recover_interrupted_material_runs(dsn=self.dsn)
                    reconcile_published_checkpoints(dsn=self.dsn)
                    next_recovery = monotonic() + 10
                if self._stop.is_set():
                    return
                normalize_next(dsn=self.dsn)
                if self._stop.is_set():
                    return
                claim = claim_next_material_processing_run(dsn=self.dsn)
                if claim is not None:
                    execute_claimed_material_processing_run(
                        claim, deepcopy(self.local_config), dsn=self.dsn
                    )
                if self._stop.is_set():
                    return
                run_next_set(dsn=self.dsn)
                finish_material_discards(dsn=self.dsn)
            except Exception as error:
                if is_starting:
                    self._startup_error = error
                    self._started.set()
                    return
                logging.getLogger(__name__).warning("RUNTIME_WORKER_ITERATION_FAILED")
            self._stop.wait(_IDLE_WAIT_SECONDS)


def start_runtime_workers(*, dsn: str | None, local_config: dict) -> RuntimeWorkers:
    workers = RuntimeWorkers(dsn, deepcopy(local_config))
    workers.start()
    return workers
