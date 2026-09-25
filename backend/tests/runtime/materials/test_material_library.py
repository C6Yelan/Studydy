"""教材庫的擁有權、舊版本讀取與草稿測試。"""

from uuid import UUID

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

import runtime.storage.materials as material_storage
from runtime.learner_session import register_account
from runtime.storage.knowledge_structures import KnowledgeStructureStoreError, read_knowledge_structure
from product_fixtures import HEADERS, ORIGIN, _app, library_materials, product_snapshot, closed_loop


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
    assert {item["display_name"] for item in items} == {"堆疊講義.pdf", "陣列入門.pdf", "遞迴課堂筆記.pdf"}
    first = next(item for item in items if item["material_id"] == str(fixture["first"].material_id))
    assert first["latest_attempt"]["status"] == "failed"
    assert first["latest_attempt"]["run_id"] == str(fixture["failed"].run_id)
    assert [link["knowledge_structure_revision"] for link in first["available_structures"]] == [fixture["second_structure"]["revision"], fixture["structure"]["revision"]]
    assert [link["status"] for link in first["available_structures"]] == ["partial", "succeeded"]
    for item in items:
        detail = client.get(f"/v1/materials/{item['material_id']}")
        assert detail.json() == item
        if item["display_name"] == "陣列入門.pdf":
            assert item["latest_attempt"] is None and item["available_structures"] == []
        if item["display_name"] == "遞迴課堂筆記.pdf":
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
    assert client.get("/v1/materials").json() == {"schema": "material-library/v1", "materials": []}
    def unavailable(*_args, **_kwargs):
        raise SQLAlchemyError("synthetic-storage-detail-not-for-response")
    monkeypatch.setattr(material_storage, "database_session", unavailable)
    failure = client.get("/v1/materials")
    assert failure.status_code == 503
    assert failure.json()["reason_code"] == "STORAGE_UNAVAILABLE"
    assert "synthetic-storage-detail" not in failure.text


def test_reopen_rejects_document_bound_to_another_run(library_materials):
    fixture = library_materials
    first = fixture["structure"]
    with psycopg.connect(fixture["dsn"]) as connection:
        # 相同 source 的 run 被錯接時，仍必須拒絕 exact revision 讀取。
        connection.execute("UPDATE knowledge_structures SET run_id=%s WHERE structure_revision=%s", (fixture["failed"].run_id, first["revision"]))
        connection.execute("UPDATE material_processing_runs SET status='succeeded', progress_stage='completed', error_code=NULL, completed_pages=1, total_pages=1, output_binding=(SELECT output_binding FROM material_processing_runs WHERE run_id=%s) WHERE run_id=%s", (UUID(first["run_id"]), fixture["failed"].run_id))
    with pytest.raises(KnowledgeStructureStoreError, match="KNOWLEDGE_STRUCTURE_UNAVAILABLE"):
        read_knowledge_structure(fixture["learner"].learner_id, fixture["first"].material_id, revision=first["revision"], dsn=fixture["dsn"])


def test_named_draft_is_owned_and_idempotent(closed_loop,tmp_path,monkeypatch):
    learner,_,_,_,dsn,token=closed_loop
    client=TestClient(_app(dsn,tmp_path,monkeypatch),base_url=ORIGIN)
    client.cookies.set('studydy_session',token)
    headers={**HEADERS,'Idempotency-Key':'named-draft'}
    body={'schema':'material-draft-create/v1','display_name':'陣列.pdf'}
    created=client.post('/v1/materials',headers=headers,json=body)
    assert created.status_code==201
    assert client.post('/v1/materials',headers=headers,json=body).json()==created.json()
    item=client.get('/v1/materials/'+created.json()['material_id']).json()
    assert item['display_name']=='陣列.pdf' and item['latest_attempt'] is None
    assert client.post('/v1/materials',headers=headers,json={**body,'display_name':'different.pdf'}).status_code==409
    for invalid in ['\x00.pdf','../private.pdf','x'*201]:
        assert client.post('/v1/materials',headers={**headers,'Idempotency-Key':'invalid'},json={**body,'display_name':invalid}).status_code==400
    assert client.post('/v1/materials',headers=HEADERS,content=b'retired').status_code == 400
    assert client.post('/v2/materials',headers=HEADERS,content=b'retired').status_code == 404
