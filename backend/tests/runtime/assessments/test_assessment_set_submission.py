"""整組交卷的原子保存、重播、範圍及舊作答保護。"""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from learning_adaptation import assessment_state
from learning_adaptation import answer_events, assessment_sets as sets
from runtime.storage.tables import Assessment, AnswerEvent, StudySession
from runtime.storage.database import database_session
from product_fixtures import HEADERS, ORIGIN, _app, closed_loop, read_answer_events
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
    events = read_answer_events(
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

    with pytest.raises(assessment_state.AssessmentSetError, match="REQUEST_INVALID"):
        send(fixture, set_id, answers)
    assert read(fixture, set_id) == before
    assert read_answer_events(
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
        except assessment_state.AssessmentSetError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    assert results.count("saved") == (2 if same_key else 1)
    assert read(fixture, set_id)["answered_count"] == 3
    events = read_answer_events(
        fixture["learner"], fixture["study"].study_session_id, dsn=fixture["dsn"],
    )
    assert [event.event_number for event in events] == [1, 2, 3]


def test_previously_saved_individual_answer_is_preserved_when_finishing_set(closed_loop):
    fixture = concept_fixture(closed_loop, 3)
    set_id = create(fixture)
    finish_set(fixture)
    answers = answers_for(fixture, set_id)

    # 模擬已有部分答案的題組，驗證單題 API 不得新增題組答案。
    with database_session(fixture["dsn"]) as session:
        study, _, _ = assessment_state._scope(
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
    with pytest.raises(assessment_state.AssessmentSetError, match="CONFLICT"):
        send(fixture, set_id, conflicting_answers)
    assert read(fixture, set_id)["answered_count"] == 1

    send(fixture, set_id, answers)
    result = read(fixture, set_id)
    assert result["answered_count"] == 3
    assert result["items"][0]["feedback"]["answer_event_id"] == saved.event.answer_event_id
    assert result["status"] == "completed"


@pytest.mark.parametrize("count,wrong,answer_xp,concept_xp", [(4, True, 33, 0), (3, False, 30, 30)])
def test_submission_awards_sources_and_keeps_them_for_replay_and_get(
    closed_loop, tmp_path, monkeypatch, count, wrong, answer_xp, concept_xp,
):
    fixture = concept_fixture(closed_loop, count)
    identity = create(fixture)
    finish_set(fixture)
    group = read(fixture, identity)
    answers = answers_for(fixture, identity)
    if wrong:
        public = group["items"][-1]["assessment"]
        answers[-1]["selected_option_id"] = next(
            option["option_id"] for option in public["options"]
            if option["option_id"] != answers[-1]["selected_option_id"]
        )
    client = TestClient(_app(fixture["dsn"], tmp_path, monkeypatch), base_url=ORIGIN)
    client.cookies.set("studydy_session", fixture["token"])
    base = f'/v1/study-sessions/{fixture["study"].study_session_id}/assessment-sets/{identity}'
    assert client.get(base).json()["xp_award"] is None
    body = {"schema": "assessment-set-submission/v1", "expected_set_version": group["set_version"], "answers": answers}
    headers = {**HEADERS, "Idempotency-Key": "xp-submit"}
    response = client.post(base + "/submissions", headers=headers, json=body)
    assert response.status_code == 200, response.json()
    result = response.json()
    assert result["xp_award"] == {
        "answers": answer_xp, "concepts": concept_xp, "cards": 0, "podcast": 0, "goals": 0, "difficulties": 0,
    }
    from backend.tests.runtime.study.test_learning_rewards import ready_cards
    from runtime.card_reviews import submit_review

    card_set, concepts = ready_cards(fixture)
    submit_review(fixture["learner"].learner_id, card_set, concepts[0], "remembered", 0, "later-card", dsn=fixture["dsn"])
    # 分項是交卷當下的收據，後來的卡片活動不能改變重送或 GET 的結果。
    assert client.post(base + "/submissions", headers=headers, json=body).json() == result
    assert client.get(base).json() == result
    rhythm = client.get("/v1/learning-activity/rhythm", params={"period": "week"})
    assert rhythm.status_code == 200, rhythm.json()
    activity = client.get("/v1/learning-activity").json()
    today = next(day for day in rhythm.json()["days"] if day["date"] == activity["today"])
    assert today["xp"] == activity["xp"]["today"] == answer_xp + concept_xp + 2


def test_repeated_question_awards_no_answer_xp(closed_loop, tmp_path, monkeypatch):
    from sqlalchemy import text
    from assessment_fixtures import answer, finish

    fixture = concept_fixture(closed_loop, 3)
    first = create(fixture)
    finish(fixture)
    answer(fixture, first, wrong={1, 2, 3})
    identity = create(fixture, key="repeat-round")
    finish(fixture, variant="repeat-round")
    # 合成歷史重做資料：先前答錯的題目與這輪使用相同的題目身分。
    # 目前題組與新作答仍由真實交卷流程驗證及寫入。
    with database_session(fixture["dsn"]) as session:
        session.execute(text("""
            UPDATE answer_events e SET semantic_identity = a.semantic_identity
            FROM assessment_set_items i JOIN assessments a ON a.assessment_revision = i.assessment_revision
            WHERE i.set_id = :set_id AND e.study_session_id = :sid
              AND e.target_claim_id = i.target_claim_id
        """), {"set_id": identity, "sid": fixture["study"].study_session_id})
    send(fixture, identity, answers_for(fixture, identity))
    client = TestClient(_app(fixture["dsn"], tmp_path, monkeypatch), base_url=ORIGIN)
    client.cookies.set("studydy_session", fixture["token"])
    result = client.get(f'/v1/study-sessions/{fixture["study"].study_session_id}/assessment-sets/{identity}').json()
    assert result["status"] == "completed"
    assert result["xp_award"]["answers"] == 0
    with database_session(fixture["dsn"]) as session:
        assert session.scalar(text("SELECT count(*) FROM answer_events")) == 6


@pytest.mark.parametrize("fail_at", [1, 2])
def test_xp_database_error_does_not_rollback_submission(closed_loop, tmp_path, monkeypatch, fail_at):
    from sqlalchemy import text
    from runtime import learning_activity as activity

    fixture = concept_fixture(closed_loop, 3)
    identity = create(fixture)
    finish_set(fixture)
    original = activity.read_xp_records
    calls = 0

    def fail(session, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == fail_at:
            # 真 SQL 錯誤會讓交易失效；必須由 XP savepoint 隔離。
            session.execute(text("SELECT * FROM xp_award_failure_injection"))
        return original(session, *args, **kwargs)

    monkeypatch.setattr(activity, "read_xp_records", fail)
    send(fixture, identity, answers_for(fixture, identity))
    client = TestClient(_app(fixture["dsn"], tmp_path, monkeypatch), base_url=ORIGIN)
    client.cookies.set("studydy_session", fixture["token"])
    response = client.get(f'/v1/study-sessions/{fixture["study"].study_session_id}/assessment-sets/{identity}')
    assert response.status_code == 200, response.json()
    assert response.json()["xp_award"] is None
    assert response.json()["status"] == "completed"
    assert response.json()["answered_count"] == 3


def test_fourth_material_stage_awards_no_xp_after_daily_cap(closed_loop, tmp_path, monkeypatch):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from learning_adaptation.learning_goals import set_goal

    for index in range(4):
        fixture = concept_fixture(closed_loop, 3, label=f"Stage {index}")
        today = datetime.now(ZoneInfo("Asia/Taipei")).date()
        goal = set_goal(fixture["learner"], fixture["source"].material_id, today + timedelta(days=30), 100, dsn=fixture["dsn"])
        assert goal["goal"]["today_stage"]["quota_points"] == 1
        identity = create(fixture)
        finish_set(fixture)
        group = read(fixture, identity)
        answers = answers_for(fixture, identity)
        # 只完成一個重點，跨過每日配額，保留未達成的最終目標。
        for answer, item in zip(answers[1:], group["items"][1:]):
            answer["selected_option_id"] = next(
                option["option_id"] for option in item["assessment"]["options"]
                if option["option_id"] != answer["selected_option_id"]
            )
        client = TestClient(_app(fixture["dsn"], tmp_path, monkeypatch), base_url=ORIGIN)
        client.cookies.set("studydy_session", fixture["token"])
        base = f'/v1/study-sessions/{fixture["study"].study_session_id}/assessment-sets/{identity}'
        response = client.post(base + "/submissions", headers={**HEADERS, "Idempotency-Key": f"stage-{index}"}, json={
            "schema": "assessment-set-submission/v1", "expected_set_version": group["set_version"], "answers": answers,
        })
        assert response.status_code == 200, response.json()
        assert response.json()["xp_award"]["goals"] == (15 if index < 3 else 0)
    rhythm = client.get("/v1/learning-activity/rhythm", params={"period": "week"})
    assert rhythm.status_code == 200, rhythm.json()
    assert client.get("/v1/learning-activity").json()["xp"]["sources"]["goals"] == 45


def test_submission_crossing_midnight_awards_answers_and_concept(closed_loop, tmp_path, monkeypatch):
    from datetime import datetime, time, timedelta
    from zoneinfo import ZoneInfo

    fixture = concept_fixture(closed_loop, 3)
    identity = create(fixture)
    finish_set(fixture)
    answers = answers_for(fixture, identity)
    zone = ZoneInfo("Asia/Taipei")
    before_time = datetime.combine(datetime.now(zone).date(), time(23, 59, 59), zone)
    after_time = before_time + timedelta(seconds=2)
    first_tick = iter([before_time])
    monkeypatch.setattr(sets, "_now", lambda: next(first_tick, after_time))

    class AnswerClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return after_time.astimezone(tz)

    monkeypatch.setattr(answer_events, "datetime", AnswerClock)
    send(fixture, identity, answers)
    client = TestClient(_app(fixture["dsn"], tmp_path, monkeypatch), base_url=ORIGIN)
    client.cookies.set("studydy_session", fixture["token"])
    response = client.get(f'/v1/study-sessions/{fixture["study"].study_session_id}/assessment-sets/{identity}')
    assert response.status_code == 200, response.json()
    assert response.json()["xp_award"] == {
        "answers": 30, "concepts": 30, "cards": 0, "podcast": 0, "goals": 0, "difficulties": 0,
    }
