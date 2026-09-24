from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func

from learning_adaptation.study_sessions import create_study_session, set_current_study_concept, StudySessionError
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
    with database_session(dsn) as db:
        # guidance 完成流程另有測試；這裡只建立 completed 狀態來驗證 ensure。
        stored = db.get(StudySession, first.study_session_id)
        stored.status = "completed"
        stored.completed_at = datetime.now(UTC)
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



def test_current_revisions_keep_distinct_persistent_states(library_materials):
    f=library_materials
    first=create_study_session(f['learner'],f['first'].material_id,f['structure']['revision'],'one',dsn=f['dsn'])
    repeated=create_study_session(f['learner'],f['first'].material_id,f['structure']['revision'],'same',dsn=f['dsn'])
    second=create_study_session(f['learner'],f['first'].material_id,f['second_structure']['revision'],'two',dsn=f['dsn'])
    assert repeated.study_session_id==first.study_session_id
    assert second.study_session_id!=first.study_session_id
    links=read_material_library(f['learner'].learner_id,material_id=f['first'].material_id,dsn=f['dsn'])[0]['study_sessions']
    assert {link['study_session_id'] for link in links}=={first.study_session_id,second.study_session_id}
