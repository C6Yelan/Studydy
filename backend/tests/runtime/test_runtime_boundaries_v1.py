from contextlib import nullcontext
from pathlib import Path

import pytest

import runtime.local_app as local_app
import runtime.local_runtime as local_runtime
import runtime.workers as workers_module
from pdf_evidence.local_ai_process import LocalAIError
from runtime.material_processing import runtime_binding


def _environment(tmp_path: Path) -> dict[str, str]:
    return {
        "STUDYDY_PROFILE": "local",
        "STUDYDY_PUBLIC_ORIGIN": "http://127.0.0.1:4173",
        "STUDYDY_SECURE_COOKIE": "false",
        "STUDYDY_LOCAL_RUNTIME_ROOT": str(tmp_path / "installed"),
    }


@pytest.mark.parametrize("field,value", [("model_id", "example/other-model"), ("model_revision", "a" * 40)])
def test_stored_runtime_binding_rejects_wrong_identity_even_with_valid_hash(tmp_path, field, value):
    """重算 binding hash 不能讓與保存快照不同的模型身分通過。"""
    from pdf_evidence.ocr_page_evidence import canonical_sha256
    from runtime.material_runtime import lock_matches_binding
    config = local_app.read_local_ai_config_from_environment(_environment(tmp_path))
    binding = runtime_binding(config)
    assert lock_matches_binding(config['runtime_lock'], binding)
    binding[field] = value
    binding["runtime_binding_sha256"] = canonical_sha256({
        key: item for key, item in binding.items() if key != "runtime_binding_sha256"
    })
    assert not lock_matches_binding(config['runtime_lock'], binding)


def test_local_app_composition_validates_settings_without_ai_then_starts_uvicorn(tmp_path, monkeypatch):
    observed = []
    import runtime.material_processing as processing
    monkeypatch.setattr(processing, "preflight_semantic_service", lambda _: pytest.fail("startup must not contact AI"))
    monkeypatch.setattr(processing, "validate_installed_local_runtime", lambda _: pytest.fail("startup must not load OCR"))
    app = local_app.create_local_app(
        profile="local", public_origin="http://127.0.0.1:4173", secure_cookie=False,
        local_config=local_app.read_local_ai_config_from_environment(_environment(tmp_path)), dsn=None,
    )
    assert app.version == "v1"

    monkeypatch.setattr(local_app, "create_local_app", lambda **arguments: observed.append(("create", arguments)) or app)
    monkeypatch.setattr(local_app.uvicorn, "run", lambda created, **arguments: observed.append(("run", created, arguments)))
    local_app.run_local_app(environment=_environment(tmp_path), port=8183)
    assert observed[-1][0] == "run"


def test_runtime_verify_loads_only_ocr_sidecar(tmp_path, monkeypatch):
    observed = []
    monkeypatch.setattr(local_runtime, "runtime_preflight", lambda _config: observed.append("semantic-preflight"))
    monkeypatch.setattr(local_runtime, "material_analysis_lock", lambda _root: nullcontext())

    class Ocr:
        def close(self): observed.append("ocr-close")

    monkeypatch.setattr(local_runtime, "start_ocr_process", lambda _config: observed.append("ocr-load") or Ocr())
    result = local_runtime.verify_local_runtime({"private_runtime_root": str(tmp_path)})
    assert result == {"status": "succeeded", "command": "verify"}
    assert observed == ["semantic-preflight", "ocr-load", "ocr-close"]

    monkeypatch.setattr(local_runtime, "start_ocr_process", lambda _config: (_ for _ in ()).throw(LocalAIError("CHILD_EXITED")))
    with pytest.raises(Exception) as failure:
        local_runtime.verify_local_runtime({"private_runtime_root": str(tmp_path)})
    assert getattr(failure.value, "component", None) == "ocr_model"


def test_worker_recovers_once_and_does_not_own_model_lifecycle(monkeypatch):
    events = []
    monkeypatch.setattr(workers_module,"run_next_set",lambda **_:False)
    monkeypatch.setattr(workers_module,"reconcile_new_artifacts",lambda **_:None)
    monkeypatch.setattr(workers_module,"reconcile_removed_material_analysis",lambda **_:None)
    monkeypatch.setattr(workers_module,"reconcile_published_checkpoints",lambda **_:None)
    monkeypatch.setattr(workers_module,"normalize_next",lambda **_:False)
    monkeypatch.setattr(workers_module, "recover_interrupted_material_runs", lambda **_: events.append("recover") or 0)
    monkeypatch.setattr(workers_module, "finish_material_discards", lambda **_: None)
    monkeypatch.setattr(workers_module, "claim_next_material_processing_run", lambda **_: None)
    worker = workers_module.RuntimeWorkers(None, {})
    worker.start()
    worker.stop()
    assert events == ["recover"]
