from __future__ import annotations

import hashlib
import io
import shutil
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError
import psycopg
import pytest

import runtime.api.app as api_app
from runtime.learner_session import register_account
from runtime.material_processing import create_material_processing_run, claim_next_material_processing_run, _record_progress
from runtime.storage.artifacts import publish_idempotent_source_pdf, open_verified_source_pdf
from runtime.storage.knowledge_structures import publish_knowledge_structure, read_knowledge_structure, KnowledgeStructureStoreError
import runtime.storage.materials as material_storage
from runtime.storage.materials import read_material_library
from runtime.storage.migrations import run_migrations
from test_closed_loop_v1 import closed_loop, _pdf, _structure
from test_accounts import _app, HEADERS, ORIGIN


@pytest.fixture
def library_materials(closed_loop):
    learner, first, settings, structure, dsn, token = closed_loop
    with psycopg.connect(dsn) as connection:
        connection.execute("UPDATE materials SET display_name='堆疊講義.pdf' WHERE material_id=%s", (first.material_id,))
    second_run = create_material_processing_run(learner.learner_id, first.material_id, first.artifact_id, "second-version", settings, dsn=dsn)
    assert claim_next_material_processing_run(dsn=dsn).run.run_id == second_run.run_id
    for stage in ("evidence", "semantics", "publishing"):
        _record_progress(second_run.run_id, stage, 1, 1, dsn=dsn)
    second_structure = _structure(str(second_run.run_id), first.sha256, settings["runtime_lock"], partial=True)
    publish_knowledge_structure(learner.learner_id, first.material_id, second_run.run_id, second_structure, dsn=dsn)
    failed = create_material_processing_run(learner.learner_id, first.material_id, first.artifact_id, "new-failed-attempt", settings, dsn=dsn)
    with psycopg.connect(dsn) as connection:
        connection.execute("UPDATE material_processing_runs SET status='failed', error_code='NO_USABLE_EVIDENCE', completed_at=now(), updated_at=now() WHERE run_id=%s", (failed.run_id,))
    uploaded = publish_idempotent_source_pdf(learner.learner_id, io.BytesIO(_pdf()), "uploaded-only", display_name="尚未處理.pdf", dsn=dsn)
    running = publish_idempotent_source_pdf(learner.learner_id, io.BytesIO(_pdf()), "running-source", display_name="處理中的筆記.pdf", dsn=dsn)
    running_run = create_material_processing_run(learner.learner_id, running.material_id, running.artifact_id, "running", settings, dsn=dsn)
    assert claim_next_material_processing_run(dsn=dsn).run.run_id == running_run.run_id
    _record_progress(running_run.run_id, "evidence", 0, 1, dsn=dsn)
    foreign = register_account("library_b@example.com", "Synthetic test password 42", dsn=dsn)
    foreign_source = publish_idempotent_source_pdf(foreign.learner_id, io.BytesIO(_pdf()), "foreign-source", display_name="B 的私人教材.pdf", dsn=dsn)
    return {"learner": learner, "first": first, "structure": structure, "second_structure": second_structure,
            "failed": failed, "uploaded": uploaded, "running": running, "foreign": foreign, "foreign_source": foreign_source,
            "dsn": dsn, "token": token, "settings": settings}


def product_snapshot(dsn):
    """比對完整產品 rows 的摘要，避免把教材內容印進 assertion log。"""
    tables = ("materials", "artifacts", "material_processing_runs", "knowledge_structures", "study_sessions", "assessments", "answer_events")
    snapshots = {}
    with psycopg.connect(dsn) as connection:
        for table in tables:
            rows = connection.execute(f"SELECT row_to_json(t)::text FROM {table} t ORDER BY 1").fetchall()
            snapshots[table] = (len(rows), hashlib.sha256("\n".join(row[0] for row in rows).encode()).hexdigest())
    return snapshots


def test_library_owns_all_materials_and_keeps_prior_versions(library_materials, tmp_path, monkeypatch):
    fixture = library_materials
    dsn = fixture["dsn"]
    app = _app(dsn, tmp_path, monkeypatch)
    client = TestClient(app, base_url=ORIGIN)
    assert client.get("/v1/materials").status_code == 401
    assert client.get(f"/v1/materials/{fixture['first'].material_id}").status_code == 401
    client.cookies.set("studydy_session", fixture["token"])
    before = product_snapshot(dsn)
    listed = client.get("/v1/materials")
    assert listed.status_code == 200 and listed.headers["cache-control"] == "private, no-store"
    items = listed.json()["materials"]
    assert {item["display_name"] for item in items} == {"堆疊講義.pdf", "尚未處理.pdf", "處理中的筆記.pdf"}
    first = next(item for item in items if item["material_id"] == str(fixture["first"].material_id))
    assert first["latest_attempt"]["status"] == "failed"
    assert first["latest_attempt"]["run_id"] == str(fixture["failed"].run_id)
    assert [link["knowledge_structure_revision"] for link in first["available_structures"]] == [fixture["second_structure"]["revision"], fixture["structure"]["revision"]]
    assert [link["status"] for link in first["available_structures"]] == ["partial", "succeeded"]
    for item in items:
        detail = client.get(f"/v1/materials/{item['material_id']}")
        assert detail.json() == item
        if item["display_name"] == "尚未處理.pdf":
            assert item["latest_attempt"] is None and item["available_structures"] == []
        if item["display_name"] == "處理中的筆記.pdf":
            assert item["latest_attempt"]["status"] == "running" and item["available_structures"] == []
        for link in item["available_structures"]:
            response = client.get(f"/v1/materials/{item['material_id']}/knowledge-structures/{link['knowledge_structure_revision']}")
            assert response.status_code == 200
            assert response.json()["knowledge_structure_revision"] == link["knowledge_structure_revision"]
            run = client.get(f"/v1/material-processing-runs/{link['run_id']}").json()
            assert run["source_artifact_id"] == item["source_artifact_id"]
    assert product_snapshot(dsn) == before
    assert client.get("/v1/materials?learner_id=" + str(fixture["foreign"].learner_id)).status_code == 400
    assert client.get("/v1/materials", headers={"X-Learner-Id": str(fixture["foreign"].learner_id)}).status_code == 400
    assert client.get(f"/v1/materials/{fixture['foreign_source'].material_id}").status_code == 404
    client.cookies.clear()
    client.cookies.set("studydy_session", fixture["foreign"].raw_token)
    assert [item["display_name"] for item in client.get("/v1/materials").json()["materials"]] == ["B 的私人教材.pdf"]
    for path in (f"/v1/materials/{first['material_id']}", f"/v1/artifacts/{first['source_artifact_id']}",
                 f"/v1/materials/{first['material_id']}/knowledge-structures/{first['available_structures'][0]['knowledge_structure_revision']}"):
        assert client.get(path).status_code == 404
    assert product_snapshot(dsn) == before
    empty = register_account("empty_library@example.com", "Synthetic test password 42", dsn=dsn)
    client.cookies.clear()
    client.cookies.set("studydy_session", empty.raw_token)
    assert client.get("/v1/materials").json() == {"schema": "material-library/v2", "materials": []}
    def unavailable(*_args, **_kwargs):
        raise SQLAlchemyError("synthetic-storage-detail-not-for-response")
    monkeypatch.setattr(material_storage, "database_session", unavailable)
    failure = client.get("/v1/materials")
    assert failure.status_code == 503
    assert failure.json()["reason_code"] == "STORAGE_UNAVAILABLE"
    assert "synthetic-storage-detail" not in failure.text


def test_named_upload_is_owned_and_idempotent(closed_loop, tmp_path, monkeypatch):
    learner, _, _, _, dsn, token = closed_loop
    client = TestClient(_app(dsn, tmp_path, monkeypatch), base_url=ORIGIN)
    client.cookies.set("studydy_session", token)
    headers = {**HEADERS, "Content-Type": "application/pdf", "Idempotency-Key": "named-upload", "X-Material-Name": "%E9%99%A3%E5%88%97.pdf"}
    pdf = _pdf()
    created = client.post("/v1/materials", headers=headers, content=pdf)
    assert created.status_code == 201
    assert client.post("/v1/materials", headers=headers, content=pdf).json() == created.json()
    item = client.get("/v1/materials/" + created.json()["material_id"]).json()
    assert item["display_name"] == "陣列.pdf" and item["latest_attempt"] is None
    conflict = client.post("/v1/materials", headers={**headers, "X-Material-Name": "different.pdf"}, content=pdf)
    assert conflict.status_code == 409
    for invalid in ("%00.pdf", "..%2Fprivate.pdf", "%FF", "x" * 201):
        assert client.post("/v1/materials", headers={**headers, "X-Material-Name": invalid}, content=pdf).status_code == 400
    with psycopg.connect(dsn) as connection:
        assert connection.execute("SELECT count(*) FROM materials WHERE learner_id=%s", (learner.learner_id,)).fetchone() == (2,)


def test_material_name_migration_preserves_accepted_schema(clean_database_dsn, migrations_dir, tmp_path, monkeypatch):
    accepted = tmp_path / "accepted"
    accepted.mkdir()
    for name in ("0001_final_schema.sql", "0002_learner_credentials.sql"):
        shutil.copyfile(migrations_dir / name, accepted / name)
    assert run_migrations(clean_database_dsn, migrations_dir=accepted) == (1, 2)
    from runtime.learner_session import TrustedLearner
    learner = TrustedLearner(uuid4())
    with psycopg.connect(clean_database_dsn) as connection:
        connection.execute("INSERT INTO learners (learner_id,created_at,username,password_hash) VALUES (%s,now(),%s,%s)", (learner.learner_id,"migration_reader","old-fixture-hash"))
    material_id, artifact_id = uuid4(), uuid4()
    content = _pdf()
    digest = hashlib.sha256(content).digest()
    fingerprint = hashlib.sha256(digest + len(content).to_bytes(8, "big")).digest()
    root = tmp_path / "artifacts"
    root.mkdir(mode=0o700)
    (root / "objects").mkdir(mode=0o700)
    (root / "objects" / artifact_id.hex).write_bytes(content)
    monkeypatch.setenv("STUDYDY_ARTIFACT_ROOT", str(root))
    with psycopg.connect(clean_database_dsn) as connection:
        connection.execute("INSERT INTO materials VALUES (%s,%s,%s,%s,%s,now())", (material_id, learner.learner_id, artifact_id, hashlib.sha256(b"old-upload").digest(), fingerprint))
        connection.execute("INSERT INTO artifacts VALUES (%s,%s,%s,'source_pdf','application/pdf',%s,%s,now())", (artifact_id, learner.learner_id, material_id, digest, len(content)))
        previous = connection.execute("SELECT * FROM materials").fetchone()
    assert run_migrations(clean_database_dsn) == (3, 4, 5, 6, 7, 8, 9)
    assert run_migrations(clean_database_dsn) == ()
    with psycopg.connect(clean_database_dsn) as connection:
        assert connection.execute("SELECT material_id,learner_id,source_artifact_id,upload_idempotency_key_sha256,upload_request_fingerprint,created_at FROM materials").fetchone() == previous
        assert connection.execute("SELECT display_name FROM materials").fetchone() == (None,)
    item = read_material_library(learner.learner_id, dsn=clean_database_dsn)[0]
    assert str(material_id)[:8] in item["display_name"]
    assert item["material_id"] == material_id and item["source_artifact_id"] == artifact_id
    # 舊無名稱 upload receipt 仍可重播，不改 owner 或新增資料。
    assert publish_idempotent_source_pdf(learner.learner_id, io.BytesIO(content), "old-upload", dsn=clean_database_dsn).material_id == material_id
    with open_verified_source_pdf(learner.learner_id, artifact_id, dsn=clean_database_dsn) as stored:
        assert stored.file.read() == content


def test_reopen_rejects_document_bound_to_another_run(library_materials):
    fixture = library_materials
    first = fixture["structure"]
    with psycopg.connect(fixture["dsn"]) as connection:
        # 相同 source 的 run 被錯接時，仍必須拒絕 exact revision 讀取。
        connection.execute("UPDATE knowledge_structures SET run_id=%s WHERE structure_revision=%s", (fixture["failed"].run_id, first["revision"]))
        connection.execute("UPDATE material_processing_runs SET status='succeeded', progress_stage='completed', error_code=NULL, completed_pages=1, total_pages=1, output_binding=(SELECT output_binding FROM material_processing_runs WHERE run_id=%s) WHERE run_id=%s", (UUID(first["run_id"]), fixture["failed"].run_id))
    with pytest.raises(KnowledgeStructureStoreError, match="KNOWLEDGE_STRUCTURE_UNAVAILABLE"):
        read_knowledge_structure(fixture["learner"].learner_id, fixture["first"].material_id, revision=first["revision"], dsn=fixture["dsn"])
