"""整組交卷的原子保存、重播、範圍及舊作答保護。"""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from learning_adaptation import answer_events, assessment_sets as sets
from runtime.storage.tables import Assessment, AnswerEvent, StudySession, database_session
from product_fixtures import HEADERS, ORIGIN, _app, closed_loop
from assessment_fixtures import concept_fixture, create, finish_set, read, answers_for, send


def test_whole_set_submission_publishes_all_feedback_and_replays_once(closed_loop, tmp_path, monkeypatch):
    fixture = concept_fixture(closed_loop, 3)
    set_id = create(fixture)
    finish_set(fixture)
    client = TestClient(_app(fixture["dsn"], tmp_path, monkeypatch), base_url=ORIGIN)
    client.cookies.set("studydy_session", fixture["token"])
    study_session_id = fixture["study"].study_session_id
    base = f"/v1/study-sessions/{study_session_id}/assessment-sets/{set_id}"
    group = read(fixture, set_id)
    answers = answers_for(fixture, set_id)

    first_answer = answers[0]
    rejected = client.post(
        f"/v1/study-sessions/{study_session_id}/assessments/"
        f"{first_answer['assessment_revision']}/submissions",
        headers={**HEADERS, "Idempotency-Key": "individual"},
        json={
            "schema": "answer-submission-create/v1",
            "question_id": first_answer["question_id"],
            "selected_option_id": first_answer["selected_option_id"],
        },
    )
    assert rejected.status_code == 404
    assert read(fixture, set_id)["answered_count"] == 0
    assert all(item["feedback"] is None for item in client.get(base).json()["items"])

    body = {
        "schema": "assessment-set-submission/v1",
        "expected_set_version": group["set_version"],
        "answers": answers,
    }
    headers = {**HEADERS, "Idempotency-Key": "whole-set"}
    response = client.post(base + "/submissions", headers=headers, json=body)
    assert response.status_code == 200, response.json()
    completed = response.json()
    assert completed["status"] == "completed"
    assert completed["answered_count"] == 3
    assert completed["cycle"]["outcome"] == "passed"
    assert all(
        item["feedback"] is not None and not item["can_submit"]
        for item in completed["items"]
    )

    replay = client.post(
        base + "/submissions", headers=headers,
        json={**body, "answers": list(reversed(answers))},
    )
    assert replay.json() == completed
    events = answer_events.read_answer_events(
        fixture["learner"], study_session_id, dsn=fixture["dsn"],
    )
    assert len(events) == 3
    other_intent = client.post(
        base + "/submissions",
        headers={**HEADERS, "Idempotency-Key": "different-intent"},
        json=body,
    )
    assert other_intent.status_code == 409
    assert client.get(base).json() == completed


@pytest.mark.parametrize(
    "corrupt", ["missing", "duplicate", "wrong-question", "wrong-option", "foreign-assessment"],
)
def test_invalid_member_rejects_every_answer_without_partial_scoring(closed_loop, corrupt):
    fixture = concept_fixture(closed_loop, 3)
    set_id = create(fixture)
    finish_set(fixture)
    before = read(fixture, set_id)
    answers = answers_for(fixture, set_id)

    if corrupt == "missing":
        answers.pop()
    elif corrupt == "duplicate":
        answers[-1] = deepcopy(answers[0])
    elif corrupt == "wrong-question":
        answers[-1]["question_id"] = answers[0]["question_id"]
    elif corrupt == "wrong-option":
        answers[-1]["selected_option_id"] = "option:sha256:" + "f" * 64
    elif corrupt == "foreign-assessment":
        answers[-1]["assessment_revision"] = "assessment:sha256:" + "f" * 64

    with pytest.raises(sets.AssessmentSetError, match="REQUEST_INVALID"):
        send(fixture, set_id, answers)
    assert read(fixture, set_id) == before
    assert answer_events.read_answer_events(
        fixture["learner"], fixture["study"].study_session_id, dsn=fixture["dsn"],
    ) == ()


def test_mid_write_failure_rolls_back_all_answers_and_versions(closed_loop, monkeypatch):
    fixture = concept_fixture(closed_loop, 3)
    set_id = create(fixture)
    finish_set(fixture)
    before = read(fixture, set_id)
    original_record_answer = answer_events.record_answer
    call_count = 0

    def fail(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("SIMULATED_STORAGE_FAILURE")
        return original_record_answer(*args, **kwargs)

    monkeypatch.setattr(answer_events, "record_answer", fail)
    with pytest.raises(RuntimeError, match="SIMULATED_STORAGE_FAILURE"):
        send(fixture, set_id, answers_for(fixture, set_id))
    assert call_count == 2
    assert read(fixture, set_id) == before
    with database_session(fixture["dsn"]) as session:
        study = session.get(StudySession, fixture["study"].study_session_id)
        assert study.last_event_number == 0
        events = list(session.scalars(select(AnswerEvent).where(
            AnswerEvent.study_session_id == fixture["study"].study_session_id,
        )))
        assert events == []


@pytest.mark.parametrize("same_key", [True, False])
def test_concurrent_set_submissions_cannot_double_score(closed_loop, same_key):
    fixture = concept_fixture(closed_loop, 3)
    set_id = create(fixture)
    finish_set(fixture)
    answers = answers_for(fixture, set_id)
    version = read(fixture, set_id)["set_version"]
    barrier = Barrier(2)

    def submit(index):
        barrier.wait(timeout=5)
        key = "one-intent" if same_key else f"intent-{index}"
        try:
            send(fixture, set_id, answers, key=key, version=version)
            return "saved"
        except sets.AssessmentSetError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    assert results.count("saved") == (2 if same_key else 1)
    assert read(fixture, set_id)["answered_count"] == 3
    events = answer_events.read_answer_events(
        fixture["learner"], fixture["study"].study_session_id, dsn=fixture["dsn"],
    )
    assert [event.event_number for event in events] == [1, 2, 3]


def test_previously_saved_individual_answer_is_preserved_when_finishing_set(closed_loop):
    fixture = concept_fixture(closed_loop, 3)
    set_id = create(fixture)
    finish_set(fixture)
    answers = answers_for(fixture, set_id)

    # 建立切換前已保存一題的 fixture，公開單題 API 現在禁止新增題組答案。
    with database_session(fixture["dsn"]) as session:
        study, _, _ = sets._scope(
            session, fixture["learner"], fixture["study"].study_session_id, lock=True,
        )
        assessment = session.get(Assessment, answers[0]["assessment_revision"])
        saved = answer_events.record_answer(
            session, study, assessment, answers[0]["selected_option_id"],
            "pre-existing-answer",
        )

    conflicting_answers = deepcopy(answers)
    public = read(fixture, set_id)["items"][0]["assessment"]
    conflicting_answers[0]["selected_option_id"] = next(
        option["option_id"] for option in public["options"]
        if option["option_id"] != answers[0]["selected_option_id"]
    )
    with pytest.raises(sets.AssessmentSetError, match="CONFLICT"):
        send(fixture, set_id, conflicting_answers)
    assert read(fixture, set_id)["answered_count"] == 1

    send(fixture, set_id, answers)
    result = read(fixture, set_id)
    assert result["answered_count"] == 3
    assert result["items"][0]["feedback"]["answer_event_id"] == saved.event.answer_event_id
    assert result["status"] == "completed"
