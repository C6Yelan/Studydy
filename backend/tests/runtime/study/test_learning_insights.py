"""診斷保存、版本授權與租約；模型使用受控替身，不碰產品資料。"""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from assessment_fixtures import answer, concept_fixture, create, finish, supplement
from product_fixtures import ORIGIN, _app, closed_loop
from learning_adaptation import learning_insights as insights
from runtime.learner_session import register_account
from runtime.storage.database import database_session
from runtime.storage.tables import LearningInsight


def _base(fixture):
    return (f"/v1/materials/{fixture['source'].material_id}/knowledge-structures/"
            f"{fixture['document']['revision']}")


def _client(fixture, tmp_path, monkeypatch):
    client = TestClient(_app(fixture["dsn"], tmp_path, monkeypatch), base_url=ORIGIN)
    client.cookies.set("studydy_session", fixture["token"])
    return client


def _diagnosis(data):
    return {"strengths": [], "weaknesses": [], "recommendations": []}


def test_completion_queues_both_scopes_and_get_reuses_results(closed_loop, monkeypatch, tmp_path):
    fixture = concept_fixture(closed_loop, 2)
    group = create(fixture)
    finish(fixture)
    client = _client(fixture, tmp_path, monkeypatch)
    before = client.get(_base(fixture) + "/learning-diagnosis")
    assert before.status_code == 200
    assert before.json()["status"] == "no_answers"
    answer(fixture, group, wrong={1})
    calls = []

    template = insights.template_diagnosis

    def generate(data):
        calls.append(deepcopy(data))
        return template(data)

    monkeypatch.setattr(insights, "template_diagnosis", generate)
    assert insights.run_next_insight("diagnosis", dsn=fixture["dsn"])
    assert insights.run_next_insight("diagnosis", dsn=fixture["dsn"])
    assert not insights.run_next_insight("diagnosis", dsn=fixture["dsn"])
    assert {data["scope"] for data in calls} == {"set", "material"}
    assert all(sum(point["answer_count"] for point in data["concepts"][0]["points"]) == 2 for data in calls)
    assert all(sum(point["correct_count"] for point in data["concepts"][0]["points"]) == 1 for data in calls)
    for params in ({}, {"set_id": str(group)}):
        first = client.get(_base(fixture) + "/learning-diagnosis", params=params)
        second = client.get(_base(fixture) + "/learning-diagnosis", params=params)
        assert first.status_code == 200, first.json()
        assert first.json() == second.json()
        assert first.json()["status"] == "ready"
        assert first.json()["event_watermark"] == 2
        assert "runtime_lock" not in first.text and "input_document" not in first.text
        point = first.json()["weaknesses"][0]["points"][0]
        assert point["answer_count"] == 1 and point["correct_count"] == 0
        assert point["latest_is_correct"] is False
        assert len(point["title"]) <= 81
        reference = point["evidence"][0]
        resolved = client.get(first.json()["source_resolver"] + f"/{reference['evidence_id']}/source")
        assert resolved.status_code == 200, resolved.json()
        assert resolved.json()["normalized_page"] == reference["normalized_page"]
    assert not insights.run_next_insight("diagnosis", dsn=fixture["dsn"])
    assert len(calls) == 2


def test_new_answers_replace_material_job_but_keep_old_set_snapshot(closed_loop, monkeypatch):
    fixture = concept_fixture(closed_loop, 1)
    first = create(fixture)
    finish(fixture)
    answer(fixture, first)
    monkeypatch.setattr(insights, "template_diagnosis", _diagnosis)
    while insights.run_next_insight("diagnosis", dsn=fixture["dsn"]):
        pass
    with database_session(fixture["dsn"]) as db:
        original = db.scalar(select(LearningInsight).where(LearningInsight.set_id == first))
        original_input = deepcopy(original.input_document)
        original_result = deepcopy(original.result_document)
    second = create(fixture, key="practice-again")
    finish(fixture, variant="again")
    answer(fixture, second)
    with database_session(fixture["dsn"]) as db:
        rows = db.scalars(select(LearningInsight).where(
            LearningInsight.study_session_id == fixture["study"].study_session_id)).all()
        assert len(rows) == 3
        old = next(row for row in rows if row.set_id == first)
        assert old.input_document == original_input
        assert old.result_document == original_result
        assert old.event_watermark == 1 and old.status == "ready"
        material = next(row for row in rows if row.set_id is None)
        assert material.event_watermark == 2 and material.status == "pending"
        assert material.result_document is None
        assert material.input_document["concepts"][0]["points"][0]["answer_count"] == 2
        current = next(row for row in rows if row.set_id == second)
        assert current.input_document["concepts"][0]["points"][0]["answer_count"] == 1


def test_first_correct_remediation_does_not_claim_completed_strength(closed_loop):
    fixture = concept_fixture(closed_loop, 1)
    original = create(fixture)
    finish(fixture)
    answer(fixture, original, wrong={1})
    first_retry = supplement(fixture, original)
    finish(fixture, variant="first-correct")
    answer(fixture, first_retry)
    while insights.run_next_insight("diagnosis", dsn=fixture["dsn"]):
        pass
    with database_session(fixture["dsn"]) as db:
        first = db.scalar(select(LearningInsight).where(LearningInsight.set_id == first_retry))
        assert first.result_document["strengths"] == []
        assert first.result_document["weaknesses"][0]["points"]
        assert first.input_document["concepts"][0]["points"][0]["answer_count"] == 1
        assert first.input_document["concepts"][0]["points"][0]["correct_count"] == 1
    second_retry = supplement(fixture, original, key="second-correct")
    finish(fixture, variant="second-correct")
    answer(fixture, second_retry)
    while insights.run_next_insight("diagnosis", dsn=fixture["dsn"]):
        pass
    with database_session(fixture["dsn"]) as db:
        second = db.scalar(select(LearningInsight).where(LearningInsight.set_id == second_retry))
        assert second.result_document["strengths"][0]["points"]
        assert second.result_document["weaknesses"] == []
        first = db.scalar(select(LearningInsight).where(LearningInsight.set_id == first_retry))
        assert first.result_document["strengths"] == []


def test_diagnosis_cache_preserves_source_nul_characters(closed_loop):
    fixture = concept_fixture(closed_loop, 1)
    concept = fixture["concept"]
    claim_id = concept["claims"][0]["claim_id"]
    source_text = "合成來源含合法 NUL：\x00；內容保留。"
    data = {"scope": "set", "concepts": [{
        "concept_id": concept["concept_id"], "label": "合成\x00概念",
        "completed_claim_ids": [], "weak_claim_ids": [claim_id],
        "points": [{"claim_id": claim_id, "title": source_text, "answer_count": 1,
                    "correct_count": 0, "latest_is_correct": False, "evidence": []}],
    }]}
    with database_session(fixture["dsn"]) as db:
        insights._enqueue(db, owner=fixture["learner"].learner_id,
            material_id=fixture["source"].material_id, revision=fixture["document"]["revision"],
            task="diagnosis", scope_key="synthetic-source-characters", data=data)
    assert insights.run_next_insight("diagnosis", dsn=fixture["dsn"])
    with database_session(fixture["dsn"]) as db:
        row = db.scalar(select(LearningInsight).where(
            LearningInsight.scope_key == "synthetic-source-characters"))
        assert row.status == "ready"
        assert row.input_document == data
        assert row.result_document["weaknesses"][0]["label"] == "合成\x00概念"
        assert row.result_document["weaknesses"][0]["points"][0]["title"] == source_text


def test_expired_lease_recovers_and_late_result_cannot_publish(closed_loop, monkeypatch):
    fixture = concept_fixture(closed_loop, 1)
    group = create(fixture)
    finish(fixture)
    answer(fixture, group)
    with database_session(fixture["dsn"]) as db:
        row = db.scalar(select(LearningInsight).where(LearningInsight.set_id == group))
        identity = row.insight_id
        row.status = "running"
        row.lease_token = uuid4()
        row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        # 固定這筆先執行，避免依資料庫同時建立紀錄的排序決定測試。
        row.created_at = datetime.now(UTC) - timedelta(days=1)

    def superseded(data):
        with database_session(fixture["dsn"]) as db:
            row = db.get(LearningInsight, identity)
            assert row.status == "running"
            row.status = "pending"
            row.lease_token = None
            row.lease_expires_at = None
        return {"strengths": [], "weaknesses": [], "recommendations": []}

    monkeypatch.setattr(insights, "template_diagnosis", superseded)
    assert insights.run_next_insight("diagnosis", dsn=fixture["dsn"])
    with database_session(fixture["dsn"]) as db:
        row = db.get(LearningInsight, identity)
        assert row.status == "pending" and row.result_document is None
    monkeypatch.setattr(insights, "template_diagnosis", _diagnosis)
    assert insights.run_next_insight("diagnosis", dsn=fixture["dsn"])
    with database_session(fixture["dsn"]) as db:
        assert db.get(LearningInsight, identity).status == "ready"


def test_difficulty_before_answers_and_api_owner_scope(closed_loop, monkeypatch, tmp_path):
    fixture = concept_fixture(closed_loop, 1)
    def model(client, **kwargs):
        return {concept["id"]: {"level": "hard", "reason": "需要整合重點。"}
                for concept in kwargs["request"]["concepts"]}
    monkeypatch.setattr(insights, "request_semantics", model)
    while insights.run_next_insight("difficulty", dsn=fixture["dsn"]):
        pass
    client = _client(fixture, tmp_path, monkeypatch)
    response = client.get(_base(fixture) + "/concept-difficulty")
    assert response.status_code == 200, response.json()
    report = response.json()
    assert report["status"] == "ready"
    assert {item["concept_id"] for item in report["concepts"]} == {
        item["concept_id"] for item in fixture["document"]["concepts"]}
    assert all(item["answer_count"] == 0 for item in report["concepts"])
    foreign = register_account("insights_other@example.com", "Synthetic test password 42", dsn=fixture["dsn"])
    client.cookies.set("studydy_session", foreign.raw_token)
    for endpoint in ("concept-difficulty", "learning-diagnosis"):
        denied = client.get(_base(fixture) + "/" + endpoint)
        assert denied.status_code == 404, denied.json()


def test_personal_difficulty_requires_three_answers_and_survives_remediation(closed_loop, monkeypatch, tmp_path):
    fixture = concept_fixture(closed_loop, 1)
    original = create(fixture)
    finish(fixture)
    answer(fixture, original, wrong={1})
    first = supplement(fixture, original)
    finish(fixture, variant="second-error")
    answer(fixture, first, wrong={1})
    client = _client(fixture, tmp_path, monkeypatch)

    def personal():
        response = client.get(_base(fixture) + "/concept-difficulty")
        assert response.status_code == 200, response.json()
        # 個人標記不能等待模型評估完成，也不能把未評估說成一般難度。
        assert response.json()["status"] == "pending"
        item = next(item for item in response.json()["concepts"]
                    if item["concept_id"] == fixture["concept"]["concept_id"])
        assert item["level"] is None
        return item

    two = personal()
    assert (two["answer_count"], two["incorrect_count"], two["personal_difficulty"]) == (2, 2, False)
    second = supplement(fixture, original, key="first-success")
    finish(fixture, variant="first-success")
    answer(fixture, second)
    three = personal()
    assert (three["answer_count"], three["incorrect_count"], three["personal_difficulty"]) == (3, 2, True)
    third = supplement(fixture, original, key="second-success")
    finish(fixture, variant="second-success")
    answer(fixture, third)
    while insights.run_next_insight("diagnosis", dsn=fixture["dsn"]):
        pass
    report = client.get(_base(fixture) + "/learning-diagnosis").json()
    assert report["weaknesses"] == [] and report["strengths"]
    four = personal()
    assert (four["answer_count"], four["incorrect_count"], four["personal_difficulty"]) == (4, 2, True)
    practice = create(fixture, key="practice-after-completed")
    finish(fixture, variant="practice-after-completed")
    answer(fixture, practice)
    five = personal()
    assert (five["answer_count"], five["incorrect_count"], five["personal_difficulty"]) == (5, 2, False)
