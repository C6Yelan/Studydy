"""開始學習遵守學生指定概念、地圖順序與冪等意圖。"""

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from assessment_fixtures import answer, concept_fixture, create, finish, read
from learning_adaptation.study_sessions import start_learning
from product_fixtures import HEADERS, ORIGIN, _app, closed_loop
from runtime.learner_session import register_account
from runtime.storage.database import database_session
from runtime.storage.tables import AssessmentSet, AssessmentSetItem, StudySession


def _client(fixture, tmp_path, monkeypatch):
    client = TestClient(_app(fixture["dsn"], tmp_path, monkeypatch), base_url=ORIGIN)
    client.cookies.set("studydy_session", fixture["token"])
    return client


def _start(client, fixture, *, key=None):
    return client.post(f"/v1/materials/{fixture['source'].material_id}/study-start",
        headers={**HEADERS, "Idempotency-Key": key or str(uuid4())},
        json={"schema": "study-start/v1", "knowledge_structure_revision": fixture["document"]["revision"]})


def test_start_reuses_same_intent_after_completion(closed_loop, monkeypatch, tmp_path):
    fixture = concept_fixture(closed_loop, 2)
    client = _client(fixture, tmp_path, monkeypatch)
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


def test_start_without_assessable_claims_keeps_empty_receipt_when_claims_return(closed_loop, monkeypatch, tmp_path):
    from copy import deepcopy
    fixture = concept_fixture(closed_loop, 1)
    client = _client(fixture, tmp_path, monkeypatch)
    target = fixture["concept"]["concept_id"]
    with database_session(fixture["dsn"]) as db:
        study = db.get(StudySession, fixture["study"].study_session_id)
        study.no_safe_claim_ids = [claim["claim_id"] for claim in fixture["concept"]["claims"]]
    url = f"/v1/materials/{fixture['source'].material_id}/study-start"
    body = {"schema":"study-start/v1", "knowledge_structure_revision":fixture["document"]["revision"],
            "current_concept_id":target}
    headers = {**HEADERS, "Idempotency-Key":"empty-intent"}
    first = client.post(url, headers=headers, json=body)
    assert first.status_code == 200, first.text
    assert first.json()["selected_set_id"] is None
    with database_session(fixture["dsn"]) as db:
        study = db.get(StudySession, fixture["study"].study_session_id)
        receipts = deepcopy(study.start_receipts)
        assert study.current_concept_id == target
        assert db.scalar(select(func.count()).select_from(AssessmentSet).where(
            AssessmentSet.study_session_id == study.study_session_id)) == 0
        study.no_safe_claim_ids = []
    # 舊的無題組收據仍原樣重播，不因後來已有可檢測重點而改寫同一意圖。
    replay = client.post(url, headers=headers, json=body)
    assert replay.status_code == 200 and replay.json()["selected_set_id"] is None
    with database_session(fixture["dsn"]) as db:
        assert db.get(StudySession, fixture["study"].study_session_id).start_receipts == receipts
    current = client.post(url, headers={**HEADERS,"Idempotency-Key":"new-intent"}, json=body)
    assert current.status_code == 200 and current.json()["selected_set_id"] is not None

def test_start_uses_map_order_and_explicit_concept_over_other_active_set(closed_loop, monkeypatch, tmp_path):
    fixture = concept_fixture(closed_loop, 1)
    client = _client(fixture, tmp_path, monkeypatch)
    url = f"/v1/materials/{fixture['source'].material_id}/study-start"
    revision = fixture["document"]["revision"]
    from runtime.storage import knowledge_reads
    with database_session(fixture["dsn"]) as db:
        plan = knowledge_reads.learning(db, fixture["learner"].learner_id, fixture["source"].material_id, revision)
    first_id, other_id = plan["initial_learning_path"][:2]
    explicit = client.post(url, headers={**HEADERS, "Idempotency-Key": "explicit-concept"},
        json={"schema": "study-start/v1", "knowledge_structure_revision": revision, "current_concept_id": other_id})
    assert explicit.status_code == 200, explicit.text
    normal = _start(client, fixture, key="map-order")
    assert normal.status_code == 200, normal.text
    with database_session(fixture["dsn"]) as db:
        assert db.get(AssessmentSet, UUID(explicit.json()["selected_set_id"])).target_concept_id == other_id
        assert db.get(AssessmentSet, UUID(normal.json()["selected_set_id"])).target_concept_id == first_id
    foreign = register_account("study_foreign@example.com", "Synthetic test password 42", dsn=fixture["dsn"])
    client.cookies.set("studydy_session", foreign.raw_token)
    assert _start(client, fixture).status_code == 404


@pytest.mark.parametrize("failed", [False, True])
def test_normal_launch_preserves_existing_remediation_or_retry_entry(closed_loop, failed):
    fixture = concept_fixture(closed_loop, 1)
    root = create(fixture)
    if failed:
        with database_session(fixture["dsn"]) as db:
            group = db.get(AssessmentSet, root)
            group.status = "failed"
            for item in db.scalars(select(AssessmentSetItem).where(AssessmentSetItem.set_id == root)):
                item.state, item.failure_reason, item.attempts = "failed", "SEMANTIC_SERVICE_UNAVAILABLE", 1
    else:
        finish(fixture)
        answer(fixture, root, wrong={1})
    launched = start_learning(fixture["learner"], fixture["source"].material_id,
        fixture["document"]["revision"], "resume-existing", fixture["settings"],
        current_concept_id=fixture["concept"]["concept_id"], dsn=fixture["dsn"])
    assert launched["selected_set_id"] == root
    with database_session(fixture["dsn"]) as db:
        assert len(db.scalars(select(AssessmentSet).where(
            AssessmentSet.study_session_id == fixture["study"].study_session_id)).all()) == 1
    assert read(fixture, root)["status"] == ("failed" if failed else "completed")
