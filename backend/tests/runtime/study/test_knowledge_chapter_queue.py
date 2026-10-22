"""章節分區佇列與地圖 API：實際 DB、受控模型回應；不呼叫真實模型。"""
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from assessment_fixtures import concept_fixture
from product_fixtures import ORIGIN, _app, closed_loop, seed_run
from runtime import knowledge_chapters as kc
from runtime.storage.database import database_session
from runtime.storage.tables import KnowledgeChapters, KnowledgeStructure


def _client(fixture, tmp_path, monkeypatch):
    client = TestClient(_app(fixture["dsn"], tmp_path, monkeypatch), base_url=ORIGIN)
    client.cookies.set("studydy_session", fixture["token"])
    return client


def _key(fixture):
    return (
        fixture["learner"].learner_id,
        fixture["source"].material_id,
        fixture["document"]["revision"],
    )


def _row(fixture):
    with database_session(fixture["dsn"]) as db:
        row = db.get(KnowledgeChapters, _key(fixture))
        return None if row is None else {
            c.name: getattr(row, c.name) for c in KnowledgeChapters.__table__.columns
        }


def _answer(request, schema, budget):
    assert budget + kc.estimate_prompt_tokens(kc.PROMPT, request) <= kc.CONTEXT_LIMIT
    return {"chapters": [{"title": "訊號編碼規則", "start": 0}, {"title": "跨領域對照", "start": 1}]}


def test_publish_queues_chapters_and_api_serves_them(closed_loop, tmp_path, monkeypatch):
    fixture = concept_fixture(closed_loop, 3)
    base = (
        f"/v1/materials/{fixture['source'].material_id}"
        f"/knowledge-structures/{fixture['document']['revision']}/regions"
    )
    client = _client(fixture, tmp_path, monkeypatch)
    queued = _row(fixture)
    assert queued["status"] == "pending" and queued["chapters"] is None
    assert queued["prompt_sha256"] == kc.PROMPT_SHA256 and queued["policy"] == kc.POLICY
    # 尚未完成時沿用現行分區。
    before = client.get(base).json()
    assert before["basis"] == "learning_order" and before["groups"] == [] and before["partial"] is False
    with database_session(fixture["dsn"]) as db:
        structures = db.scalar(select(func.count()).select_from(KnowledgeStructure))
        study_rows = db.execute(text("SELECT * FROM study_sessions ORDER BY 1")).all()

    # 前一份教材的章節工作也在佇列裡；依建立順序一份一份處理。
    calls = []

    def answer(request, schema, budget):
        calls.append(request)
        return _answer(request, schema, budget)

    while kc.step(dsn=fixture["dsn"], call=answer, model_id="test-model"):
        pass
    done = _row(fixture)
    assert done["status"] == "ready" and done["model_id"] == "test-model", (done["error_code"], done["failed_windows"], done["calls"])
    assert done["calls"] == 1 and done["failed_windows"] == [] and done["lease_token"] is None
    path = [c for chapter in done["chapters"] for c in chapter["concept_ids"]]
    assert [c["title"] for c in done["chapters"]] == ["訊號編碼規則", "跨領域對照"]
    labels = {c[0]: c[1] for c in calls[-1]["concepts"]}
    assert labels == {0: "Signals", 1: "Other topic"}

    body = client.get(base).json()
    assert body["basis"] == "chapters" and body["partial"] is False and body["groups"] == []
    assert [(r["label"], r["group_id"], r["source_id"]) for r in body["regions"]] == [
        ("訊號編碼規則", None, None), ("跨領域對照", None, None),
    ]
    assert [c for r in body["regions"] for c in r["concept_ids"]] == path == before_path(before)
    # 衍生資料不產生新版本，也不動學習紀錄。
    with database_session(fixture["dsn"]) as db:
        assert db.scalar(select(func.count()).select_from(KnowledgeStructure)) == structures
        assert db.execute(text("SELECT * FROM study_sessions ORDER BY 1")).all() == study_rows

    # 部分窗退回時標示 partial；失敗時退回現行分區。
    with database_session(fixture["dsn"]) as db:
        row = db.get(KnowledgeChapters, _key(fixture))
        row.status = "fallback"
        row.chapters = [
            {"title": "訊號編碼規則", "concept_ids": path[:1], "fallback": False},
            {"title": "Synthetic", "concept_ids": path[1:], "fallback": True},
        ]
    assert client.get(base).json()["partial"] is True
    with database_session(fixture["dsn"]) as db:
        row = db.get(KnowledgeChapters, _key(fixture))
        row.status, row.chapters = "failed", None
    assert client.get(base).json()["basis"] == "learning_order"


def before_path(view):
    return [c for r in view["regions"] for c in r["concept_ids"]]


def _drain_others(fixture):
    """closed_loop 自帶一份已發布教材；先讓它的章節工作失敗結束，只留本案例的教材。"""
    with database_session(fixture["dsn"]) as db:
        db.execute(
            text("UPDATE knowledge_chapters SET status='failed' WHERE material_id <> :m"),
            {"m": fixture["source"].material_id},
        )


def test_failed_windows_fall_back_and_interrupted_jobs_retry_once(closed_loop):
    fixture = concept_fixture(closed_loop, 3)
    _drain_others(fixture)

    def broken(*_):
        raise kc.SemanticServiceError("SEMANTIC_SERVICE_UNAVAILABLE")

    assert kc.step(dsn=fixture["dsn"], call=broken, model_id="test-model")
    row = _row(fixture)
    assert row["status"] == "failed" and row["calls"] == 2 and row["chapters"] is None
    assert row["error_code"] == "CHAPTER_ALL_WINDOWS_FAILED"
    assert row["failed_windows"][0]["errors"] == ["CALL_FAILED:SEMANTIC_SERVICE_UNAVAILABLE"]
    assert kc.step(dsn=fixture["dsn"], call=broken) is False  # 不無限重試

    # 工作中斷（租約過期）可重來一次；第二次仍中斷就判定失敗。
    with database_session(fixture["dsn"]) as db:
        row = db.get(KnowledgeChapters, _key(fixture))
        row.status, row.attempts, row.lease_token = "running", 1, uuid4()
        row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert kc.step(dsn=fixture["dsn"], call=_answer, model_id="m")
    assert _row(fixture)["status"] == "ready" and _row(fixture)["attempts"] == 2
    with database_session(fixture["dsn"]) as db:
        row = db.get(KnowledgeChapters, _key(fixture))
        row.status, row.attempts, row.lease_token, row.chapters = "running", 2, uuid4(), None
        row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert kc.step(dsn=fixture["dsn"], call=lambda *_: __import__("pytest").fail("sent"))
    row = _row(fixture)
    assert row["status"] == "failed" and row["error_code"] == "CHAPTER_JOB_INTERRUPTED"


def test_shutdown_returns_the_job_to_the_queue(closed_loop):
    fixture = concept_fixture(closed_loop, 3)
    _drain_others(fixture)

    def stopping(*_):
        raise kc.WorkerStopping()

    assert kc.step(dsn=fixture["dsn"], call=stopping, stop=Event())
    row = _row(fixture)
    assert (row["status"], row["attempts"], row["lease_token"]) == ("pending", 0, None)


def test_material_analysis_runs_first(closed_loop):
    fixture = concept_fixture(closed_loop, 3)
    _drain_others(fixture)
    seed_run(
        fixture["learner"].learner_id, fixture["source"].material_id, str(uuid4()),
        fixture["settings"], dsn=fixture["dsn"],
    )
    assert kc.step(dsn=fixture["dsn"], call=_answer) is False
    assert _row(fixture)["status"] == "pending"


def test_backfill_queues_published_materials_once(closed_loop):
    fixture = concept_fixture(closed_loop, 3)
    dsn = fixture["dsn"]
    with database_session(dsn) as db:
        db.execute(text("DELETE FROM knowledge_chapters"))
    material = fixture["source"].material_id
    assert kc.queue_existing(dsn=dsn, material_ids=[material]) == [(material, "queued")]
    assert kc.queue_existing(dsn=dsn, material_ids=[material]) == [(material, "kept:pending")]
    everything = dict(kc.queue_existing(dsn=dsn))
    assert len(everything) == 2 and everything[material] == "kept:pending"
    _drain_others(fixture)
    assert kc.step(dsn=dsn, call=_answer, model_id="m")
    assert kc.queue_existing(dsn=dsn, material_ids=[material]) == [(material, "kept:ready")]
    assert kc.queue_existing(dsn=dsn, material_ids=[material], requeue=True) == [(material, "requeued")]
    row = _row(fixture)
    assert (row["status"], row["chapters"], row["calls"], row["attempts"]) == ("pending", None, 0, 0)


def test_migration_enforces_status_and_cascades_with_the_structure(closed_loop):
    fixture = concept_fixture(closed_loop, 3)
    owner, material, revision = _key(fixture)
    import psycopg
    import pytest

    with psycopg.connect(fixture["dsn"]) as connection:
        for statement in (
            "UPDATE knowledge_chapters SET status='done'",
            "UPDATE knowledge_chapters SET status='ready'",  # ready 必須有 chapters
            "UPDATE knowledge_chapters SET status='running'",  # running 必須有租約
        ):
            with pytest.raises(psycopg.errors.CheckViolation):
                with connection.transaction():
                    connection.execute(statement)
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with connection.transaction():
                connection.execute(
                    "INSERT INTO knowledge_chapters (learner_id, material_id, structure_revision, status, policy,"
                    " prompt_sha256, created_at, updated_at) VALUES (%s, %s, %s, 'pending',"
                    " 'knowledge-chapters/v1', %s, now(), now())",
                    (owner, material, "knowledge-structure:sha256:" + "0" * 64, kc.PROMPT_SHA256),
                )
        connection.execute("UPDATE materials SET head_revision = NULL WHERE material_id = %s", (material,))
        for table in ("study_sessions", "card_source_indexes", "knowledge_read_indexes",
                      "material_retrieval_indexes"):
            connection.execute(f"DELETE FROM {table} WHERE material_id = %s", (material,))
        connection.execute("DELETE FROM knowledge_structures WHERE material_id = %s", (material,))
        assert connection.execute(
            "SELECT count(*) FROM knowledge_chapters WHERE material_id = %s", (material,)
        ).fetchone() == (0,)


def test_worker_sends_the_chapter_prompt_through_the_luna_text_channel(closed_loop, monkeypatch):
    import json
    from pathlib import Path

    from runtime import provider_http
    from runtime.luna_test import test_lock

    fixture = concept_fixture(closed_loop, 3)
    _drain_others(fixture)
    lock = json.loads((Path(__file__).parents[4] / "local_ai/runtime-lock.json").read_text())
    monkeypatch.setattr(kc, "configured_lock", lambda: test_lock(lock))
    sent = []

    def provider(path, body):
        # 請求進行中持有共用閘門，教材分析要等這筆結束才能領取。
        assert kc.request_gate.locked()
        sent.append((path, body))
        return _answer(body["request"], body["schema"], kc.CONTEXT_LIMIT - kc.estimate_prompt_tokens(body["prompt"], body["request"]))

    monkeypatch.setattr(provider_http, "provider", provider)
    assert kc.step(dsn=fixture["dsn"], stop=Event())
    assert not kc.request_gate.locked()
    row = _row(fixture)
    assert row["status"] == "ready" and row["model_id"] == "gpt-5.6-luna" and row["calls"] == 1
    path, body = sent[0]
    assert path == "/semantics" and body["task"] == "material_review" and body["prompt"] == kc.PROMPT
    assert set(body["request"]) == {"sources", "concepts", "chapter_count"}
