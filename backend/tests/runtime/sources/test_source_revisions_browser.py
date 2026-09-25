"""真 API／DB／轉檔與 worker 的 B3-A browser；只有語意回應為受控 fixture。"""

import httpx

import runtime.api.app as api
from browser_e2e_runner import PORT, local_api, main as run_browser
from runtime.workers import RuntimeWorkers
from product_fixtures import closed_loop
from test_source_normalization import normalizer
from test_source_revisions import pdf, revisions


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
            assert run_browser("e2e/initial-sources-real.spec.ts") == 0
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
