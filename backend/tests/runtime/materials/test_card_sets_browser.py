"""透過隔離真 API／DB 驗證保存卡組、重新登入與來源回查。"""

import httpx
import psycopg

import runtime.api.app as api
from browser_e2e_runner import PORT, local_api, main as run_browser
from product_fixtures import closed_loop


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
    with local_api(app):
        assert run_browser("e2e/api/concept-cards.spec.ts") == 0
    with psycopg.connect(dsn) as connection:
        assert connection.execute("SELECT count(*) FROM study_sessions").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM answer_events").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM materials").fetchone() == (1,)


def test_recommendations_read_real_weaknesses_without_mutating_learning(closed_loop, monkeypatch):
    from assessment_fixtures import concept_fixture, create, finish_set, answer
    from product_fixtures import product_snapshot
    from learning_adaptation.learner_progress import derive_learner_progress
    fixture = concept_fixture(closed_loop, count=2)
    group_id = create(fixture)
    finish_set(fixture)
    answer(fixture, group_id, wrong=(1,))
    assert derive_learner_progress(fixture["learner"], fixture["study"].study_session_id,
                                  dsn=fixture["dsn"]).weaknesses
    before = product_snapshot(fixture["dsn"])
    monkeypatch.setenv("STUDYDY_E2E_CARD_RECOMMENDATIONS", "true")
    monkeypatch.setenv("STUDYDY_E2E_RECOMMENDATION_MATERIAL", str(fixture["source"].material_id))
    monkeypatch.setenv("STUDYDY_E2E_RECOMMENDATION_RUN", str(fixture["run"].run_id))
    monkeypatch.setenv("STUDYDY_E2E_RECOMMENDATION_REVISION", fixture["document"]["revision"])
    def reject(*args, **kwargs):
        raise AssertionError("MODEL_NOT_ALLOWED")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", reject)
    app = api.create_app(api.ApiSettings(
        profile="local", public_origin=f"http://127.0.0.1:{PORT}", secure_cookie=False,
        local_config=fixture["settings"], dsn=fixture["dsn"],
    ))
    with local_api(app):
        assert run_browser("e2e/api/card-recommendations.spec.ts") == 0
    assert product_snapshot(fixture["dsn"]) == before
