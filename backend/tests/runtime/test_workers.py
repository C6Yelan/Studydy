from multiprocessing import get_context
from pathlib import Path
from threading import Event

import pytest
import runtime.workers as worker_module


def _hold_agent1_lock(runtime_root: str, acquired, release) -> None:
    from pdf_evidence.text_first_run import _agent1_lock

    with _agent1_lock(Path(runtime_root)):
        acquired.set()
        release.wait(10)


def test_second_process_does_not_recover_or_claim_before_ownership(
    tmp_path, monkeypatch
):
    runtime_root = tmp_path / "runtime"
    process_context = get_context("spawn")
    acquired = process_context.Event()
    release = process_context.Event()
    lock_owner = process_context.Process(
        target=_hold_agent1_lock,
        args=(str(runtime_root), acquired, release),
    )
    lock_owner.start()
    assert acquired.wait(5)

    calls = []
    recovered = Event()
    executed = Event()
    claim_marker = object()
    workers = None

    def recover(*, dsn):
        calls.append("recover")
        recovered.set()

    def claim(*, dsn):
        calls.append("claim")
        return claim_marker

    def execute(claimed, local_config, *, dsn):
        calls.append("execute")
        assert claimed is claim_marker
        executed.set()
        assert workers is not None
        workers._stop.set()

    monkeypatch.setattr(worker_module, "recover_interrupted_material_runs", recover)
    monkeypatch.setattr(worker_module, "claim_next_material_processing_run", claim)
    monkeypatch.setattr(worker_module, "execute_claimed_material_processing_run", execute)

    try:
        workers = worker_module.RuntimeWorkers(
            None, {"private_runtime_root": str(runtime_root)}
        )
        workers.start()
        assert not recovered.wait(0.25)
        assert calls == []

        release.set()
        assert executed.wait(5)
        workers.stop()
        assert calls[:3] == ["recover", "claim", "execute"]
    finally:
        release.set()
        if workers is not None:
            workers.stop()
        lock_owner.join(5)
    assert lock_owner.exitcode == 0
    with worker_module._agent1_lock(runtime_root, wait_seconds=0):
        pass


def test_initial_recovery_failure_stops_startup_and_releases_lock(
    tmp_path, monkeypatch
):
    runtime_root = tmp_path / "runtime"

    def failed_recovery(*, dsn):
        raise RuntimeError("MATERIAL_RUN_STORAGE_FAILED")

    monkeypatch.setattr(
        worker_module, "recover_interrupted_material_runs", failed_recovery
    )
    monkeypatch.setattr(
        worker_module,
        "claim_next_material_processing_run",
        lambda **_: pytest.fail("claim must follow successful recovery"),
    )
    workers = worker_module.RuntimeWorkers(
        None, {"private_runtime_root": str(runtime_root)}
    )

    with pytest.raises(RuntimeError, match="MATERIAL_RUN_STORAGE_FAILED"):
        workers.start()
    with worker_module._agent1_lock(runtime_root, wait_seconds=0):
        pass
