"""依序執行待處理教材工作的背景 worker。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from threading import Event, Thread

from .material_processing import (
    claim_next_material_processing_run,
    execute_claimed_material_processing_run,
    recover_interrupted_material_runs,
)

_IDLE_WAIT_SECONDS = 0.1


@dataclass
class RuntimeWorkers:
    dsn: str | None = field(repr=False)
    local_config: dict = field(repr=False)
    _stop: Event = field(default_factory=Event, init=False, repr=False)
    _thread: Thread | None = field(default=None, init=False, repr=False)

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("RUNTIME_WORKERS_ALREADY_STARTED")
        recover_interrupted_material_runs(dsn=self.dsn)
        self._thread = Thread(target=self._loop, name="studydy-material-worker", daemon=False)
        try:
            self._thread.start()
        except Exception:
            self._thread = None
            raise RuntimeError("RUNTIME_WORKERS_START_FAILED") from None

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                claim = claim_next_material_processing_run(dsn=self.dsn)
                if claim is not None:
                    execute_claimed_material_processing_run(
                        claim, deepcopy(self.local_config), dsn=self.dsn
                    )
                    continue
            except Exception:
                pass
            self._stop.wait(_IDLE_WAIT_SECONDS)


def start_runtime_workers(*, dsn: str | None, local_config: dict) -> RuntimeWorkers:
    workers = RuntimeWorkers(dsn, deepcopy(local_config))
    workers.start()
    return workers
