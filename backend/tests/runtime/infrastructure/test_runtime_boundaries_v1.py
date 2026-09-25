from contextlib import nullcontext
from pathlib import Path

import pytest

import runtime.local_app as local_app
import runtime.local_runtime as local_runtime
import runtime.workers as workers_module
from pdf_evidence.local_ai_process import LocalAIError
from runtime.material_runtime import runtime_binding


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
    import runtime.material_runtime as material_runtime
    monkeypatch.setattr(material_runtime, "preflight_semantic_service", lambda _: pytest.fail("startup must not contact AI"))
    monkeypatch.setattr(material_runtime, "validate_installed_local_runtime", lambda _: pytest.fail("startup must not load OCR"))
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
        def close(self):
            observed.append("ocr-close")

    monkeypatch.setattr(local_runtime, "start_ocr_process", lambda _config: observed.append("ocr-load") or Ocr())
    result = local_runtime.verify_local_runtime({"private_runtime_root": str(tmp_path)})
    assert result == {"status": "succeeded", "command": "verify"}
    assert observed == ["semantic-preflight", "ocr-load", "ocr-close"]

    def fail_ocr(_config):
        raise LocalAIError("CHILD_EXITED")

    monkeypatch.setattr(local_runtime, "start_ocr_process", fail_ocr)
    with pytest.raises(Exception) as failure:
        local_runtime.verify_local_runtime({"private_runtime_root": str(tmp_path)})
    assert getattr(failure.value, "component", None) == "ocr_model"


def test_runtime_verify_reports_lock_contention_as_busy():
    from pdf_evidence.material_pipeline import MaterialAnalysisError
    assert local_runtime._failure(MaterialAnalysisError('RUNTIME_BUSY'))['reason'] == 'RUNTIME_BUSY'


@pytest.mark.parametrize('operation', ['remove-source', 'preview'])
def test_waiting_storage_does_not_block_other_api_requests(tmp_path, monkeypatch, operation):
    import asyncio
    from contextlib import contextmanager
    from threading import Event
    from types import SimpleNamespace
    from uuid import uuid4
    import httpx
    import runtime.api.app as api

    config = local_app.read_local_ai_config_from_environment(_environment(tmp_path))
    app = api.create_app(api.ApiSettings(
        profile='local', public_origin='http://127.0.0.1:4173', secure_cookie=False,
        local_config=config, dsn=None,
    ))
    monkeypatch.setattr(api, '_trusted_learner', lambda *_: SimpleNamespace(learner_id=uuid4()))

    async def scenario():
        entered = asyncio.Event()
        release = Event()
        loop = asyncio.get_running_loop()
        released = []

        def wait_for_storage(*_, **__):
            loop.call_soon_threadsafe(entered.set)
            released.append(release.wait(2))
            raise api.SourceError('SOURCE_BUSY')

        @contextmanager
        def open_source(*args, **kwargs):
            wait_for_storage()
            yield  # pragma: no cover

        monkeypatch.setattr(api, 'remove_staged_source', wait_for_storage)
        monkeypatch.setattr(api, 'open_verified_source_pdf', open_source)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://127.0.0.1:4173') as client:
            if operation == 'remove-source':
                request = client.delete(f'/v1/materials/{uuid4()}/sources/{uuid4()}',
                                        headers={'Origin': 'http://127.0.0.1:4173'})
            else:
                request = client.get(f'/v1/artifacts/{uuid4()}')
            waiting = asyncio.create_task(request)
            try:
                await asyncio.wait_for(entered.wait(), 3)
                response = await asyncio.wait_for(client.get('/v1/openapi.json'), 1)
                assert response.status_code == 200
                assert not waiting.done()
                assert not released
            finally:
                release.set()
                response = await waiting
            assert released == [True]
            assert response.status_code == (409 if operation == 'remove-source' else 404)

    asyncio.run(scenario())


def test_empty_body_dependency_rejects_content_before_storage(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from uuid import uuid4
    import runtime.api.app as api
    config = local_app.read_local_ai_config_from_environment(_environment(tmp_path))
    app = api.create_app(api.ApiSettings(
        profile='local', public_origin='http://127.0.0.1:4173', secure_cookie=False,
        local_config=config, dsn=None,
    ))
    monkeypatch.setattr(api, '_trusted_learner', lambda *_: pytest.fail('body must be checked first'))
    response = TestClient(app).request('DELETE', f'/v1/materials/{uuid4()}/sources/{uuid4()}',
        headers={'Origin': 'http://127.0.0.1:4173'}, content=b'unexpected')
    assert response.status_code == 400
    assert response.json()['reason_code'] == 'REQUEST_INVALID'


def test_worker_recovers_once_and_does_not_own_model_lifecycle(monkeypatch):
    events = []
    monkeypatch.setattr(workers_module, "run_next_set", lambda **_: False)
    monkeypatch.setattr(workers_module, "reconcile_new_artifacts", lambda **_: None)
    monkeypatch.setattr(workers_module, "reconcile_removed_material_analysis", lambda **_: None)
    monkeypatch.setattr(workers_module, "reconcile_published_checkpoints", lambda **_: None)
    monkeypatch.setattr(workers_module, "normalize_next", lambda **_: False)
    monkeypatch.setattr(workers_module, "recover_interrupted_material_runs", lambda **_: events.append("recover") or 0)
    monkeypatch.setattr(workers_module, "finish_material_discards", lambda **_: None)
    monkeypatch.setattr(workers_module, "claim_next_material_processing_run", lambda **_: None)
    worker = workers_module.RuntimeWorkers(None, {})
    worker.start()
    worker.stop()
    assert events == ["recover"]


def test_worker_start_timeout_returns_and_late_recovery_does_not_claim_work(monkeypatch):
    from threading import Event
    entered, release = Event(), Event()
    events = []

    def blocked(**_):
        entered.set()
        assert release.wait(5)

    monkeypatch.setattr(workers_module, '_STARTUP_WAIT_SECONDS', 0.05)
    monkeypatch.setattr(workers_module, 'reconcile_new_artifacts', blocked)
    monkeypatch.setattr(workers_module, 'reconcile_removed_material_analysis', lambda **_: events.append('late recovery'))
    monkeypatch.setattr(workers_module, 'claim_next_material_processing_run', lambda **_: events.append('claim'))
    worker = workers_module.RuntimeWorkers(None, {})
    try:
        with pytest.raises(RuntimeError, match='RUNTIME_WORKERS_START_FAILED'):
            worker.start()
        assert entered.is_set() and worker._thread.is_alive()
        with pytest.raises(RuntimeError, match='RUNTIME_WORKERS_ALREADY_STARTED'):
            worker.start()
    finally:
        release.set()
        worker.stop()
    assert not events


def test_worker_stop_reports_busy_and_stops_before_claiming_next_work(monkeypatch):
    from threading import Event
    entered, release = Event(), Event()
    claimed = []

    def blocked(**_):
        entered.set()
        assert release.wait(5)

    for name in ('reconcile_new_artifacts', 'reconcile_removed_material_analysis',
                 'reconcile_published_checkpoints', 'recover_interrupted_material_runs',
                 'finish_material_discards'):
        monkeypatch.setattr(workers_module, name, lambda **_: None)
    monkeypatch.setattr(workers_module, '_SHUTDOWN_WAIT_SECONDS', 0.05)
    monkeypatch.setattr(workers_module, 'normalize_next', blocked)
    monkeypatch.setattr(workers_module, 'claim_next_material_processing_run', lambda **_: claimed.append(True))
    worker = workers_module.RuntimeWorkers(None, {})
    worker.start()
    try:
        assert entered.wait(1)
        with pytest.raises(RuntimeError, match='RUNTIME_WORKERS_STOP_FAILED'):
            worker.stop()
        assert worker._thread.is_alive()
    finally:
        release.set()
        worker._thread.join(timeout=2)
        worker.stop()
    assert not claimed
