"""透過隔離真 API／DB 驗證保存卡組、重新登入與來源回查。"""

import httpx
import psycopg
from threading import Event, Thread

import runtime.api.app as api
from runtime import card_generation
from browser_e2e_runner import PORT, local_api, main as run_browser
from product_fixtures import closed_loop
from materials.test_card_generation import response


def test_card_sets_browser(closed_loop, monkeypatch):
    _, _, settings, _, dsn, _ = closed_loop
    monkeypatch.setenv("STUDYDY_E2E_CARDS", "true")
    def reject(*args, **kwargs):
        raise AssertionError("MODEL_NOT_ALLOWED")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", reject)
    app = api.create_app(api.ApiSettings(
        profile="local", public_origin=f"http://127.0.0.1:{PORT}", secure_cookie=False,
        local_config=settings, dsn=dsn,
    ))
    stop = Event()
    failures = []

    def generate():
        try:
            while not stop.wait(.05):
                card_generation.step(dsn=dsn, call=response)
        except Exception as error:
            failures.append(error)

    worker = Thread(target=generate, daemon=True)
    worker.start()
    try:
        with local_api(app):
            assert run_browser("e2e/api/concept-cards.spec.ts") == 0
    finally:
        stop.set()
        worker.join(timeout=10)
    assert not worker.is_alive()
    assert failures == []
    with psycopg.connect(dsn) as connection:
        assert connection.execute("SELECT count(*) FROM study_sessions").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM answer_events").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM materials").fetchone() == (1,)
