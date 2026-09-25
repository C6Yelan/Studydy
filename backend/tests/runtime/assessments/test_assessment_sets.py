"""單一觀念、多個重點的真 PostgreSQL／API 題組；模型使用受控回應。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from product_fixtures import HEADERS, ORIGIN, _app, closed_loop
from assessment_fixtures import concept_fixture, create, finish_set, model_for, read
from learning_adaptation import assessment_sets as sets
from learning_adaptation.answer_events import read_answer_events
from runtime.learner_session import TrustedLearner
from runtime.material_discard import request_material_discard
from runtime.storage.tables import Assessment, AssessmentSet, AssessmentSetItem, StudySession, database_session


# 保留單題及多題的動態範圍；三題完整流程另由 API／整組交卷案例驗證。
@pytest.mark.parametrize("count", [1, 7])
def test_dynamic_set_covers_multiple_points_of_one_concept_and_read_never_generates(closed_loop, count):
    fixture = concept_fixture(closed_loop, count)
    plan = sets.read_plan(
        fixture["learner"], fixture["study"].study_session_id,
        fixture["concept"]["concept_id"], dsn=fixture["dsn"],
    )
    assert plan["requested_count"] == count

    set_id = create(fixture)
    assert create(fixture) == set_id
    preparing = read(fixture, set_id)
    assert preparing["requested_count"] == count
    assert all(item["assessment"] is None for item in preparing["items"])

    calls = []
    finish_set(fixture, calls=calls)
    result = read(fixture, set_id)
    assert result["status"] == "ready"
    assert result["published_count"] == count
    assert len(calls) == 2 * count
    assert {
        item["assessment"]["target_concept_id"] for item in result["items"]
    } == {fixture["concept"]["concept_id"]}
    assert len({item["target_claim_id"] for item in result["items"]}) == count
    before = deepcopy(result)
    assert read(fixture, set_id) == before
    assert len(calls) == 2 * count
    for item in result["items"]:
        assert "correct_option_id" not in item["assessment"]
        assert "generation_provenance" not in item["assessment"]
        assert item["can_submit"]
        assert item["feedback"] is None


def test_partial_publish_keeps_verified_questions_and_failure_is_not_wrong(closed_loop):
    fixture = concept_fixture(closed_loop)
    set_id = create(fixture)
    calls = []
    finish_set(fixture, fail={1}, calls=calls)
    result = read(fixture, set_id)
    assert result["status"] == "partial_ready"
    assert result["verified_count"] == 2
    assert result["published_count"] == 0
    assert all(item["assessment"] is None for item in result["items"])

    def publish_partial():
        sets.change_set(
            fixture["learner"], fixture["study"].study_session_id, set_id,
            "publish-partial", result["set_version"], "partial", dsn=fixture["dsn"],
        )

    publish_partial()
    ready = read(fixture, set_id)
    assert ready["published_count"] == 2
    assert ready["requested_count"] == 3
    assert sum(item["state"] == "omitted" for item in ready["items"]) == 1
    assert read_answer_events(
        fixture["learner"], fixture["study"].study_session_id, dsn=fixture["dsn"],
    ) == ()
    publish_partial()
    assert read(fixture, set_id) == ready
    assert sets.claim_set_work(dsn=fixture["dsn"]) is None



def test_expired_lease_and_late_result_do_not_publish_or_retry_implicitly(closed_loop):
    fixture = concept_fixture(closed_loop, 1)
    set_id = create(fixture)
    work = sets.claim_set_work(dsn=fixture["dsn"])
    with database_session(fixture["dsn"]) as session:
        group = session.get(AssessmentSet, set_id)
        group.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)

    assert sets.claim_set_work(dsn=fixture["dsn"]) is None
    assert read(fixture, set_id)["status"] == "failed"
    calls = []
    sets.execute_set_work(
        work, dsn=fixture["dsn"], semantic_call=model_for(fixture, calls=calls),
    )
    assert calls == []
    assert read(fixture, set_id)["published_count"] == 0

    result = read(fixture, set_id)
    sets.change_set(
        fixture["learner"], fixture["study"].study_session_id, set_id,
        "retry", result["set_version"], "retry", dsn=fixture["dsn"],
    )
    finish_set(fixture)
    assert read(fixture, set_id)["status"] == "ready"


def test_api_preserves_scope_private_preparation_and_read_only_resume(closed_loop, tmp_path, monkeypatch):
    fixture = concept_fixture(closed_loop)
    client = TestClient(_app(fixture["dsn"], tmp_path, monkeypatch), base_url=ORIGIN)
    client.cookies.set("studydy_session", fixture["token"])
    study_session_id = fixture["study"].study_session_id
    base = f"/v1/study-sessions/{study_session_id}/assessment-sets"
    create_body = {
        "schema": "assessment-set-create/v1",
        "target_concept_id": fixture["concept"]["concept_id"],
    }

    created = client.post(
        base, headers={**HEADERS, "Idempotency-Key": "http-round"}, json=create_body,
    )
    assert created.status_code == 202, created.json()
    set_id = created.json()["set_id"]
    assert created.json()["requested_count"] == 3

    work = sets.claim_set_work(dsn=fixture["dsn"])
    calls = []
    sets.execute_set_work(
        work, dsn=fixture["dsn"], semantic_call=model_for(fixture, calls=calls),
    )
    with database_session(fixture["dsn"]) as session:
        item = session.get(AssessmentSetItem, (work.set_id, work.ordinal))
        hidden_revision = item.prepared_document["public"]["assessment_revision"]
    result = client.get(f"{base}/{set_id}")
    assert result.status_code == 200
    assert result.json()["verified_count"] == 1
    assert all(item["assessment"] is None for item in result.json()["items"])
    assert client.get(
        f"/v1/study-sessions/{study_session_id}/assessments/{hidden_revision}"
    ).status_code == 404

    resume_route = (
        f"/v1/materials/{fixture['source'].material_id}"
        f"/knowledge-structures/{fixture['document']['revision']}"
        f"/study-sessions/{study_session_id}/resume"
    )
    restored = client.get(
        resume_route, params={"run_id": str(fixture["run"].run_id), "set_id": set_id},
    )
    assert restored.status_code == 200, restored.json()
    assert restored.json()["schema"] == "study-resume/v1"
    assert restored.json()["selected_set_id"] == set_id
    assert "assessments" not in restored.json()
    assert len(calls) == 2

    conflict = client.post(
        base, headers={**HEADERS, "Idempotency-Key": "another-round"}, json=create_body,
    )
    assert conflict.status_code == 409
    fixed_count = client.post(
        base, headers={**HEADERS, "Idempotency-Key": "fixed-count"},
        json={**create_body, "requested_count": 5},
    )
    assert fixed_count.status_code == 400

    finish_set(fixture)
    ready = client.get(f"{base}/{set_id}").json()
    assert ready["published_count"] == 3
    assert not {"runtime_lock_document", "target_plan", "action_receipts"} & ready.keys()
    for item in ready["items"]:
        assert not {"prepared_document", "correct_option_id", "generation_provenance"} & item.keys()
    with pytest.raises(sets.AssessmentSetError, match="NOT_FOUND"):
        sets.read_set(
            TrustedLearner(uuid4()), study_session_id, work.set_id, dsn=fixture["dsn"],
        )


def test_ended_study_automatically_cancels_pending_generation(closed_loop):
    fixture = concept_fixture(closed_loop, 1)
    set_id = create(fixture)
    with database_session(fixture["dsn"]) as session:
        study = session.get(StudySession, fixture["study"].study_session_id)
        study.status = "completed"
        study.completed_at = sets._now()

    assert sets.claim_set_work(dsn=fixture["dsn"]) is None
    result = read(fixture, set_id)
    assert result["status"] == "cancelled"
    assert result["published_count"] == 0
    assert read_answer_events(
        fixture["learner"], fixture["study"].study_session_id, dsn=fixture["dsn"],
    ) == ()


def test_material_delete_cancels_set_and_late_model_result_cannot_recreate_records(closed_loop):
    fixture = concept_fixture(closed_loop, 1)
    set_id = create(fixture)
    work = sets.claim_set_work(dsn=fixture["dsn"])
    model = model_for(fixture)

    def discard(client, **kwargs):
        response = model(client, **kwargs)
        assert request_material_discard(
            fixture["learner"].learner_id, fixture["source"].material_id,
            dsn=fixture["dsn"],
        ) == "removed"
        return response

    sets.execute_set_work(work, dsn=fixture["dsn"], semantic_call=discard)
    with database_session(fixture["dsn"]) as session:
        assert session.get(AssessmentSet, set_id) is None
        assert session.get(StudySession, fixture["study"].study_session_id) is None
    assert sets.claim_set_work(dsn=fixture["dsn"]) is None


@pytest.mark.parametrize("committed", [False, True])
def test_uncertain_commit_reuses_verified_result_without_another_model_call(closed_loop, monkeypatch, committed):
    fixture = concept_fixture(closed_loop, 1)
    set_id = create(fixture)
    work = sets.claim_set_work(dsn=fixture["dsn"])
    commit = sets._commit_prepared
    attempt_count = 0

    def uncertain(*args, **kwargs):
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count == 1:
            if committed:
                commit(*args, **kwargs)
            raise RuntimeError("Synthetic commit response interruption")
        return commit(*args, **kwargs)

    monkeypatch.setattr(sets, "_commit_prepared", uncertain)
    calls = []
    sets.execute_set_work(
        work, dsn=fixture["dsn"], semantic_call=model_for(fixture, calls=calls),
    )
    result = read(fixture, set_id)
    assert result["status"] == "ready"
    assert result["published_count"] == 1
    assert calls == ["assessment", "assessment_check"]
    assert result["items"][0]["attempts"] == 1
    with database_session(fixture["dsn"]) as session:
        assessments = list(session.scalars(select(Assessment).where(
            Assessment.study_session_id == fixture["study"].study_session_id,
        )))
        assert len(assessments) == 1


def test_concurrent_creation_replays_same_intent_and_rejects_another_active_set_for_same_concept(closed_loop):
    fixture = concept_fixture(closed_loop, 1)
    barrier = Barrier(2)

    def concurrent():
        barrier.wait(timeout=5)
        return create(fixture)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: concurrent(), range(2)))
    assert first == second
    with pytest.raises(sets.AssessmentSetError, match="ACTIVE"):
        create(fixture, "another-intent")
    with database_session(fixture["dsn"]) as session:
        groups = list(session.scalars(select(AssessmentSet).where(
            AssessmentSet.study_session_id == fixture["study"].study_session_id,
        )))
        assert len(groups) == 1


def test_retry_only_failed_point_and_published_scope_is_immutable(closed_loop):
    fixture = concept_fixture(closed_loop)
    set_id = create(fixture)
    finish_set(fixture, fail={1})
    with database_session(fixture["dsn"]) as session:
        preserved = {
            item.ordinal: deepcopy(item.prepared_document)
            for item in session.scalars(select(AssessmentSetItem).where(
                AssessmentSetItem.set_id == set_id,
            ))
            if item.state == "verified"
        }

    before = read(fixture, set_id)
    sets.change_set(
        fixture["learner"], fixture["study"].study_session_id, set_id,
        "retry", before["set_version"], "retry-failed", dsn=fixture["dsn"],
    )
    calls = []
    finish_set(fixture, calls=calls)
    ready = read(fixture, set_id)
    assert calls == ["assessment", "assessment_check"]
    assert ready["published_count"] == 3
    for item in ready["items"]:
        if item["ordinal"] in preserved:
            assert item["assessment"] == preserved[item["ordinal"]]["public"]
            assert item["attempts"] == 1
        else:
            assert item["attempts"] == 2

    with pytest.raises(DBAPIError, match="immutable"):
        with database_session(fixture["dsn"]) as session:
            session.get(AssessmentSetItem, (set_id, 1)).target_claim_id = "replacement"
    with pytest.raises(DBAPIError, match="immutable"):
        with database_session(fixture["dsn"]) as session:
            session.get(AssessmentSet, set_id).requested_count = 99
    assert read(fixture, set_id) == ready


def test_heading_classification_does_not_exclude_a_grounded_definition(closed_loop):
    fixture = concept_fixture(
        closed_loop, 1,
        facts=["The integer at position i is stored in list[i]."],
        evidence_kind="heading",
    )
    assert all(row["kind"] == "heading" for row in fixture["document"]["evidence"])
    plan = sets.read_plan(
        fixture["learner"], fixture["study"].study_session_id,
        fixture["concept"]["concept_id"], dsn=fixture["dsn"],
    )
    assert plan["requested_count"] == 1
    assert plan["excluded"] == []


def test_heartbeat_retries_storage_failure_but_stops_on_stale_work(monkeypatch):
    group = SimpleNamespace(lease_expires_at=None)
    calls = []

    class Stop:
        def wait(self, _):
            return len(calls) >= 3

    def leased(*_):
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError("synthetic storage failure")
        if len(calls) == 3:
            raise sets.AssessmentSetError("ASSESSMENT_SET_STALE_WORK")
        return None, None, None, group

    monkeypatch.setattr(sets, "database_session", lambda _: nullcontext(None))
    monkeypatch.setattr(sets, "_leased", leased)
    sets._heartbeat(None, Stop(), None)
    assert len(calls) == 3
    assert group.lease_expires_at is not None
