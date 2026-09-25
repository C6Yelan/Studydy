from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from learning_adaptation.study_sessions import StudySessionError, create_study_session
from runtime.storage.materials import read_material_library
from runtime.storage.tables import StudySession, database_session
from assessment_fixtures import answer, concept_fixture, create, finish_set, learning_records
from product_fixtures import HEADERS, ORIGIN, _app, closed_loop, library_materials, product_snapshot


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
    fixture = library_materials
    first = create_study_session(
        fixture["learner"], fixture["first"].material_id,
        fixture["structure"]["revision"], "one", dsn=fixture["dsn"],
    )
    repeated = create_study_session(
        fixture["learner"], fixture["first"].material_id,
        fixture["structure"]["revision"], "same", dsn=fixture["dsn"],
    )
    second = create_study_session(
        fixture["learner"], fixture["first"].material_id,
        fixture["second_structure"]["revision"], "two", dsn=fixture["dsn"],
    )
    assert repeated.study_session_id == first.study_session_id
    assert second.study_session_id != first.study_session_id
    links = read_material_library(
        fixture["learner"].learner_id,
        material_id=fixture["first"].material_id,
        dsn=fixture["dsn"],
    )[0]["study_sessions"]
    assert {link["study_session_id"] for link in links} == {
        first.study_session_id, second.study_session_id,
    }


def test_resume_reads_current_sets_without_single_question_projection(learning_records, tmp_path, monkeypatch):
    fixture = learning_records
    client = TestClient(_app(fixture["dsn"], tmp_path, monkeypatch), base_url=ORIGIN)
    client.cookies.set("studydy_session", fixture["token"])
    path = (
        f"/v1/materials/{fixture['first'].material_id}"
        f"/knowledge-structures/{fixture['structure']['revision']}"
        f"/study-sessions/{fixture['active'].study_session_id}/resume"
    )
    before = product_snapshot(fixture["dsn"])
    response = client.get(path, params={
        "run_id": fixture["structure"]["run_id"],
        "set_id": str(fixture["set_id"]),
    })
    assert response.status_code == 200
    view = response.json()
    assert view["schema"] == "study-resume/v1"
    assert view["selected_set_id"] == str(fixture["set_id"])
    assert not {"assessments", "selected_assessment_revision"} & view.keys()
    assert product_snapshot(fixture["dsn"]) == before

    retired = client.get(path, params={
        "run_id": fixture["structure"]["run_id"],
        "assessment_revision": "retired",
    })
    assert retired.status_code == 400
    client.cookies.set("studydy_session", fixture["foreign"].raw_token)
    assert client.get(path, params={"run_id": fixture["structure"]["run_id"]}).status_code == 404
