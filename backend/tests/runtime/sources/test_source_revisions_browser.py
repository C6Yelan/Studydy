"""真 API／DB／轉檔與 worker 的 B3-A browser；只有語意回應為受控 fixture。"""

import httpx
from uuid import uuid4

import runtime.api.app as api
from browser_e2e_runner import PORT, local_api, main as run_browser
from runtime.workers import RuntimeWorkers
from product_fixtures import closed_loop
from test_source_revisions import pdf, revisions
from runtime import material_processing as processing
from runtime.source_normalization import create_draft, upload_source, normalize_next, read_sources
from runtime.source_revisions import create_revision
from pdf_evidence.material_pipeline import MaterialAnalysisError


def test_failed_initial_source_can_be_replaced_in_browser(revisions, monkeypatch, tmp_path):
    learner, _, settings, dsn, _, _, execute, _, _, _ = revisions
    material = create_draft(learner.learner_id, 'Failed draft', str(uuid4()), dsn=dsn)
    upload_source(learner.learner_id, material, pdf('Initial readable source.'), 'Initial.pdf',
                  'application/pdf', str(uuid4()), dsn=dsn)
    assert normalize_next(dsn=dsn)
    source, = read_sources(learner.learner_id, material, dsn=dsn)
    create_revision(learner.learner_id, material, [source['normalization_id']], str(uuid4()), settings, dsn=dsn)
    with monkeypatch.context() as patch:
        def fail(*args, **kwargs):
            raise MaterialAnalysisError('SEMANTIC_OUTPUT_TRUNCATED')
        patch.setattr(processing, 'analyze_material', fail)
        assert execute().status == 'failed'
    recovery = tmp_path / 'Recovery.pdf'
    recovery.write_bytes(pdf('A stack removes the last inserted element first.'))
    monkeypatch.setenv('STUDYDY_E2E_REMOVAL_MATERIAL', str(material))
    monkeypatch.setenv('STUDYDY_E2E_REMOVAL_PDF', str(recovery))

    def reject(*args, **kwargs):
        raise AssertionError('MODEL_NOT_ALLOWED')

    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', reject)
    app = api.create_app(api.ApiSettings(profile='local', public_origin=f'http://127.0.0.1:{PORT}',
                                        secure_cookie=False, local_config=settings, dsn=dsn))
    worker = RuntimeWorkers(dsn, settings)
    worker.start()
    try:
        with local_api(app):
            assert run_browser('e2e/api/failed-source-removal.spec.ts') == 0
    finally:
        worker.stop()


def test_real_initial_multiple_sources_browser(revisions, monkeypatch, tmp_path):
    _, _, settings, dsn, _, _, _, _, _, requests = revisions
    source = tmp_path / "Initial.pdf"
    source.write_bytes(pdf("A stack removes the last inserted element first."))
    attempts = []

    def reject(*args, **kwargs):
        attempts.append("unexpected-model-http")
        raise AssertionError("MODEL_NOT_ALLOWED")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", reject)
    monkeypatch.setenv("STUDYDY_E2E_INITIAL_REAL", "true")
    monkeypatch.setenv("STUDYDY_E2E_INITIAL_PDF", str(source))
    before = len(requests)
    app = api.create_app(api.ApiSettings(
        profile="local",
        public_origin=f"http://127.0.0.1:{PORT}",
        secure_cookie=False,
        local_config=settings,
        dsn=dsn,
    ))
    worker = RuntimeWorkers(dsn, settings)
    worker.start()
    try:
        with local_api(app):
            assert run_browser("e2e/api/initial-sources.spec.ts") == 0
    finally:
        worker.stop()
    assert attempts == []
    evidence_pages = {
        row[1]
        for request in requests[before:]
        for section in request["sections"]
        for row in section["evidence"]
    }
    assert evidence_pages == {1, 2, 3}
