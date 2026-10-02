"""正式前端＋真 API／隔離 DB；卡組 CRUD／翻卡／續讀不改變學習權威。"""
import json

import httpx

import runtime.api.app as api
from assessment_fixtures import concept_fixture
from browser_e2e_runner import PORT, local_api, main as run_browser
from learning_adaptation.learner_progress import derive_learner_progress
from product_fixtures import closed_loop, product_snapshot


def test_concept_cards_browser_is_read_only(closed_loop, monkeypatch):
    learner, source, settings, document, dsn, _ = closed_loop
    f = concept_fixture(closed_loop, source_review_required=True)
    monkeypatch.setattr(api, "runtime_binding", lambda _: {})
    app = api.create_app(api.ApiSettings(
        profile="local", public_origin=f"http://127.0.0.1:{PORT}", secure_cookie=False,
        local_config=settings, dsn=dsn,
    ))
    writes, reads = [], []

    @app.middleware("http")
    async def observe(request, call_next):
        if request.method == "GET":
            reads.append(request.url.path)
        elif not request.url.path.startswith("/v1/session"):
            writes.append(request.url.path)
        return await call_next(request)

    def no_model_http(*_args, **_kwargs):
        raise AssertionError("CARDS_MUST_NOT_CALL_MODEL")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", no_model_http)
    monkeypatch.setenv("STUDYDY_E2E_CARDS", "true")
    monkeypatch.setenv("STUDYDY_E2E_CARDS_DATA", json.dumps([
        {"material": str(source.material_id), "run": document["run_id"], "revision": document["revision"], "labels": ["Stack"]},
        {"material": str(f["source"].material_id), "run": str(f["run"].run_id), "revision": f["document"]["revision"], "labels": ["Signals", "Other topic"]},
    ]))
    before = product_snapshot(dsn)
    progress = derive_learner_progress(learner, f["study"].study_session_id, dsn=dsn)
    with local_api(app):
        assert run_browser("e2e/api/concept-cards.spec.ts") == 0
    assert product_snapshot(dsn) == before
    assert derive_learner_progress(learner, f["study"].study_session_id, dsn=dsn) == progress
    assert writes and all("/card-sets" in path for path in writes)
    assert sum(path.endswith("/cards") for path in reads) >= 4
    assert any("/evidence/" in path and path.endswith("/source") for path in reads)
