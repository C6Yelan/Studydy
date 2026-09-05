from copy import deepcopy
from contextlib import nullcontext
from pathlib import Path

import pytest

import runtime.api.app as api_app
import runtime.local_app as local_app
import runtime.local_runtime as local_runtime
import runtime.workers as workers_module
from pdf_evidence.local_ai_process import LocalAIError
from runtime.material_processing import MaterialProcessingError, runtime_binding


def _environment(tmp_path: Path) -> dict[str, str]:
    return {
        "STUDYDY_PROFILE": "local",
        "STUDYDY_PUBLIC_ORIGIN": "http://127.0.0.1:4173",
        "STUDYDY_SECURE_COOKIE": "false",
        "STUDYDY_LOCAL_RUNTIME_ROOT": str(tmp_path / "installed"),
    }


def test_local_config_has_one_python_one_semantic_lock_and_no_verifier(tmp_path):
    config = local_app.read_local_ai_config_from_environment(_environment(tmp_path))
    assert set(config) == {
        "private_runtime_root", "runtime_lock", "python_executable",
        "site_packages", "ocr_model_root",
    }
    assert config["runtime_lock"]["python"] == "3.12"
    assert config["runtime_lock"]["semantic_service"]["model_id"] == "Qwen/Qwen3.8-27B-FP8"
    assert "verifier" not in str(config).casefold()
    assert "mdeberta" not in str(config).casefold()
    tampered = deepcopy(config)
    tampered["runtime_lock"]["assessment"]["verifier"] = {"model": "second-authority"}
    with pytest.raises(MaterialProcessingError):
        runtime_binding(tampered)


def test_local_app_composition_preflights_then_starts_uvicorn(tmp_path, monkeypatch):
    observed = []
    monkeypatch.setattr(api_app, "runtime_preflight", lambda config: observed.append(("preflight", deepcopy(config))))
    app = local_app.create_local_app(
        profile="local", public_origin="http://127.0.0.1:4173", secure_cookie=False,
        local_config=local_app.read_local_ai_config_from_environment(_environment(tmp_path)), dsn=None,
    )
    assert app.version == "3.0.0"
    assert observed[0][0] == "preflight"

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
    monkeypatch.setattr(workers_module, "recover_interrupted_material_runs", lambda **_: events.append("recover") or 0)
    monkeypatch.setattr(workers_module, "claim_next_material_processing_run", lambda **_: None)
    worker = workers_module.RuntimeWorkers(None, {})
    worker.start()
    worker.stop()
    assert events == ["recover"]
    assert not hasattr(workers_module, "start_assessment_process")
    assert not hasattr(workers_module, "material_analysis_lock")


def test_source_tree_has_no_qwen_process_owner_or_retired_semantic_modules():
    root = Path(__file__).parents[3]
    production = "\n".join(path.read_text(encoding="utf-8") for path in (root / "backend/src").rglob("*.py"))
    assert "subprocess.Popen" not in production.replace((root / "backend/src/pdf_evidence/local_ai_process.py").read_text(), "")
    assert "Qwen3-14B" not in production
    assert not (root / "backend/src/pdf_evidence/text_first_run.py").exists()
    assert not (root / "backend/src/knowledge_map/formal_concepts.py").exists()
    assert not (root / "local_ai/assessment-runtime-lock.json").exists()
