"""起始程度的公開邊界、跨概念診斷與原有作答完成規則。"""

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from assessment_fixtures import answer, concept_fixture, create, finish, model_for, other_model, read
from learning_adaptation import assessment_sets
from learning_adaptation.learner_progress import derive_learner_progress
from product_fixtures import HEADERS, ORIGIN, _app, closed_loop
from runtime.learner_session import register_account
from runtime.storage.database import database_session
from runtime.storage.tables import AssessmentSet, StudySession


def _client(fixture, tmp_path, monkeypatch):
    client = TestClient(_app(fixture["dsn"], tmp_path, monkeypatch), base_url=ORIGIN)
    client.cookies.set("studydy_session", fixture["token"])
    return client


def _preference(client, fixture, level):
    response = client.put(f"/v1/materials/{fixture['source'].material_id}/starting-level",
        headers=HEADERS, json={"schema": "starting-level-update/v1", "starting_level": level})
    assert response.status_code == 200, response.json()
    return response.json()


def _start(client, fixture, *, key=None):
    return client.post(f"/v1/materials/{fixture['source'].material_id}/study-start",
        headers={**HEADERS, "Idempotency-Key": key or str(uuid4())},
        json={"schema": "study-start/v1", "knowledge_structure_revision": fixture["document"]["revision"]})


def _finish_cross(fixture):
    signals = model_for(fixture)
    def model(client, **kwargs):
        request = kwargs["request"]
        other = (request["claim"]["text"].startswith("Other topic")
                 if kwargs["task"] == "assessment" else
                 "other topic" in request["questions"][0]["prompt"])
        return (other_model if other else signals)(client, **kwargs)
    while work := assessment_sets.claim_set_work(dsn=fixture["dsn"]):
        assessment_sets.execute_set_work(work, dsn=fixture["dsn"], semantic_call=model)


def test_first_start_requires_explicit_choice_and_preferences_are_owner_scoped(closed_loop, monkeypatch, tmp_path):
    learner, source, settings, document, dsn, token = closed_loop
    fixture = {"learner": learner, "source": source, "settings": settings,
               "document": document, "dsn": dsn, "token": token}
    client = _client(fixture, tmp_path, monkeypatch)
    path = f"/v1/materials/{source.material_id}/starting-level"
    preference = client.get(path)
    assert preference.status_code == 200
    assert preference.json()["starting_level"] is None
    required = _start(client, fixture)
    assert required.status_code == 409, required.json()
    assert required.json()["reason_code"] == "STARTING_LEVEL_REQUIRED"
    with database_session(dsn) as db:
        assert db.scalar(select(func.count()).select_from(StudySession)) == 0
    invalid = client.put(path, headers=HEADERS,
        json={"schema": "starting-level-update/v1", "starting_level": "automatic"})
    assert invalid.status_code == 400
    missing_origin = client.put(path, json={
        "schema": "starting-level-update/v1", "starting_level": "beginner"})
    assert missing_origin.status_code == 403
    assert _preference(client, fixture, "beginner")["starting_level"] == "beginner"
    launched = _start(client, fixture)
    assert launched.status_code == 200, launched.json()
    assert launched.json()["starting_level"] == "beginner"
    assert launched.json()["selected_set_id"] is None
    foreign = register_account("starting_foreign@example.com", "Synthetic test password 42", dsn=dsn)
    client.cookies.set("studydy_session", foreign.raw_token)
    assert client.get(path).status_code == 404
    assert client.put(path, headers=HEADERS, json={
        "schema": "starting-level-update/v1", "starting_level": "familiar"}).status_code == 404


def test_changing_level_preserves_active_set_and_answer_progress(closed_loop, monkeypatch, tmp_path):
    fixture = concept_fixture(closed_loop, 2)
    group = create(fixture)
    finish(fixture)
    client = _client(fixture, tmp_path, monkeypatch)
    _preference(client, fixture, "familiar")
    launched = _start(client, fixture)
    assert launched.status_code == 200, launched.json()
    assert launched.json()["selected_set_id"] == str(group)
    with database_session(fixture["dsn"]) as db:
        assert db.get(StudySession, fixture["study"].study_session_id).last_event_number == 0
        assert db.get(AssessmentSet, group).status == "ready"
        assert db.scalar(select(func.count()).select_from(AssessmentSet).where(
            AssessmentSet.study_session_id == fixture["study"].study_session_id)) == 1
    answer(fixture, group, wrong={1})
    _preference(client, fixture, "gaps")
    with database_session(fixture["dsn"]) as db:
        assert db.get(StudySession, fixture["study"].study_session_id).last_event_number == 2
        assert db.get(AssessmentSet, group).status == "completed"
    launched = _start(client, fixture)
    assert launched.status_code == 200, launched.json()
    with database_session(fixture["dsn"]) as db:
        assert db.scalar(select(func.count()).select_from(AssessmentSet).where(
            AssessmentSet.study_session_id == fixture["study"].study_session_id,
            AssessmentSet.kind == "material_diagnostic")) == 0


def test_some_knowledge_starts_a_concept_set_and_reuses_same_intent(closed_loop, monkeypatch, tmp_path):
    fixture = concept_fixture(closed_loop, 2)
    client = _client(fixture, tmp_path, monkeypatch)
    _preference(client, fixture, "some_knowledge")
    first = _start(client, fixture, key="start-intent")
    second = _start(client, fixture, key="start-intent")
    assert first.status_code == second.status_code == 200, (first.json(), second.json())
    assert first.json()["selected_set_id"] == second.json()["selected_set_id"]
    finish(fixture)
    answer(fixture, UUID(first.json()["selected_set_id"]))
    replay = _start(client, fixture, key="start-intent")
    assert replay.status_code == 200, replay.json()
    assert replay.json()["selected_set_id"] == first.json()["selected_set_id"]
    with database_session(fixture["dsn"]) as db:
        group = db.scalar(select(AssessmentSet).where(
            AssessmentSet.study_session_id == fixture["study"].study_session_id))
        assert group.kind == "diagnostic"
        assert group.requested_count == 2
        assert db.scalar(select(func.count()).select_from(AssessmentSet).where(
            AssessmentSet.study_session_id == fixture["study"].study_session_id)) == 1


@pytest.mark.parametrize("level", ["familiar", "gaps"])
def test_cross_diagnostic_grades_each_concept_and_leaves_untested_points(closed_loop, monkeypatch, tmp_path, level):
    fixture = concept_fixture(closed_loop, 2)
    client = _client(fixture, tmp_path, monkeypatch)
    _preference(client, fixture, level)
    launched = _start(client, fixture)
    assert launched.status_code == 200, launched.json()
    identity = UUID(launched.json()["selected_set_id"])
    _finish_cross(fixture)
    group = read(fixture, identity)
    assert group["kind"] == "material_diagnostic"
    assert group["target_concept_id"] is None and group["cycle"] is None
    assert len(group["items"]) == 2
    workspace = client.get(
        f"/v1/materials/{fixture['source'].material_id}/knowledge-structures/{fixture['document']['revision']}"
        f"/study-sessions/{fixture['study'].study_session_id}/workspace",
        params={"run_id": fixture["document"]["run_id"], "set_id": str(identity)},
    )
    assert workspace.status_code == 200, workspace.json()
    body = workspace.json()
    assert body["starting_level"] == level and body["selected_set"]["cycle"] is None
    members = {item["target_concept_id"] for item in body["selected_set"]["items"]}
    assert members <= {concept["concept_id"] for concept in body["context"]["concepts"]}
    assert all("correct_option_id" not in item["assessment"] for item in body["selected_set"]["items"])
    other_id = next(concept["concept_id"] for concept in fixture["document"]["concepts"]
                    if concept["concept_id"] != fixture["concept"]["concept_id"])
    wrong = next(item["ordinal"] for item in group["items"] if item["target_concept_id"] == other_id)
    answer(fixture, identity, wrong={wrong})
    progress = derive_learner_progress(fixture["learner"], fixture["study"].study_session_id, dsn=fixture["dsn"])
    partial = next(state for state in progress.concept_states if state.concept_id == fixture["concept"]["concept_id"])
    assert len(partial.completed_claim_ids) == 1 and partial.status != "completed"
    route = client.get(f"/v1/study-sessions/{fixture['study'].study_session_id}/map-progress")
    assert route.status_code == 200, route.json()
    assert route.json()["learning_path"].index(other_id) < route.json()["learning_path"].index(partial.concept_id)
    next_start = _start(client, fixture)
    assert next_start.status_code == 200, next_start.json()
    with database_session(fixture["dsn"]) as db:
        next_group = db.get(AssessmentSet, UUID(next_start.json()["selected_set_id"]))
        assert next_group.target_concept_id == other_id
        assert db.scalar(select(func.count()).select_from(AssessmentSet).where(
            AssessmentSet.study_session_id == fixture["study"].study_session_id,
            AssessmentSet.kind == "material_diagnostic")) == 1
    remaining = create(fixture, key="finish-remaining-points")
    with database_session(fixture["dsn"]) as db:
        assert db.get(AssessmentSet, remaining).requested_count == 1


def test_cross_diagnostic_can_finish_study_only_when_all_points_are_completed(closed_loop, monkeypatch, tmp_path):
    fixture = concept_fixture(closed_loop, 1)
    client = _client(fixture, tmp_path, monkeypatch)
    _preference(client, fixture, "familiar")
    launched = _start(client, fixture)
    assert launched.status_code == 200, launched.json()
    identity = UUID(launched.json()["selected_set_id"])
    _finish_cross(fixture)
    answer(fixture, identity)
    sid = fixture["study"].study_session_id
    progress = derive_learner_progress(fixture["learner"], sid, dsn=fixture["dsn"])
    assert all(state.status == "completed" for state in progress.concept_states)
    assert progress.next_action.action == "complete"
    with database_session(fixture["dsn"]) as db:
        assert db.get(StudySession, sid).status == "active"
    completed = client.post(f"/v1/study-sessions/{sid}/guidance/apply", headers=HEADERS,
        json={"schema": "guidance-apply/v1", "guidance_revision": progress.guidance_revision})
    assert completed.status_code == 200, completed.json()
    with database_session(fixture["dsn"]) as db:
        study = db.get(StudySession, sid)
        assert study.status == "completed" and study.completed_at is not None
