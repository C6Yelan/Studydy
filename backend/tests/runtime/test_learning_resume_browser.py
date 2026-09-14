"""以真 Browser/API/DB 驗證原題、原回饋及提交回應遺失；restore 不得生成或寫入。"""

import httpx
import psycopg

import runtime.api.app as api_app
from browser_e2e_runner import local_api, main as run_browser
from test_closed_loop_v1 import closed_loop
from test_material_library import library_materials, product_snapshot
from test_learning_resume import learning_records


def test_learning_resume_browser_preserves_records_and_recovers_lost_response(learning_records, monkeypatch):
    fixture = learning_records
    monkeypatch.setattr(api_app, "runtime_binding", lambda _: {})
    app = api_app.create_app(api_app.ApiSettings(
        profile="local", public_origin="http://127.0.0.1:4173", secure_cookie=False,
        local_config=fixture["settings"], dsn=fixture["dsn"],
    ))
    product_writes = []
    read_checks = []
    model_calls = []

    @app.middleware("http")
    async def observe_restore(request, call_next):
        path = request.url.path
        is_restore = request.method == "GET" and (path.endswith("/resume") or path == "/v1/materials")
        before = product_snapshot(fixture["dsn"]) if is_restore else None
        if request.method != "GET" and not path.startswith("/v1/session"):
            product_writes.append(path)
        response = await call_next(request)
        if is_restore:
            read_checks.append(before == product_snapshot(fixture["dsn"]))
        return response

    def reject_model(*_args, **_kwargs):
        model_calls.append("unexpected-http")
        raise AssertionError("RESTORE_MUST_NOT_CALL_MODEL")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", reject_model)
    monkeypatch.setenv("STUDYDY_E2E_RESUME", "true")
    before = product_snapshot(fixture["dsn"])
    with local_api(app):
        assert run_browser("e2e/learning-resume.spec.ts", production=True) == 0
    after = product_snapshot(fixture["dsn"])
    for table in ("materials", "artifacts", "material_processing_runs", "knowledge_structures", "assessments"):
        assert after[table] == before[table]
    assert after["study_sessions"][0] == before["study_sessions"][0]
    assert after["answer_events"][0] == before["answer_events"][0] + 1
    assert len(product_writes) == 2 and all(path.endswith("/submissions") for path in product_writes)
    assert len(read_checks) >= 8 and all(read_checks)
    assert model_calls == []
    with psycopg.connect(fixture["dsn"]) as connection:
        assert connection.execute("SELECT count(*) FROM answer_events WHERE assessment_revision=%s", (fixture["unanswered"].assessment_revision,)).fetchone() == (1,)
        assert connection.execute("SELECT last_event_number FROM study_sessions WHERE study_session_id=%s", (fixture["active"].study_session_id,)).fetchone() == (2,)
