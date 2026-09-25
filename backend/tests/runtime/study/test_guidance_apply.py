"""只套用既有 next_action；不增加學習路徑或評分策略。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from learning_adaptation import assessment_sets as sets
from learning_adaptation.learner_progress import LearnerProgressError, apply_guidance, derive_learner_progress
from learning_adaptation.study_sessions import read_study_session
from runtime.learner_session import TrustedLearner
from product_fixtures import HEADERS, ORIGIN, _app, closed_loop
from assessment_fixtures import create_other, other_model, answer, finish, concept_fixture, create, read


def progress(fixture):
    return derive_learner_progress(
        fixture["learner"], fixture["study"].study_session_id, dsn=fixture["dsn"],
    )


def apply(fixture, revision):
    return apply_guidance(
        fixture["learner"], fixture["study"].study_session_id, revision, dsn=fixture["dsn"],
    )


def test_apply_advance_complete_and_replay_preserves_session_and_answers(closed_loop, tmp_path, monkeypatch):
    fixture = concept_fixture(closed_loop, 1)
    root = create(fixture)
    finish(fixture)
    answer(fixture, root)
    before = progress(fixture)
    assert before.next_action.action == "advance"

    client = TestClient(_app(fixture["dsn"], tmp_path, monkeypatch), base_url=ORIGIN)
    client.cookies.set("studydy_session", fixture["token"])
    study_session_id = fixture["study"].study_session_id
    url = f"/v1/study-sessions/{study_session_id}/guidance/apply"
    body = {"schema": "guidance-apply/v1", "guidance_revision": before.guidance_revision}
    stale = client.post(
        url, headers=HEADERS,
        json={**body, "guidance_revision": "learner-guidance:sha256:" + "f" * 64},
    )
    assert stale.status_code == 409
    assert stale.json()["reason_code"] == "LEARNER_GUIDANCE_STALE"

    result = client.post(url, headers=HEADERS, json=body)
    assert result.status_code == 200, result.json()
    assert result.json()["current_concept_id"] == before.next_action.target_concept_id
    assert client.post(url, headers=HEADERS, json=body).json() == result.json()
    assert read(fixture, root)["answered_count"] == 1

    second = create_other(fixture)
    while work := sets.claim_set_work(dsn=fixture["dsn"]):
        sets.execute_set_work(work, dsn=fixture["dsn"], semantic_call=other_model)
    answer(fixture, second)
    final = progress(fixture)
    assert final.next_action.action == "complete"
    assert apply(fixture, final.guidance_revision).study_session_id == study_session_id
    stored = read_study_session(fixture["learner"], study_session_id, dsn=fixture["dsn"])
    assert stored.status == "completed"
    assert stored.completed_at is not None
    apply(fixture, final.guidance_revision)
    assert read_study_session(fixture["learner"], study_session_id, dsn=fixture["dsn"]) == stored
    invalid = client.post(
        url, headers=HEADERS,
        json={**body, "current_concept_id": before.current_concept_id},
    )
    assert invalid.status_code == 400


def test_active_and_stale_guidance_never_change_focus(closed_loop):
    fixture = concept_fixture(closed_loop, 1)
    root = create(fixture)
    finish(fixture)
    answer(fixture, root)
    before = progress(fixture)

    active = create(fixture, "another-activity")
    with pytest.raises(LearnerProgressError, match="STALE"):
        apply(fixture, before.guidance_revision)
    current = progress(fixture)
    assert current.next_action.action == "continue_set"
    with pytest.raises(LearnerProgressError, match="STALE"):
        apply(fixture, current.guidance_revision)
    assert read(fixture, active)["status"] == "preparing"
    assert progress(fixture).current_concept_id == before.current_concept_id
    with pytest.raises(LearnerProgressError):
        apply_guidance(
            TrustedLearner(uuid4()), fixture["study"].study_session_id,
            current.guidance_revision, dsn=fixture["dsn"],
        )


def test_concurrent_same_revision_advances_only_once(closed_loop):
    fixture = concept_fixture(closed_loop, 1)
    root = create(fixture)
    finish(fixture)
    answer(fixture, root)
    before = progress(fixture)
    barrier = Barrier(2)

    def run(_):
        barrier.wait(timeout=10)
        return apply(fixture, before.guidance_revision)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, range(2)))
    assert all(
        result.current_concept_id == before.next_action.target_concept_id
        for result in results
    )
    study = read_study_session(
        fixture["learner"], fixture["study"].study_session_id, dsn=fixture["dsn"],
    )
    assert study.status == "active"
