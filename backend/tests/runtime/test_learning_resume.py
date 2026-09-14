from __future__ import annotations

from fastapi.testclient import TestClient
import psycopg
import pytest

from learning_adaptation.answer_events import submit_answer
from learning_adaptation.study_sessions import create_study_session, complete_study_session
from test_closed_loop_v1 import closed_loop, Client, generate_assessment, _assessment_response
from test_material_library import library_materials, product_snapshot
from test_accounts import _app, ORIGIN, HEADERS


@pytest.fixture
def learning_records(library_materials):
    fixture = library_materials
    learner, source, dsn = fixture["learner"], fixture["first"], fixture["dsn"]
    structure = fixture["structure"]
    concept = structure["concepts"][0]

    def question(study, key, prompt):
        return generate_assessment(
            learner, study.study_session_id, concept["claims"][0]["claim_id"], key, fixture["settings"],
            dsn=dsn, client=Client(), semantic_call=lambda *_args, **_kwargs: _assessment_response(key, prompt, concept["evidence_refs"][0]),
        )

    completed = create_study_session(learner, source.material_id, structure["revision"], "completed-study", dsn=dsn)
    old_question = question(completed, "completed-question", "已完成紀錄：Stack 使用哪種順序？")
    old_answer = submit_answer(learner, completed.study_session_id, old_question.assessment_revision,
        old_question.question_id, old_question.private_answer_document["correct_option_id"], "old-answer", dsn=dsn)
    complete_study_session(learner, completed.study_session_id, dsn=dsn)

    no_safe = create_study_session(learner, source.material_id, fixture["second_structure"]["revision"], "no-safe-study", dsn=dsn)
    second_concept = fixture["second_structure"]["concepts"][0]
    with psycopg.connect(dsn) as connection:
        connection.execute("UPDATE study_sessions SET status='no_safe', no_safe_claim_ids=%s, deferred_concept_ids=%s WHERE study_session_id=%s",
            ([claim["claim_id"] for claim in second_concept["claims"]], [second_concept["concept_id"]], no_safe.study_session_id))

    # Preserve a pre-existing legacy duplicate to exercise read-only resume and canonical projection.
    from datetime import UTC, datetime
    from hashlib import sha256
    from uuid import uuid4
    from runtime.storage.tables import StudySession, database_session
    from learning_adaptation.study_sessions import read_study_session
    active_id = uuid4()
    with database_session(dsn) as db:
        row = db.get(StudySession, completed.study_session_id)
        values = {column.name: getattr(row, column.name) for column in StudySession.__table__.columns}
        values.update(study_session_id=active_id, idempotency_key_sha256=sha256(b"legacy-active").digest(),
            status="active", started_at=datetime.now(UTC), completed_at=None, last_event_number=0)
        db.add(StudySession(**values))
    active = read_study_session(learner, active_id, dsn=dsn)
    answered = question(active, "previous-question", "先前題目：Stack 的資料順序是什麼？")
    previous_answer = submit_answer(learner, active.study_session_id, answered.assessment_revision,
        answered.question_id, answered.private_answer_document["correct_option_id"], "previous-answer", dsn=dsn)
    unanswered = question(active, "saved-question", "保存的未答題：Stack 如何取出資料？")
    return {**fixture, "active": active, "completed": completed, "no_safe": no_safe,
        "old_question": old_question, "old_answer": old_answer, "answered": answered,
        "previous_answer": previous_answer, "unanswered": unanswered}


def resume_path(fixture, study=None, *, revision=None, material_id=None):
    study = study or fixture["active"]
    return (f"/v1/materials/{material_id or study.material_id}/knowledge-structures/"
            f"{revision or study.knowledge_structure_revision}/study-sessions/{study.study_session_id}/resume")


def test_resume_is_read_only_and_returns_saved_question_feedback_and_states(learning_records, tmp_path, monkeypatch):
    fixture = learning_records
    client = TestClient(_app(fixture["dsn"], tmp_path, monkeypatch), base_url=ORIGIN)
    client.cookies.set("studydy_session", fixture["token"])
    before = product_snapshot(fixture["dsn"])
    params = {"run_id": fixture["structure"]["run_id"]}
    restored = client.get(resume_path(fixture), params=params)
    assert restored.status_code == 200 and restored.headers["cache-control"] == "private, no-store"
    body = restored.json()
    assert body["session"]["study_session_id"] == str(fixture["active"].study_session_id)
    assert body["session"]["knowledge_structure_revision"] == fixture["structure"]["revision"]
    assert body["session"]["current_concept_id"] == fixture["active"].current_concept_id
    assert body["selected_assessment_revision"] == fixture["unanswered"].assessment_revision
    assert body["assessments"][0]["assessment"] == fixture["unanswered"].public_document
    assert body["assessments"][0]["feedback"] is None and body["assessments"][0]["can_submit"]
    assert body["assessments"][1]["feedback"]["answer_event_id"] == str(fixture["previous_answer"].event.answer_event_id)
    assert body["progress"] == client.get(f"/v1/study-sessions/{fixture['active'].study_session_id}/progress").json()
    assert all(f'"{name}"' not in restored.text for name in ("correct_option_id", "private_answer_document", "generation_provenance", "correct_answer"))
    selected = client.get(resume_path(fixture), params={**params, "assessment_revision": fixture["answered"].assessment_revision}).json()
    assert selected["selected_assessment_revision"] == fixture["answered"].assessment_revision
    completed = client.get(resume_path(fixture, fixture["completed"]), params=params).json()
    assert completed["session"]["status"] == "completed"
    assert completed["assessments"][0]["feedback"]["answer_event_id"] == str(fixture["old_answer"].event.answer_event_id)
    assert not completed["assessments"][0]["can_submit"]
    no_safe = client.get(resume_path(fixture, fixture["no_safe"]), params={"run_id": fixture["second_structure"]["run_id"]}).json()
    assert no_safe["session"]["status"] == "no_safe"
    assert no_safe["session"]["no_safe_claim_ids"] and no_safe["session"]["deferred_concept_ids"]
    assert no_safe["progress"]["deferred_concept_ids"] == no_safe["session"]["deferred_concept_ids"]
    assert no_safe["progress"]["next_action"]["action"] == "no_safe"
    library = client.get("/v1/materials").json()["materials"]
    links = next(item["study_sessions"] for item in library if item["material_id"] == str(fixture["first"].material_id))
    assert len(links) == 2 and links[0]["study_session_id"] == str(fixture["active"].study_session_id)
    assert links[0]["knowledge_structure_revision"] == fixture["structure"]["revision"]
    assert product_snapshot(fixture["dsn"]) == before


def test_bound_resume_refuses_owner_material_revision_run_and_question_mismatch(learning_records, tmp_path, monkeypatch):
    fixture = learning_records
    client = TestClient(_app(fixture["dsn"], tmp_path, monkeypatch), base_url=ORIGIN)
    params = {"run_id": fixture["structure"]["run_id"]}
    assert client.get(resume_path(fixture), params=params).status_code == 401
    client.cookies.set("studydy_session", fixture["foreign"].raw_token)
    assert client.get(resume_path(fixture), params=params).status_code == 404
    client.cookies.clear()
    client.cookies.set("studydy_session", fixture["token"])
    before = product_snapshot(fixture["dsn"])
    assert client.get(resume_path(fixture, material_id=fixture["uploaded"].material_id), params=params).status_code == 404
    assert client.get(resume_path(fixture, revision=fixture["second_structure"]["revision"]), params=params).status_code == 404
    assert client.get(resume_path(fixture), params={"run_id": fixture["second_structure"]["run_id"]}).status_code == 404
    assert client.get(resume_path(fixture), params={**params, "assessment_revision": fixture["old_question"].assessment_revision}).status_code == 404
    assert client.get(resume_path(fixture), params={**params, "learner_id": str(fixture["foreign"].learner_id)}).status_code == 400
    assert product_snapshot(fixture["dsn"]) == before
    template = "/v1/materials/{material_id}/knowledge-structures/{structure_revision}/study-sessions/{study_session_id}/resume"
    operation = client.app.openapi()["paths"][template]["get"]
    assert "409" in operation["responses"] and operation["security"]
    assert any(parameter["name"] == "run_id" and parameter["required"] for parameter in operation["parameters"])


def test_committed_answer_can_be_read_after_lost_response_and_replayed_once(learning_records, tmp_path, monkeypatch):
    fixture = learning_records
    app = _app(fixture["dsn"], tmp_path, monkeypatch)
    client = TestClient(app, base_url=ORIGIN)
    client.cookies.set("studydy_session", fixture["token"])
    assessment = fixture["unanswered"]
    path = f"/v1/study-sessions/{fixture['active'].study_session_id}/assessments/{assessment.assessment_revision}/submissions"
    headers = {**HEADERS, "Idempotency-Key": "lost-response-answer"}
    answer = {"schema": "answer-submission-create/v2", "question_id": assessment.question_id,
        "selected_option_id": assessment.private_answer_document["correct_option_id"]}
    sent = client.post(path, headers=headers, json=answer)
    assert sent.status_code == 201
    saved = sent.json()
    after_submit = product_snapshot(fixture["dsn"])
    client.delete("/v1/session", headers=HEADERS)
    fresh = TestClient(app, base_url=ORIGIN)
    assert fresh.post("/v1/session/login", headers=HEADERS,
        json={"email": "learner_test@example.com", "password": "Synthetic test password 42"}).status_code == 200
    restored = fresh.get(resume_path(fixture), params={"run_id": fixture["structure"]["run_id"]}).json()
    assert restored["assessments"][0]["feedback"] == saved
    assert fresh.post(path, headers=headers, json=answer).json() == saved
    different = next(option["option_id"] for option in assessment.public_document["options"] if option["option_id"] != answer["selected_option_id"])
    assert fresh.post(path, headers=headers, json={**answer, "selected_option_id": different}).status_code == 409
    assert fresh.post(path, headers={**HEADERS, "Idempotency-Key": "new-key"}, json=answer).status_code == 409
    assert product_snapshot(fixture["dsn"]) == after_submit


def test_resume_detects_concurrent_state_change_without_applying_guidance(learning_records, tmp_path, monkeypatch):
    import runtime.api.app as api_app
    fixture = learning_records
    app = _app(fixture["dsn"], tmp_path, monkeypatch)
    original = api_app.derive_learner_progress
    def concurrently_completed(*args, **kwargs):
        progress = original(*args, **kwargs)
        complete_study_session(fixture["learner"], fixture["active"].study_session_id, dsn=fixture["dsn"])
        return progress
    monkeypatch.setattr(api_app, "derive_learner_progress", concurrently_completed)
    client = TestClient(app, base_url=ORIGIN)
    client.cookies.set("studydy_session", fixture["token"])
    response = client.get(resume_path(fixture), params={"run_id": fixture["structure"]["run_id"]})
    assert response.status_code == 409 and response.json()["reason_code"] == "IDEMPOTENCY_CONFLICT"


def test_completed_pending_question_is_read_only_and_cannot_score(learning_records, tmp_path, monkeypatch):
    fixture = learning_records
    complete_study_session(fixture["learner"], fixture["active"].study_session_id, dsn=fixture["dsn"])
    client = TestClient(_app(fixture["dsn"], tmp_path, monkeypatch), base_url=ORIGIN)
    client.cookies.set("studydy_session", fixture["token"])
    before = product_snapshot(fixture["dsn"])
    response = client.get(resume_path(fixture), params={"run_id": fixture["structure"]["run_id"]})
    assert response.status_code == 200
    record = response.json()["assessments"][0]
    assert record["feedback"] is None and record["can_submit"] is False
    question = fixture["unanswered"]
    stale = client.post(f"/v1/study-sessions/{fixture['active'].study_session_id}/assessments/{question.assessment_revision}/submissions",
        headers={**HEADERS, "Idempotency-Key": "stale-unanswered"},
        json={"schema": "answer-submission-create/v2", "question_id": question.question_id,
              "selected_option_id": question.private_answer_document["correct_option_id"]})
    assert stale.status_code == 409 and stale.json()["reason_code"] == "IDEMPOTENCY_CONFLICT"
    assert product_snapshot(fixture["dsn"]) == before
