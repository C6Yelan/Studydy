from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import os
from pathlib import Path
from threading import Lock, Timer
import time
from typing import Any, Callable, Iterator

import learning_adaptation.assessment_generation as generation_module
from pdf_evidence.concept_api import start_concept_server

from .assessment_verifier import start_assessment_process


_PATCH_LOCK = Lock()
_IDLE_SECONDS = 60.0


class _HeldModelLock:
    def __init__(self, descriptor: int) -> None:
        self._descriptor = descriptor

    def close(self) -> None:
        if self._descriptor < 0:
            return
        fcntl.flock(self._descriptor, fcntl.LOCK_UN)
        os.close(self._descriptor)
        self._descriptor = -1


def _acquire_model_lock(runtime_root: Path, wait_seconds: float = 5) -> _HeldModelLock:
    if runtime_root.is_symlink():
        raise ValueError("RUNTIME_BINDING_INVALID")
    runtime_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = runtime_root / "material-analysis.lock"
    if lock_path.is_symlink():
        raise ValueError("RUNTIME_BINDING_INVALID")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    deadline = time.monotonic() + wait_seconds
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return _HeldModelLock(descriptor)
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ValueError("RUNTIME_BUSY") from None
                time.sleep(0.05)
    except BaseException:
        os.close(descriptor)
        raise


class AssessmentRuntimeReuseError(RuntimeError):
    """Assessment reusable runtime 無法安全啟動或回收。"""


class _ConceptServerLease:
    def close(self) -> None:
        """單次 generation 結束不關閉 reusable process。"""


class _VerifierLease:
    def __init__(self, process: Any) -> None:
        self._process = process

    def request(self, request: dict[str, Any], timeout_seconds: float) -> Any:
        return self._process.request(request, timeout_seconds)

    def close(self) -> None:
        """單次 generation 結束不送 EOF。"""

    def abort(self) -> None:
        """outer lifecycle 會在 failure 後統一回收兩個 process。"""


@dataclass(frozen=True)
class AssessmentRuntimeProfile:
    cold_starts: int
    warm_requests: int
    qwen_startup_seconds: tuple[float, ...]
    verifier_startup_seconds: tuple[float, ...]
    proposal_inference_seconds: tuple[float, ...]
    repair_inference_seconds: tuple[float, ...]
    verification_seconds: tuple[float, ...]
    request_seconds: tuple[float, ...]
    shutdown_seconds: tuple[float, ...]


class AssessmentRuntimeReuse:
    """在 app lifecycle 內 reuse frozen generator 使用的實體模型 process。"""

    def __init__(
        self,
        local_config: dict[str, Any],
        *,
        idle_seconds: float = _IDLE_SECONDS,
    ) -> None:
        if (
            not isinstance(local_config, dict)
            or type(idle_seconds) not in {int, float}
            or idle_seconds < 0
        ):
            raise ValueError("ASSESSMENT_RUNTIME_REUSE_INVALID")
        self._settings = local_config
        self._runtime_root = Path(local_config["private_runtime_root"])
        self._idle_seconds = float(idle_seconds)
        self._state_lock = Lock()
        self._model_lock_context: Any | None = None
        self._server: Any | None = None
        self._verifier: Any | None = None
        self._idle_timer: Timer | None = None
        self._active_requests = 0
        self._closed = False
        self._cold_starts = 0
        self._warm_requests = 0
        self._timings: dict[str, list[float]] = {
            "qwen_startup": [],
            "verifier_startup": [],
            "proposal_inference": [],
            "repair_inference": [],
            "verification": [],
            "request": [],
            "shutdown": [],
        }

    def _cancel_idle_timer(self) -> None:
        if self._idle_timer is not None:
            self._idle_timer.cancel()
            self._idle_timer = None

    def _start_locked(self) -> None:
        if self._closed:
            raise AssessmentRuntimeReuseError("ASSESSMENT_RUNTIME_REUSE_CLOSED")
        if self._server is not None and self._verifier is not None:
            self._warm_requests += 1
            return
        held_lock = _acquire_model_lock(self._runtime_root)
        try:
            qwen_started = time.monotonic()
            server = start_concept_server(self._settings)
            self._timings["qwen_startup"].append(
                time.monotonic() - qwen_started
            )
            verifier_started = time.monotonic()
            verifier = start_assessment_process(
                self._settings,
                self._settings["assessment_runtime_lock"]["verifier"][
                    "startup_timeout_seconds"
                ],
            )
            self._timings["verifier_startup"].append(
                time.monotonic() - verifier_started
            )
        except BaseException:
            if "server" in locals():
                server.close()
            held_lock.close()
            raise
        self._model_lock_context = held_lock
        self._server = server
        self._verifier = verifier
        self._cold_starts += 1

    def _shutdown_locked(self) -> None:
        self._cancel_idle_timer()
        if (
            self._server is None
            and self._verifier is None
            and self._model_lock_context is None
        ):
            return
        started = time.monotonic()
        verifier = self._verifier
        server = self._server
        held_lock = self._model_lock_context
        self._verifier = None
        self._server = None
        self._model_lock_context = None
        try:
            if verifier is not None:
                verifier.abort()
        finally:
            try:
                if server is not None:
                    server.close()
            finally:
                if held_lock is not None:
                    held_lock.close()
        self._timings["shutdown"].append(time.monotonic() - started)

    def _idle_shutdown(self) -> None:
        with self._state_lock:
            self._idle_timer = None
            if self._active_requests == 0:
                self._shutdown_locked()

    @contextmanager
    def _reuse_lock(self, runtime_root: Path, **_: Any) -> Iterator[None]:
        if runtime_root != self._runtime_root:
            raise ValueError("RUNTIME_BINDING_INVALID")
        with self._state_lock:
            self._cancel_idle_timer()
            self._start_locked()
            self._active_requests += 1
        try:
            yield
        finally:
            with self._state_lock:
                self._active_requests -= 1
                if self._active_requests == 0:
                    self._idle_timer = Timer(
                        self._idle_seconds, self._idle_shutdown
                    )
                    self._idle_timer.daemon = True
                    self._idle_timer.start()

    def _server_lease(self, _: dict[str, Any]) -> _ConceptServerLease:
        if self._server is None:
            raise AssessmentRuntimeReuseError("ASSESSMENT_RUNTIME_NOT_READY")
        return _ConceptServerLease()

    def _verifier_lease(
        self, _: dict[str, Any], __: float
    ) -> _VerifierLease:
        if self._verifier is None:
            raise AssessmentRuntimeReuseError("ASSESSMENT_RUNTIME_NOT_READY")
        return _VerifierLease(self._verifier)

    def _timed_request_stage(self, original: Callable[..., Any]) -> Callable[..., Any]:
        def timed(*args: Any, **kwargs: Any) -> Any:
            stage = args[2]
            name = (
                "repair_inference"
                if stage["generation"]["max_tokens"] == 3400
                else "proposal_inference"
            )
            started = time.monotonic()
            try:
                return original(*args, **kwargs)
            finally:
                self._timings[name].append(time.monotonic() - started)

        return timed

    def _timed_rank(self, original: Callable[..., Any]) -> Callable[..., Any]:
        def timed(*args: Any, **kwargs: Any) -> Any:
            started = time.monotonic()
            try:
                return original(*args, **kwargs)
            finally:
                self._timings["verification"].append(
                    time.monotonic() - started
                )

        return timed

    def generate(self, operation: Callable[[], Any]) -> Any:
        """暫時替 frozen generator 提供 lease；結束後完整還原 globals。"""

        with _PATCH_LOCK:
            originals = {
                "material_analysis_lock": generation_module.material_analysis_lock,
                "start_concept_server": generation_module.start_concept_server,
                "start_assessment_process": generation_module.start_assessment_process,
                "_request_stage": generation_module._request_stage,
                "_rank_candidates": generation_module._rank_candidates,
            }
            generation_module.material_analysis_lock = self._reuse_lock
            generation_module.start_concept_server = self._server_lease
            generation_module.start_assessment_process = self._verifier_lease
            generation_module._request_stage = self._timed_request_stage(
                originals["_request_stage"]
            )
            generation_module._rank_candidates = self._timed_rank(
                originals["_rank_candidates"]
            )
            started = time.monotonic()
            try:
                value = operation()
            except BaseException:
                with self._state_lock:
                    self._shutdown_locked()
                raise
            finally:
                self._timings["request"].append(
                    time.monotonic() - started
                )
                for name, original in originals.items():
                    setattr(generation_module, name, original)
            return value

    def profile(self) -> AssessmentRuntimeProfile:
        with self._state_lock:
            return AssessmentRuntimeProfile(
                cold_starts=self._cold_starts,
                warm_requests=self._warm_requests,
                qwen_startup_seconds=tuple(self._timings["qwen_startup"]),
                verifier_startup_seconds=tuple(
                    self._timings["verifier_startup"]
                ),
                proposal_inference_seconds=tuple(
                    self._timings["proposal_inference"]
                ),
                repair_inference_seconds=tuple(
                    self._timings["repair_inference"]
                ),
                verification_seconds=tuple(
                    self._timings["verification"]
                ),
                request_seconds=tuple(self._timings["request"]),
                shutdown_seconds=tuple(self._timings["shutdown"]),
            )

    def close(self) -> None:
        with self._state_lock:
            self._closed = True
            self._shutdown_locked()
