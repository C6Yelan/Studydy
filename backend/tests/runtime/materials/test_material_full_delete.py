"""完整刪除教材後，其他教材與已保存學習資料仍保持正確。"""

from contextlib import contextmanager
from hashlib import sha256

import psycopg
import pytest
from fastapi.testclient import TestClient

import runtime.material_discard as discard
from runtime.storage.artifacts import _root
from runtime.storage.tables import database_session
from product_fixtures import HEADERS, ORIGIN, _app, library_materials, product_snapshot, closed_loop
from assessment_fixtures import learning_records


def test_full_learned_material_delete_preserves_every_other_material(learning_records, tmp_path, monkeypatch):
    fixture = learning_records
    material_id = fixture["first"].material_id
    artifact_id = fixture["first"].artifact_id
    with psycopg.connect(fixture["dsn"]) as db:
        study_ids = [row[0] for row in db.execute(
            "SELECT study_session_id FROM study_sessions WHERE material_id=%s", (material_id,),
        )]
        assessment_ids = [row[0] for row in db.execute(
            "SELECT assessment_revision FROM assessments WHERE study_session_id=ANY(%s)",
            (study_ids,),
        )]
        assert assessment_ids
        assert db.execute(
            "SELECT count(*) FROM answer_events WHERE material_id=%s", (material_id,),
        ).fetchone()[0] > 0

    def others():
        snapshot = {}
        with psycopg.connect(fixture["dsn"]) as db:
            for table in (
                "materials", "artifacts", "material_processing_runs", "knowledge_structures",
                "study_sessions", "answer_events", "assessments",
            ):
                if table == "assessments":
                    condition, value = "NOT (study_session_id=ANY(%s))", study_ids
                else:
                    condition, value = "material_id<>%s", material_id
                rows = db.execute(
                    f"SELECT row_to_json(t)::text FROM {table} t WHERE {condition} ORDER BY 1",
                    (value,),
                ).fetchall()
                snapshot[table] = sha256(repr(rows).encode()).hexdigest()
        return snapshot

    before = others()
    client = TestClient(_app(fixture["dsn"], tmp_path, monkeypatch), base_url=ORIGIN)
    client.cookies.set("studydy_session", fixture["foreign"].raw_token)
    assert client.delete(f"/v1/materials/{material_id}", headers=HEADERS).status_code == 404
    assert others() == before

    client.cookies.set("studydy_session", fixture["token"])
    response = client.delete(f"/v1/materials/{material_id}", headers=HEADERS)
    assert response.status_code == 202
    assert response.json()["state"] == "removed"
    with psycopg.connect(fixture["dsn"]) as db:
        for table in (
            "materials", "artifacts", "material_processing_runs", "knowledge_structures",
            "study_sessions", "answer_events",
        ):
            assert db.execute(
                f"SELECT count(*) FROM {table} WHERE material_id=%s", (material_id,),
            ).fetchone() == (0,)
        assert db.execute(
            "SELECT count(*) FROM assessments WHERE study_session_id=ANY(%s)", (study_ids,),
        ).fetchone() == (0,)
    assert not (_root() / "objects" / artifact_id.hex).exists()
    assert not (_root() / ".trash" / artifact_id.hex).exists()
    assert others() == before

    paths = [
        f"/v1/artifacts/{artifact_id}",
        f"/v1/materials/{material_id}",
        f"/v1/material-processing-runs/{fixture['structure']['run_id']}",
        f"/v1/materials/{material_id}/knowledge-structures/{fixture['structure']['revision']}",
        f"/v1/study-sessions/{fixture['active'].study_session_id}",
        (
            f"/v1/materials/{material_id}/knowledge-structures/{fixture['structure']['revision']}"
            f"/study-sessions/{fixture['active'].study_session_id}/resume"
            f"?run_id={fixture['structure']['run_id']}"
        ),
        f"/v1/study-sessions/{fixture['active'].study_session_id}/assessment-sets/{fixture['set_id']}",
    ]
    for path in paths:
        response = client.get(path)
        assert response.status_code == 404
        assert response.json()["reason_code"] == "RESOURCE_NOT_FOUND"


def test_full_delete_rollback_restores_learning_rows_and_quarantined_pdf(learning_records, monkeypatch):
    fixture = learning_records
    before = product_snapshot(fixture["dsn"])

    @contextmanager
    def fail_after_children(dsn):
        with database_session(dsn) as session:
            execute = session.execute

            def checked(statement, *args, **kwargs):
                if str(statement).startswith("DELETE FROM artifacts"):
                    raise RuntimeError("synthetic commit-stage failure")
                return execute(statement, *args, **kwargs)

            session.execute = checked
            yield session

    with monkeypatch.context() as patch:
        patch.setattr(discard, "database_session", fail_after_children)
        with pytest.raises(discard.MaterialDiscardError, match="MATERIAL_DISCARD_STORAGE_FAILED"):
            discard.request_material_discard(
                fixture["learner"].learner_id, fixture["first"].material_id,
                dsn=fixture["dsn"],
            )
    after = product_snapshot(fixture["dsn"])
    assert {k: v for k, v in before.items() if k != "materials"} == {
        k: v for k, v in after.items() if k != "materials"
    }
    artifact_id = fixture["first"].artifact_id
    assert (_root() / "objects" / artifact_id.hex).exists()
    assert not (_root() / ".trash" / artifact_id.hex).exists()
    discard.finish_material_discards(dsn=fixture["dsn"])
    assert not (_root() / "objects" / artifact_id.hex).exists()
