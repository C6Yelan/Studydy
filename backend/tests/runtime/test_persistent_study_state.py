from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func

from learning_adaptation.study_sessions import create_study_session, complete_study_session, set_current_study_concept, StudySessionError
from runtime.storage.tables import StudySession, database_session
from runtime.storage.materials import read_material_library
from test_closed_loop_v1 import closed_loop
from test_material_library import library_materials
from test_accounts import _app, ORIGIN, HEADERS


def test_ensure_initial_idempotency_completed_and_focus_api(closed_loop, tmp_path, monkeypatch):
    learner, source, _, structure, dsn, token = closed_loop
    concept = structure["concepts"][0]["concept_id"]
    def ensure(key, selected=concept):
        return create_study_session(learner, source.material_id, structure["revision"], key, current_concept_id=selected, dsn=dsn)
    first = ensure("initial")
    assert ensure("initial") == first
    with pytest.raises(StudySessionError, match="IDEMPOTENCY_CONFLICT"):
        ensure("initial", None)
    assert ensure("another") == first
    assert ensure("another", None) == first
    client = TestClient(_app(dsn, tmp_path, monkeypatch), base_url=ORIGIN)
    client.cookies.set("studydy_session", token)
    operation = client.app.openapi()["paths"]["/v1/study-sessions/{study_session_id}/focus"]["post"]
    assert operation["operationId"] == "focusStudySession"
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("StudySessionView")
    path = f"/v1/study-sessions/{first.study_session_id}/focus"
    body = {"schema": "study-session-focus/v1", "current_concept_id": concept}
    for _ in range(2):
        response = client.post(path, headers=HEADERS, json=body)
        assert response.status_code == 200
        assert response.json()["study_session_id"] == str(first.study_session_id)
    assert client.post(path, headers=HEADERS, json={**body, "current_concept_id": "unknown"}).status_code >= 400
    complete_study_session(learner, first.study_session_id, dsn=dsn)
    assert ensure("after-complete").status == "completed"
    assert ensure("after-complete").study_session_id == first.study_session_id
    assert client.post(path, headers=HEADERS, json=body).status_code >= 400
    client.cookies.clear()
    assert client.post(path, headers=HEADERS, json=body).status_code == 401
    with database_session(dsn) as db:
        assert db.scalar(select(func.count()).select_from(StudySession)) == 1


def test_concurrent_ensure_serializes_creation(closed_loop):
    learner, source, _, structure, dsn, _ = closed_loop
    barrier = Barrier(2)
    def ensure(key):
        barrier.wait()
        return create_study_session(learner, source.material_id, structure["revision"], key, dsn=dsn)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(ensure, ["one", "two"]))
    assert results[0].study_session_id == results[1].study_session_id
    with database_session(dsn) as db:
        assert db.scalar(select(func.count()).select_from(StudySession)) == 1


def test_legacy_canonical_and_cross_revision(library_materials):
    f = library_materials
    learner, source, dsn = f["learner"], f["first"], f["dsn"]
    first = create_study_session(learner, source.material_id, f["structure"]["revision"], "one", dsn=dsn)
    timestamp = datetime.now(UTC)
    ids = sorted([uuid4(), uuid4()])
    with database_session(dsn) as db:
        original = db.get(StudySession, first.study_session_id)
        original.status = "completed"; original.completed_at = timestamp
        original.started_at = timestamp + timedelta(days=1)  # A newer completion must not beat active/no_safe.
        values = {column.name: getattr(original, column.name) for column in StudySession.__table__.columns}
        for index, identity in enumerate(ids):
            db.add(StudySession(**{**values, "study_session_id": identity, "status": "no_safe" if index else "active", "started_at": timestamp,
                "completed_at": None, "idempotency_key_sha256": sha256(str(identity).encode()).digest()}))
    chosen = create_study_session(learner, source.material_id, f["structure"]["revision"], "ensure-legacy", dsn=dsn)
    assert chosen.study_session_id == ids[-1]
    other = create_study_session(learner, source.material_id, f["second_structure"]["revision"], "second-revision", dsn=dsn)
    assert other.study_session_id != chosen.study_session_id
    links = read_material_library(learner.learner_id, material_id=source.material_id, dsn=dsn)[0]["study_sessions"]
    assert {link["study_session_id"] for link in links} == {chosen.study_session_id, other.study_session_id}
    with database_session(dsn) as db:
        assert db.scalar(select(func.count()).select_from(StudySession)) == 4
