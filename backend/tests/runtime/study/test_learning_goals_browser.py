"""真 UI 設目標、選程度、作答，再讀目標進度；模型只用受控替身。"""

from datetime import timedelta
import json
from threading import Event, Thread

import httpx
from sqlalchemy import select

from assessment_fixtures import concept_fixture, model_for
from product_fixtures import closed_loop
from browser_e2e_runner import PORT, local_api, main as run_browser
from learning_adaptation import assessment_sets
from learning_adaptation import learning_goals
from runtime.api import app as api
from runtime.storage.database import database_session
from runtime.storage.tables import AnswerEvent, LearningGoal, StudySession


def test_goal_browser_saves_and_counts_real_answers_without_completing_material(closed_loop, monkeypatch):
    fixture = concept_fixture(closed_loop, 2)
    monkeypatch.setattr(api, "runtime_binding", lambda _: {})
    app = api.create_app(api.ApiSettings(
        profile="local", public_origin=f"http://127.0.0.1:{PORT}", secure_cookie=False,
        local_config=fixture["settings"], dsn=fixture["dsn"],
    ))
    stop, errors, calls = Event(), [], []
    model = model_for(fixture, calls=calls)

    def worker():
        while not stop.wait(0.05):
            try:
                work = assessment_sets.claim_set_work(dsn=fixture["dsn"])
                if work:
                    assessment_sets.execute_set_work(work, dsn=fixture["dsn"], semantic_call=model)
            except Exception as error:
                errors.append(type(error).__name__)
                return

    def no_model_http(*args, **kwargs):
        raise AssertionError("MODEL_HTTP_NOT_ALLOWED")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", no_model_http)
    target_date = learning_goals._now().astimezone(learning_goals.TIMEZONE).date() + timedelta(days=3)
    monkeypatch.setenv("STUDYDY_E2E_LEARNING_GOALS", "true")
    monkeypatch.setenv("STUDYDY_E2E_GOAL_DATA", json.dumps({
        "material": str(fixture["source"].material_id), "run": str(fixture["run"].run_id),
        "revision": fixture["document"]["revision"], "session": str(fixture["study"].study_session_id),
        "concept": fixture["concept"]["concept_id"], "target_date": target_date.isoformat(),
    }))
    thread = Thread(target=worker)
    thread.start()
    try:
        with local_api(app):
            assert run_browser("e2e/api/learning-goals.spec.ts") == 0
    finally:
        stop.set()
        thread.join(timeout=10)
    assert not thread.is_alive() and errors == []
    assert calls == ["assessment", "assessment_check"] * 2
    with database_session(fixture["dsn"]) as db:
        goal = db.get(LearningGoal, fixture["source"].material_id)
        assert goal.target_date == target_date and goal.target_percent == 60
        events = db.scalars(select(AnswerEvent).where(
            AnswerEvent.study_session_id == fixture["study"].study_session_id)).all()
        assert len(events) == 2 and all(event.is_correct for event in events)
        assert db.get(StudySession, fixture["study"].study_session_id).status == "active"
        view = learning_goals.read_goal(db, fixture["learner"].learner_id, fixture["source"].material_id)
        assert view["goal"]["achieved"] and view["goal"]["completed_points"] == 2
