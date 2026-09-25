"""錯題 → 直接補強 → 由作答投影本輪結果；只用受控回應與隔離 DB。"""

import psycopg
import pytest
from fastapi.testclient import TestClient

from learning_adaptation import answer_events, assessment_sets as sets
from learning_adaptation.answer_events import read_answer_events
from learning_adaptation.learner_progress import derive_learner_progress
from runtime.material_discard import request_material_discard
from runtime.storage.database import connect_database
from runtime.storage.tables import Assessment, AssessmentSet, database_session
from assessment_fixtures import (
    answers_for,
    concept_fixture,
    create,
    model_for,
    read,
    answer,
    change,
    finish,
    supplement,
)
from product_fixtures import HEADERS, ORIGIN, _app, closed_loop


def test_remediation_origin_is_immutable_in_database(closed_loop):
    fixture = concept_fixture(closed_loop, 1)
    root = create(fixture)
    finish(fixture)
    answer(fixture, root, wrong={1})
    child = supplement(fixture, root)

    with connect_database(fixture["dsn"]) as connection:
        with pytest.raises(psycopg.Error, match="assessment set origin is immutable"):
            with connection.transaction():
                connection.execute(
                    "UPDATE assessment_sets SET diagnostic_set_id=NULL,kind='diagnostic' WHERE set_id=%s",
                    (child,),
                )
        stored_origin = connection.execute(
            "SELECT diagnostic_set_id FROM assessment_sets WHERE set_id=%s", (child,),
        ).fetchone()
        assert stored_origin == (root,)


def test_remediation_targets_only_remaining_wrong_points(closed_loop):
    fixture = concept_fixture(closed_loop, 4)
    root = create(fixture)
    finish(fixture)
    answer(fixture, root, wrong={2, 4})

    cycle = read(fixture, root)["cycle"]
    assert cycle["pending_count"] == cycle["passed_count"] == 2
    assert cycle["outcome"] == "needs_review"
    assert cycle["can_create_remediation"]
    progress = derive_learner_progress(
        fixture["learner"], fixture["study"].study_session_id, dsn=fixture["dsn"],
    )
    assert progress.next_action.action == "remediate"

    wrong_claims = {
        point["claim_id"] for point in cycle["points"]
        if point["result"] == "needs_review"
    }
    before_events = read_answer_events(
        fixture["learner"], fixture["study"].study_session_id, dsn=fixture["dsn"],
    )
    first_child = supplement(fixture, root)
    assert read_answer_events(
        fixture["learner"], fixture["study"].study_session_id, dsn=fixture["dsn"],
    ) == before_events
    first_group = read(fixture, first_child)
    assert first_group["selection_policy"] == "needs-review-points/v1"
    assert {item["target_claim_id"] for item in first_group["items"]} == wrong_claims
    progress = derive_learner_progress(
        fixture["learner"], fixture["study"].study_session_id, dsn=fixture["dsn"],
    )
    assert progress.next_action.action == "continue_set"
    with pytest.raises(sets.AssessmentSetError, match="CONFLICT"):
        supplement(fixture, root)

    finish(fixture, "first supplement")
    answer(fixture, first_child, wrong={2})
    cycle = read(fixture, root)["cycle"]
    assert cycle["pending_count"] == cycle["remediation_passed_count"] == 1
    assert cycle["can_create_remediation"]
    assert cycle["outcome"] == "needs_review"
    assert sets.claim_set_work(dsn=fixture["dsn"]) is None

    remaining_claims = {
        point["claim_id"] for point in cycle["points"]
        if point["result"] == "needs_review"
    }
    second_child = supplement(fixture, root)
    assert {
        item["target_claim_id"] for item in read(fixture, second_child)["items"]
    } == remaining_claims
    finish(fixture, "second supplement")
    answer(fixture, second_child)

    cycle = read(fixture, root)["cycle"]
    assert cycle["outcome"] == "passed"
    assert cycle["pending_count"] == 0
    assert cycle["passed_count"] == 4
    assert cycle["remediation_passed_count"] == 2
    progress = derive_learner_progress(
        fixture["learner"], fixture["study"].study_session_id, dsn=fixture["dsn"],
    )
    state = next(
        row for row in progress.concept_states
        if row.concept_id == fixture["concept"]["concept_id"]
    )
    assert state.status != "mastered"
    assert state.qualified_correct_items == 2
    assert progress.next_action.action in ("advance", "complete")
    snapshot = read(fixture, root)
    assert read(fixture, root) == snapshot
    assert sets.claim_set_work(dsn=fixture["dsn"]) is None


def test_wrong_again_remains_pending_on_reload_without_automatic_generation(closed_loop):
    fixture = concept_fixture(closed_loop, 1)
    root = create(fixture)
    finish(fixture)
    answer(fixture, root, wrong={1})
    child = supplement(fixture, root)
    finish(fixture, "remediation")
    answer(fixture, child, wrong={1})

    cycle = read(fixture, root)["cycle"]
    assert cycle["outcome"] == "needs_review"
    assert cycle["can_create_remediation"]
    assert cycle["points"][0]["result"] == "needs_review"
    assert read(fixture, root)["cycle"] == cycle
    assert sets.claim_set_work(dsn=fixture["dsn"]) is None
    events = read_answer_events(
        fixture["learner"], fixture["study"].study_session_id, dsn=fixture["dsn"],
    )
    assert len(events) == 2


def test_assisted_correct_does_not_restore_mastery_after_latest_independent_error(closed_loop):
    fixture = concept_fixture(closed_loop, 1)
    for index in range(3):
        group = create(fixture, f"round-{index}")
        finish(fixture, f"round-{index}")
        answer(fixture, group, wrong={1} if index == 2 else set())

    child = supplement(fixture, group)
    finish(fixture, "assisted")
    answer(fixture, child)
    progress = derive_learner_progress(
        fixture["learner"], fixture["study"].study_session_id, dsn=fixture["dsn"],
    )
    state = next(
        row for row in progress.concept_states
        if row.concept_id == fixture["concept"]["concept_id"]
    )
    assert state.status == "learning"
    assert state.qualified_correct_items == 2
    assert not state.mastered_claim_ids
    events = read_answer_events(
        fixture["learner"], fixture["study"].study_session_id, dsn=fixture["dsn"],
    )
    assert events[-1].assisted
    assert events[-1].is_correct
    assert all(not event.assisted for event in events[:-1])


def test_direct_remediation_http_replay_is_scoped_and_get_does_not_create(closed_loop, tmp_path, monkeypatch):
    fixture = concept_fixture(closed_loop, 1)
    root = create(fixture)
    finish(fixture)
    answer(fixture, root, wrong={1})
    client = TestClient(_app(fixture["dsn"], tmp_path, monkeypatch), base_url=ORIGIN)
    client.cookies.set("studydy_session", fixture["token"])

    url = f"/v1/study-sessions/{fixture['study'].study_session_id}/assessment-sets/{root}"
    before = client.get(url).json()
    assert before["cycle"]["active_set_id"] is None
    body = {
        "schema": "assessment-set-action/v1",
        "expected_set_version": before["cycle"]["set_version"],
    }
    headers = {**HEADERS, "Idempotency-Key": "remediation-http"}
    created = client.post(url + "/remediation", headers=headers, json=body)
    assert created.status_code == 202, created.json()

    replay = client.post(url + "/remediation", headers=headers, json=body)
    assert replay.json()["set_id"] == created.json()["set_id"]
    assert replay.json()["diagnostic_set_id"] == str(root)
    assert replay.json()["kind"] == "remediation"
    assert client.get(url).json()["cycle"]["active_set_id"] == created.json()["set_id"]
    duplicate = client.post(
        url + "/remediation",
        headers={**HEADERS, "Idempotency-Key": "duplicate"},
        json=body,
    )
    assert duplicate.status_code == 409
    stale = client.post(
        url + "/remediation", headers=headers,
        json={**body, "expected_set_version": 1},
    )
    assert stale.status_code == 409
    assert "runtime_lock_document" not in replay.json()

    schema = client.app.openapi()
    set_routes = {
        route for route in schema["paths"] if "/assessment-sets/" in route
    }
    assert {route.rsplit("/", 1)[-1] for route in set_routes} == {
        "{set_id}", "submissions", "remediation", "retry", "publish-partial",
    }
    assert created.json()["schema"] == "assessment-set/v1"
    for name, version in (
        ("AssessmentSetView", "assessment-set/v1"),
        ("LearnerProgressView", "learner-progress/v1"),
        ("StudyResumeView", "study-resume/v1"),
    ):
        assert schema["components"]["schemas"][name]["properties"]["schema"]["const"] == version
    for suffix in ("retry", "publish-partial", "remediation", "submissions"):
        path = f"/v1/study-sessions/{{study_session_id}}/assessment-sets/{{set_id}}/{suffix}"
        assert any(
            parameter["name"] == "Idempotency-Key" and parameter["required"]
            for parameter in schema["paths"][path]["post"]["parameters"]
        )


def test_partial_initial_and_failed_remediation_never_turn_unavailable_into_pass_or_wrong(closed_loop):
    fixture = concept_fixture(closed_loop, 3)
    root = create(fixture)
    finish(fixture, fail={2})
    change(fixture, root, "publish-partial")
    answer(fixture, root, wrong={2})
    cycle = read(fixture, root)["cycle"]
    assert cycle["unavailable_count"] == 1
    assert cycle["pending_count"] == 1

    child = supplement(fixture, root)
    version = read(fixture, root)["cycle"]["set_version"]
    finish(fixture, "failed-remediation", fail={1})
    assert read(fixture, root)["cycle"]["set_version"] > version
    assert read(fixture, child)["status"] == "failed"
    events = read_answer_events(
        fixture["learner"], fixture["study"].study_session_id, dsn=fixture["dsn"],
    )
    assert len(events) == 2
    assert read(fixture, root)["cycle"]["pending_count"] == 1

    change(fixture, child, "retry")
    finish(fixture, "retry-remediation")
    answer(fixture, child)
    cycle = read(fixture, root)["cycle"]
    assert cycle["outcome"] == "incomplete"
    assert cycle["passed_count"] == 2
    assert cycle["unavailable_count"] == 1
    progress = derive_learner_progress(
        fixture["learner"], fixture["study"].study_session_id, dsn=fixture["dsn"],
    )
    assert progress.next_action.action in ("advance", "complete")


def test_material_removal_purges_diagnostic_and_remediation_and_fences_late_result(closed_loop):
    fixture = concept_fixture(closed_loop, 1)
    root = create(fixture)
    finish(fixture)
    answer(fixture, root, wrong={1})
    child = supplement(fixture, root)
    work = sets.claim_set_work(dsn=fixture["dsn"])

    assert request_material_discard(
        fixture["learner"].learner_id, fixture["source"].material_id,
        dsn=fixture["dsn"],
    ) == "removed"
    calls = []
    sets.execute_set_work(
        work, dsn=fixture["dsn"], semantic_call=model_for(fixture, calls=calls),
    )
    assert calls == []
    with database_session(fixture["dsn"]) as session:
        assert session.get(AssessmentSet, root) is None
        assert session.get(AssessmentSet, child) is None


def test_unanswered_is_not_wrong_and_incomplete_can_advance(closed_loop):
    fixture = concept_fixture(closed_loop, 2)
    root = create(fixture)
    finish(fixture)
    answers = answers_for(fixture, root)

    # 建立「題組已結束、只作答一題」的狀態，驗證未答不算答錯。
    with database_session(fixture["dsn"]) as session:
        study, _, _ = sets._scope(
            session, fixture["learner"], fixture["study"].study_session_id, lock=True,
        )
        assessment = session.get(Assessment, answers[0]["assessment_revision"])
        answer_events.record_answer(
            session, study, assessment, answers[0]["selected_option_id"], "saved-answer",
        )
        group = session.get(AssessmentSet, root)
        group.status = "completed"
        group.completed_at = sets._now()

    cycle = read(fixture, root)["cycle"]
    assert cycle["outcome"] == "incomplete"
    assert cycle["unanswered_count"] == 1
    assert cycle["pending_count"] == 0
    assert not cycle["can_create_remediation"]
    progress = derive_learner_progress(
        fixture["learner"], fixture["study"].study_session_id, dsn=fixture["dsn"],
    )
    assert progress.next_action.action in ("advance", "complete")
    with pytest.raises(sets.AssessmentSetError, match="CONFLICT"):
        supplement(fixture, root)
