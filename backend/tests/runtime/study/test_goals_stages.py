"""目標進度只用真實重點證據；規劃不建立學習或假造承接進度。"""

from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func, select

import assessment_fixtures
from assessment_fixtures import answer, concept_fixture, create, finish
from product_fixtures import HEADERS, ORIGIN, _app, closed_loop
import test_passed_check_carryover as carryover
from test_version_moves import new_revision
from learning_adaptation.goal_metrics import read_goal_metrics
from learning_adaptation.learner_progress import derive_learner_progress
from runtime.storage.database import database_session
from runtime.storage.tables import AnswerEvent, LearningGoal, StudySession
from runtime.learner_session import register_account
from runtime.version_moves import execute_version_move, preview_version_move


def test_goal_counts_exact_inherited_points_without_creating_a_study(closed_loop, monkeypatch):
    old, _ = carryover.passed_fixture(closed_loop, count=2)
    with database_session(old["dsn"]) as db:
        before = db.scalar(select(func.count()).select_from(StudySession))
        first_answer = db.scalar(select(func.min(AnswerEvent.created_at)).where(
            AnswerEvent.study_session_id == old["study"].study_session_id))
    monkeypatch.setattr(carryover, "create_study_session", lambda *args, **kwargs: None)
    current = carryover.append_revision(old)
    assert current["study"] is None
    with database_session(old["dsn"]) as db:
        metrics = read_goal_metrics(db, old["learner"], old["source"].material_id, current["document"]["revision"])
        assert metrics["assessable_points"] == 4
        assert metrics["completed_points"] == 2
        assert metrics["remaining_points"] == 2
        assert db.scalar(select(func.count()).select_from(StudySession)) == before
        historical = read_goal_metrics(db, old["learner"], old["source"].material_id,
            current["document"]["revision"], through=first_answer - timedelta(microseconds=1))
        assert historical["completed_points"] == 0


def test_carried_concept_completion_does_not_invent_goal_point_evidence(closed_loop):
    old, _ = carryover.passed_fixture(closed_loop, count=2)
    document = new_revision(old, "rewrite")
    material = old["source"].material_id
    preview = preview_version_move(old["learner"], material, dsn=old["dsn"])
    moved = execute_version_move(old["learner"], material, document["revision"], [], True,
        preview["preview_digest"], "goal-move", dsn=old["dsn"])
    progress = derive_learner_progress(old["learner"], moved["study_session_id"], dsn=old["dsn"])
    carried = next(state for state in progress.concept_states if state.label == "Signal codes")
    assert carried.status == "completed" and carried.completed_claim_ids == []
    with database_session(old["dsn"]) as db:
        metrics = read_goal_metrics(db, old["learner"], material, document["revision"])
    concept = next(item for item in metrics["concepts"] if item["concept_id"] == carried.concept_id)
    assert concept["completed_points"] == 0
    assert concept["remaining_points"] == concept["manual_review_points"] == 2
    from learning_adaptation.learning_goals import set_goal
    goal = set_goal(old["learner"], material, date.today(), 100, dsn=old["dsn"])["goal"]
    other = next(c for c in document["concepts"] if c["label"] == "Other topic")
    extra = next(c for c in document["concepts"] if c["label"] == "Extra topic")
    assert goal["today_stage"]["concepts"] == [
        {"concept_id": other["concept_id"], "label": "Other topic", "remaining_points": 1},
        {"concept_id": extra["concept_id"], "label": "Extra topic", "remaining_points": 1},
    ]
    assert next(c for c in goal["manual_review"] if c["concept_id"] == carried.concept_id)["remaining_points"] == 2


def _client(fixture, tmp_path, monkeypatch):
    client = TestClient(_app(fixture["dsn"], tmp_path, monkeypatch), base_url=ORIGIN)
    client.cookies.set("studydy_session", fixture["token"])
    return client


def _path(fixture):
    return f"/v1/materials/{fixture['source'].material_id}/goal"


def _save_goal(client, fixture, target_date, percent):
    response = client.put(_path(fixture), headers=HEADERS, json={
        "schema": "learning-goal-update/v1", "target_date": target_date.isoformat(), "target_percent": percent})
    assert response.status_code == 200, response.json()
    return response.json()


def test_goal_setup_and_preview_do_not_create_study_and_are_owner_scoped(closed_loop, monkeypatch, tmp_path):
    monkeypatch.setattr(assessment_fixtures, "create_study_session", lambda *args, **kwargs: None)
    fixture = concept_fixture(closed_loop, 5)
    client = _client(fixture, tmp_path, monkeypatch)
    initial = client.get(_path(fixture))
    assert initial.status_code == 200 and initial.json()["goal"] is None
    today = date.fromisoformat(initial.json()["today"])
    saved = _save_goal(client, fixture, today + timedelta(days=3), 60)
    goal = saved["goal"]
    assert (goal["assessable_points"], goal["target_points"], goal["completed_points"]) == (6, 4, 0)
    assert goal["today_stage"]["quota_points"] == 1
    assert goal["today_stage"]["scheduled_points"] == 5
    with database_session(fixture["dsn"]) as db:
        row = db.get(LearningGoal, fixture["source"].material_id)
        before = (row.baseline_at, row.daily_date, row.daily_quota_points, row.notice_revision)
        assert db.scalar(select(func.count()).select_from(StudySession)) == 0
    assert client.get(_path(fixture), params={"preview_offset": 7}).status_code == 200
    assert client.get(_path(fixture)).json() == saved
    with database_session(fixture["dsn"]) as db:
        row = db.get(LearningGoal, fixture["source"].material_id)
        assert (row.baseline_at, row.daily_date, row.daily_quota_points, row.notice_revision) == before
        assert db.scalar(select(func.count()).select_from(StudySession)) == 0
    listed = client.get("/v1/learning-goals")
    assert listed.status_code == 200 and len(listed.json()["goals"]) == 1
    foreign = register_account("goals_foreign@example.com", "Synthetic test password 42", dsn=fixture["dsn"])
    client.cookies.set("studydy_session", foreign.raw_token)
    assert client.get(_path(fixture)).status_code == 404
    assert client.put(_path(fixture), headers=HEADERS, json={
        "schema": "learning-goal-update/v1", "target_date": (today + timedelta(days=2)).isoformat(),
        "target_percent": 80}).status_code == 404
    assert client.get("/v1/learning-goals").json()["goals"] == []


def test_new_revision_replans_goal_and_acknowledgement_does_not_reset_it(closed_loop, monkeypatch, tmp_path):
    from learning_adaptation import learning_goals

    fixture = concept_fixture(closed_loop, 2)
    client = _client(fixture, tmp_path, monkeypatch)
    today = date.fromisoformat(client.get(_path(fixture)).json()["today"])
    deadline = today + timedelta(days=4)
    _save_goal(client, fixture, deadline, 80)
    with database_session(fixture["dsn"]) as db:
        original_baseline = db.get(LearningGoal, fixture["source"].material_id).baseline_at
    monkeypatch.setattr(learning_goals, "_now", lambda: original_baseline + timedelta(hours=1))
    _save_goal(client, fixture, deadline, 80)
    with database_session(fixture["dsn"]) as db:
        assert db.get(LearningGoal, fixture["source"].material_id).baseline_at == original_baseline
    current = carryover.append_revision(fixture)
    updated = client.get(_path(fixture))
    assert updated.status_code == 200, updated.json()
    goal = updated.json()["goal"]
    assert goal["target_date"] == deadline.isoformat() and goal["target_percent"] == 80
    assert updated.json()["knowledge_structure_revision"] == current["document"]["revision"]
    assert goal["update_notice"]["previous_points"] == 3
    assert goal["update_notice"]["current_points"] == 4
    with database_session(fixture["dsn"]) as db:
        row = db.get(LearningGoal, fixture["source"].material_id)
        replanned = (row.baseline_at, row.daily_date, row.daily_completed_points, row.daily_quota_points)
        assert row.baseline_at > original_baseline
    stale = client.post(_path(fixture) + "/acknowledge-update", headers=HEADERS, json={
        "schema": "learning-goal-ack/v1", "knowledge_structure_revision": fixture["document"]["revision"]})
    assert stale.status_code == 409 and stale.json()["reason_code"] == "LEARNING_GOAL_STALE"
    acknowledged = client.post(_path(fixture) + "/acknowledge-update", headers=HEADERS, json={
        "schema": "learning-goal-ack/v1", "knowledge_structure_revision": current["document"]["revision"]})
    assert acknowledged.status_code == 200, acknowledged.json()
    assert client.get(_path(fixture)).json()["goal"]["update_notice"] is None
    with database_session(fixture["dsn"]) as db:
        row = db.get(LearningGoal, fixture["source"].material_id)
        assert (row.baseline_at, row.daily_date, row.daily_completed_points, row.daily_quota_points) == replanned


def test_today_quota_finishes_without_moving_goalposts_after_answers(closed_loop, monkeypatch, tmp_path):
    fixture = concept_fixture(closed_loop, 2)
    client = _client(fixture, tmp_path, monkeypatch)
    today = date.fromisoformat(client.get(_path(fixture)).json()["today"])
    before = _save_goal(client, fixture, today + timedelta(days=1), 100)["goal"]
    assert before["today_stage"]["quota_points"] == 2
    group = create(fixture)
    finish(fixture)
    answer(fixture, group)
    after = client.get(_path(fixture)).json()["goal"]
    assert after["completed_points"] == 2 and not after["achieved"]
    assert after["today_stage"]["quota_points"] == 2
    assert after["today_stage"]["completed_points"] == 2
    assert after["today_stage"]["done"] and after["today_stage"]["concepts"] == []
    assert after["future_stages"][0]["date"] == (today + timedelta(days=1)).isoformat()


def test_expired_goal_can_keep_its_date_while_lowering_target(closed_loop, monkeypatch, tmp_path):
    from learning_adaptation import learning_goals

    fixture = concept_fixture(closed_loop, 4)
    group = create(fixture)
    finish(fixture)
    answer(fixture, group, wrong={4})
    client = _client(fixture, tmp_path, monkeypatch)
    today = date.fromisoformat(client.get(_path(fixture)).json()["today"])
    initial = _save_goal(client, fixture, today, 100)
    assert initial["goal"]["completed_points"] == 3 and not initial["goal"]["achieved"]
    with database_session(fixture["dsn"]) as db:
        baseline = db.get(LearningGoal, fixture["source"].material_id).baseline_at
    monkeypatch.setattr(learning_goals, "_now", lambda: baseline + timedelta(days=1))
    _save_goal(client, fixture, today, 100)
    with database_session(fixture["dsn"]) as db:
        assert db.get(LearningGoal, fixture["source"].material_id).baseline_at == baseline
    lowered = _save_goal(client, fixture, today, 60)["goal"]
    assert lowered["target_date"] == today.isoformat()
    assert lowered["target_percent"] == 60 and lowered["achieved"]
    invalid = client.put(_path(fixture), headers=HEADERS, json={
        "schema": "learning-goal-update/v1", "target_date": (today - timedelta(days=1)).isoformat(),
        "target_percent": 60})
    assert invalid.status_code == 400


def test_same_day_replan_preserves_all_today_earnings_and_next_day_is_not_behind(closed_loop, monkeypatch, tmp_path):
    from learning_adaptation import learning_goals

    fixture = concept_fixture(closed_loop, 6)
    client = _client(fixture, tmp_path, monkeypatch)
    today = date.fromisoformat(client.get(_path(fixture)).json()["today"])
    _save_goal(client, fixture, today + timedelta(days=1), 100)
    group = create(fixture)
    finish(fixture)
    answer(fixture, group, wrong={5, 6})
    before = client.get(_path(fixture)).json()["goal"]
    assert before["today_stage"]["quota_points"] == 4
    assert before["today_stage"]["completed_points"] == 4 and before["today_stage"]["done"]
    changed = _save_goal(client, fixture, today + timedelta(days=2), 100)["goal"]
    assert changed["today_stage"]["quota_points"] == 3
    assert changed["today_stage"]["completed_points"] == 4 and changed["today_stage"]["done"]
    assert changed["completed_points"] == 4 and changed["remaining_points"] == 3
    with database_session(fixture["dsn"]) as db:
        rebased_at = db.get(LearningGoal, fixture["source"].material_id).baseline_at
    monkeypatch.setattr(learning_goals, "_now", lambda: rebased_at + timedelta(days=1))
    tomorrow = client.get(_path(fixture)).json()["goal"]
    assert tomorrow["behind_points"] == 0
    assert tomorrow["today_stage"]["quota_points"] == 2
    assert tomorrow["today_stage"]["completed_points"] == 0


def test_revision_update_preserves_today_earnings_before_publication(closed_loop, monkeypatch, tmp_path):
    fixture = concept_fixture(closed_loop, 2)
    client = _client(fixture, tmp_path, monkeypatch)
    today = date.fromisoformat(client.get(_path(fixture)).json()["today"])
    deadline = today + timedelta(days=2)
    _save_goal(client, fixture, deadline, 100)
    group = create(fixture)
    finish(fixture)
    answer(fixture, group)
    current = carryover.append_revision(fixture)
    response = client.get(_path(fixture))
    assert response.status_code == 200, response.json()
    assert response.json()["knowledge_structure_revision"] == current["document"]["revision"]
    goal = response.json()["goal"]
    assert goal["target_date"] == deadline.isoformat() and goal["target_percent"] == 100
    assert goal["assessable_points"] == 4 and goal["completed_points"] == 2
    assert goal["today_stage"]["quota_points"] == 2
    assert goal["today_stage"]["completed_points"] == 2 and goal["today_stage"]["done"]
    assert not goal["achieved"]
