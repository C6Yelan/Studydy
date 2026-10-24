"""難度工作中斷接續與費用邊界；全部模型由替身提供。"""
from datetime import UTC, datetime, timedelta
from copy import deepcopy

from sqlalchemy import select

from assessment_fixtures import concept_fixture
from product_fixtures import closed_loop
from learning_adaptation import learning_insights as insights
from learning_adaptation.concept_difficulty import difficulty_batches, response_schema
from runtime.generation_jobs import JobCancelled
from runtime.storage.database import database_session
from runtime.storage.tables import LearningInsight


def _job(fixture):
    with database_session(fixture["dsn"]) as db:
        row = db.scalar(select(LearningInsight).where(
            LearningInsight.material_id == fixture["source"].material_id,
            LearningInsight.task == "difficulty"))
        row.created_at = datetime.now(UTC) - timedelta(days=1)
        return row.insight_id


def _two_batches(document, lock, **kwargs):
    batch = difficulty_batches(document, lock, **kwargs)[0]
    return [{"request": {"concepts": [concept]},
             "mapping": {concept["id"]: batch["mapping"][concept["id"]]},
             "response_schema": response_schema([concept["id"]])}
            for concept in batch["request"]["concepts"]]


def test_restart_reuses_saved_batch_and_preserves_frozen_runtime(closed_loop, monkeypatch):
    fixture = concept_fixture(closed_loop, 1)
    identity = _job(fixture)
    monkeypatch.setattr(insights, "difficulty_batches", _two_batches)
    calls = []
    saved_lock = None
    with database_session(fixture["dsn"]) as db:
        saved_lock = deepcopy(db.get(LearningInsight, identity).runtime_lock_document)

    def model(client, **kwargs):
        assert kwargs["runtime_lock"] == saved_lock
        ids = [concept["id"] for concept in kwargs["request"]["concepts"]]
        calls.append(ids)
        if len(calls) == 2:
            raise JobCancelled()
        return {short: {"level": "standard", "reason": ""} for short in ids}

    monkeypatch.setattr(insights, "request_semantics", model)
    assert insights.run_next_insight("difficulty", dsn=fixture["dsn"])
    with database_session(fixture["dsn"]) as db:
        row = db.get(LearningInsight, identity)
        assert row.status == "pending" and row.result_document is None
        assert len(row.completed_batches) == 1 and row.model_calls == 2
    assert insights.run_next_insight("difficulty", dsn=fixture["dsn"])
    with database_session(fixture["dsn"]) as db:
        row = db.get(LearningInsight, identity)
        assert row.status == "ready" and len(row.completed_batches) == 2
        assert row.model_calls == 3
        assert {item["concept_id"] for item in row.result_document["concepts"]} == {
            concept["concept_id"] for concept in fixture["document"]["concepts"]}
    assert calls == [["c1"], ["c2"], ["c2"]]


def test_saved_call_budget_blocks_another_provider_request(closed_loop, monkeypatch):
    fixture = concept_fixture(closed_loop, 1)
    identity = _job(fixture)
    with database_session(fixture["dsn"]) as db:
        db.get(LearningInsight, identity).model_calls = 5
    calls = []
    monkeypatch.setattr(insights, "request_semantics", lambda *a, **k: calls.append(k))
    assert insights.run_next_insight("difficulty", dsn=fixture["dsn"])
    with database_session(fixture["dsn"]) as db:
        row = db.get(LearningInsight, identity)
        assert row.status == "failed" and row.result_document is None
        assert row.failure_reason == "DIFFICULTY_CALL_BUDGET_EXCEEDED"
        assert row.model_calls == 5
    assert calls == []


def test_capacity_failure_does_not_publish_fallback_or_send_model(closed_loop, monkeypatch):
    fixture = concept_fixture(closed_loop, 1)
    identity = _job(fixture)
    def too_large(*args, **kwargs):
        raise ValueError("DIFFICULTY_CALL_BUDGET_EXCEEDED")
    monkeypatch.setattr(insights, "difficulty_batches", too_large)
    calls = []
    monkeypatch.setattr(insights, "request_semantics", lambda *a, **k: calls.append(k))
    assert insights.run_next_insight("difficulty", dsn=fixture["dsn"])
    with database_session(fixture["dsn"]) as db:
        row = db.get(LearningInsight, identity)
        assert row.status == "failed" and row.result_document is None
        assert row.failure_reason == "DIFFICULTY_CALL_BUDGET_EXCEEDED"
        assert row.model_calls == 0
    assert calls == []
