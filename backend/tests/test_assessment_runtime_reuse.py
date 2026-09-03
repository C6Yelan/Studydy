from __future__ import annotations

from pathlib import Path
from threading import Thread

import pytest

import learning_adaptation.assessment_generation as generation_module
import learning_adaptation.assessment_runtime_reuse as reuse_module
from learning_adaptation.assessment_runtime_reuse import AssessmentRuntimeReuse


class _Verifier:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def request(self, request, timeout):
        self.calls.append("verify")
        return {"request": request, "timeout": timeout}

    def abort(self) -> None:
        self.calls.append("verifier_abort")


def _manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []

    class ModelLock:
        def close(self):
            calls.append("lock_exit")

    def model_lock(root, **_):
        assert root == tmp_path
        calls.append("lock_enter")
        return ModelLock()

    def start_verifier(_, __):
        calls.append("verifier_start")
        return _Verifier(calls)

    monkeypatch.setattr(reuse_module, "_acquire_model_lock", model_lock)
    monkeypatch.setattr(reuse_module, "start_assessment_process", start_verifier)
    manager = AssessmentRuntimeReuse(
        {
            "private_runtime_root": str(tmp_path),
            "assessment_runtime_lock": {
                "verifier": {"startup_timeout_seconds": 1}
            },
        },
        idle_seconds=60,
    )
    return manager, calls


def _successful_operation(settings):
    def operation():
        with generation_module.material_analysis_lock(Path(settings["private_runtime_root"])):
            verifier = generation_module.start_assessment_process(settings, 1)
            assert verifier.request({"item": 1}, 1)["request"] == {"item": 1}
            verifier.close()
        return "ok"

    return operation


def test_reuses_ready_processes_and_restores_frozen_generator_globals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    manager, calls = _manager(tmp_path, monkeypatch)
    settings = {"private_runtime_root": str(tmp_path)}
    original_lock = generation_module.material_analysis_lock
    try:
        assert manager.generate(_successful_operation(settings)) == "ok"
        assert manager.generate(_successful_operation(settings)) == "ok"
        assert calls.count("verifier_start") == 1
        assert calls.count("verify") == 2
        profile = manager.profile()
        assert profile.cold_starts == 1
        assert profile.warm_requests == 1
        assert len(profile.request_seconds) == 2
        assert generation_module.material_analysis_lock is original_lock
    finally:
        manager.close()
    assert calls.count("verifier_abort") == 1
    assert calls.count("lock_exit") == 1


def test_failure_discards_processes_and_next_request_starts_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    manager, calls = _manager(tmp_path, monkeypatch)
    settings = {"private_runtime_root": str(tmp_path)}

    def failed():
        with generation_module.material_analysis_lock(tmp_path):
            generation_module.start_assessment_process(settings, 1)
            raise RuntimeError("MODEL_PROCESS_FAILED")

    try:
        with pytest.raises(RuntimeError, match="MODEL_PROCESS_FAILED"):
            manager.generate(failed)
        assert manager.generate(_successful_operation(settings)) == "ok"
        assert calls.count("verifier_start") == 2
        assert manager.profile().cold_starts == 2
    finally:
        manager.close()


def test_runtime_started_in_request_thread_can_close_in_lifespan_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    calls: list[str] = []
    monkeypatch.setattr(
        reuse_module,
        "start_assessment_process",
        lambda *_: _Verifier(calls),
    )
    manager = AssessmentRuntimeReuse(
        {
            "private_runtime_root": str(tmp_path),
            "assessment_runtime_lock": {
                "verifier": {"startup_timeout_seconds": 1}
            },
        },
        idle_seconds=60,
    )
    errors: list[BaseException] = []

    def request_thread():
        try:
            manager.generate(
                _successful_operation(
                    {"private_runtime_root": str(tmp_path)}
                )
            )
        except BaseException as error:
            errors.append(error)

    thread = Thread(target=request_thread)
    thread.start()
    thread.join(5)
    assert not thread.is_alive()
    assert errors == []
    manager.close()
    released_lock = reuse_module._acquire_model_lock(
        tmp_path, wait_seconds=0
    )
    released_lock.close()
