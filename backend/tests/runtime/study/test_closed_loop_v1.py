"""正式資料庫與 API 的閉環測試；共用教材設定由 product_fixtures 提供。"""

from __future__ import annotations

from uuid import UUID

import psycopg
import pytest
from fastapi.testclient import TestClient

from runtime.material_processing import MaterialProcessingError, read_material_processing_run
from product_fixtures import closed_loop


def test_database_requires_structure_schema(closed_loop):
    _, _, _, _, dsn, _ = closed_loop
    with psycopg.connect(dsn) as connection:
        with pytest.raises(psycopg.errors.CheckViolation, match='knowledge_structure_version'):
            with connection.transaction():
                connection.execute("UPDATE knowledge_structures SET document=document-'schema'")


def test_terminal_material_run_tamper_cannot_report_false_success(closed_loop):
    learner, _source, _settings_value, structure, dsn, _token = closed_loop
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "UPDATE material_processing_runs SET output_binding="
            "jsonb_set(output_binding,'{page_count}','2'::jsonb) "
            "WHERE run_id=%s",
            (structure["run_id"],),
        )
    with pytest.raises(MaterialProcessingError, match="MATERIAL_RUN_INVALID"):
        read_material_processing_run(
            learner.learner_id, UUID(structure["run_id"]), dsn=dsn
        )


def test_real_api_lifespan_login_and_saved_reads_work_without_ai(closed_loop, monkeypatch):
    """真 worker 啟停與登入、已保存 Map 讀取皆不需要模型在線。"""
    import httpx
    from runtime.local_app import create_local_app
    learner, source, settings, structure, dsn, _token = closed_loop
    calls = []
    def offline(*_args, **_kwargs):
        calls.append("model")
        raise httpx.ConnectError("offline")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", offline)
    origin = "http://127.0.0.1:4173"
    app = create_local_app(profile="local", public_origin=origin, secure_cookie=False, local_config=settings, dsn=dsn)
    with TestClient(app, base_url=origin) as client:
        login = client.post("/v1/session/login", headers={"Origin": origin}, json={
            "email": "learner_test@example.com", "password": "Synthetic test password 42",
        })
        assert login.status_code == 200
        assert client.get("/v1/session").json()["learner_id"] == str(learner.learner_id)
        assert client.get("/v1/materials").status_code == 200
        map_response = client.get(
            f"/v1/materials/{source.material_id}/knowledge-structures/{structure['revision']}"
        )
        assert map_response.status_code == 200
        assert map_response.json()["concepts"][0]["claims"][0]["evidence"][0]["quote"]
    assert calls == []
