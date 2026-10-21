"""概念卡複習的真 API／DB：佇列、冪等、並行、卡組改版／刪除與帳號隔離。"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient

from assessment_fixtures import concept_fixture
from product_fixtures import HEADERS, ORIGIN, _app, closed_loop
from runtime import card_reviews, card_sets
from runtime.learner_session import register_account
from runtime.material_discard import request_material_discard
from runtime.storage.database import database_session
from runtime.storage.tables import CardSet

TAIPEI = ZoneInfo("Asia/Taipei")


@pytest.fixture
def two_concepts(closed_loop):
    """兩個概念的合成教材，才能測試移出／加回卡組。"""
    made = concept_fixture(closed_loop, 2)
    learner, _, settings, _, dsn, token = closed_loop
    return learner, made["source"], settings, made["document"], dsn, token


def ready_set(fixture, key="cards"):
    learner, source, _, document, dsn, _ = fixture
    ids = [c["concept_id"] for c in document["concepts"]]
    saved = card_sets.create_card_set(learner.learner_id, source.material_id, document["revision"], "複習", ids, key, dsn=dsn)
    with database_session(dsn) as db:
        db.get(CardSet, saved["card_set_id"]).status = "ready"
    return saved["card_set_id"], ids


def test_queue_review_schedule_and_replay(two_concepts):
    owner, _, _, document, dsn, _ = two_concepts
    set_id, ids = ready_set(two_concepts)
    learner = owner.learner_id
    queue = card_reviews.review_queue(learner, dsn=dsn)
    assert (queue["due_count"], queue["new_count"], queue["next_due_at"]) == (0, len(ids), None)
    assert [c["card"]["concept_id"] for c in queue["cards"]] == ids
    assert all(c["state"] == "new" and c["review_count"] == 0 for c in queue["cards"])
    labels = {c["concept_id"]: c["label"] for c in document["concepts"]}
    assert queue["cards"][0]["card"]["label"] == labels[ids[0]] and queue["cards"][0]["card"]["claims"]

    now = datetime(2026, 10, 21, 20, 0, tzinfo=TAIPEI)
    first = card_reviews.submit_review(learner, set_id, ids[0], "remembered", 0, "r1", dsn=dsn, now=now)
    assert (first["review_count"], first["interval_days"]) == (1, 1)
    assert first["due_at"] == datetime(2026, 10, 22, tzinfo=TAIPEI)
    # 同一個鍵重送取得同一筆結果，不會多算一次。
    assert card_reviews.submit_review(learner, set_id, ids[0], "remembered", 0, "r1", dsn=dsn, now=now + timedelta(hours=1)) == first
    with pytest.raises(card_sets.CardSetError, match="^IDEMPOTENCY_CONFLICT$"):
        card_reviews.submit_review(learner, set_id, ids[0], "forgot", 0, "r1", dsn=dsn)
    # 另一個分頁還停在舊次數時拒絕。
    with pytest.raises(card_sets.CardSetError, match="^CARD_REVIEW_CONFLICT$"):
        card_reviews.submit_review(learner, set_id, ids[0], "forgot", 0, "r2", dsn=dsn)

    later = card_reviews.review_queue(learner, dsn=dsn, now=now)
    assert (later["due_count"], later["new_count"]) == (0, len(ids) - 1)
    assert later["next_due_at"] == datetime(2026, 10, 22, tzinfo=TAIPEI)
    assert later["sets"] == [{"card_set_id": set_id, "due_count": 0, "new_count": len(ids) - 1, "reviewed_count": 1}]
    tomorrow = card_reviews.review_queue(learner, dsn=dsn, now=datetime(2026, 10, 22, 0, 1, tzinfo=TAIPEI))
    assert tomorrow["due_count"] == 1
    # 到期卡排在新卡之前。
    assert tomorrow["cards"][0]["card"]["concept_id"] == ids[0] and tomorrow["cards"][0]["state"] == "due"
    assert tomorrow["cards"][0]["last_rating"] == "remembered"

    second = card_reviews.submit_review(learner, set_id, ids[0], "forgot", 1, "r3", dsn=dsn, now=now + timedelta(days=1))
    assert (second["review_count"], second["interval_days"]) == (2, 0)
    with psycopg.connect(dsn) as db:
        assert db.execute("SELECT count(*) FROM card_review_events WHERE card_set_id=%s", (set_id,)).fetchone() == (2,)
        assert db.execute("SELECT streak, review_count, last_rating FROM card_review_states").fetchall() == [(0, 2, "forgot")]

    with pytest.raises(card_sets.CardSetError, match="^RESOURCE_NOT_FOUND$"):
        card_reviews.submit_review(learner, set_id, f"concept:sha256:{'f' * 64}", "remembered", 0, "r4", dsn=dsn)
    with pytest.raises(card_sets.CardSetError, match="^REQUEST_INVALID$"):
        card_reviews.submit_review(learner, set_id, ids[0], "easy", 2, "r5", dsn=dsn)


def test_concurrent_reviews_of_the_same_card_count_once(two_concepts):
    owner, _, _, _, dsn, _ = two_concepts
    set_id, ids = ready_set(two_concepts)

    def rate(key):
        try:
            return card_reviews.submit_review(owner.learner_id, set_id, ids[0], "remembered", 0, key, dsn=dsn)["review_count"]
        except card_sets.CardSetError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = sorted(map(str, pool.map(rate, ["tab-a", "tab-b"])))
    assert results == ["1", "CARD_REVIEW_CONFLICT"]


def test_edit_delete_and_discard_follow_the_card_set(two_concepts):
    owner, source, _, _, dsn, _ = two_concepts
    learner = owner.learner_id
    set_id, ids = ready_set(two_concepts)
    card_reviews.submit_review(learner, set_id, ids[0], "fuzzy", 0, "k1", dsn=dsn)
    # 移出卡組後不再列出；加回來沿用原本排程。
    card_sets.update_card_set(learner, set_id, "複習", 1, ids[1:], dsn=dsn)
    with database_session(dsn) as db:
        db.get(CardSet, set_id).status = "ready"
    queue = card_reviews.review_queue(learner, dsn=dsn)
    assert ids[0] not in [c["card"]["concept_id"] for c in queue["cards"]]
    assert queue["sets"][0]["reviewed_count"] == 0
    with pytest.raises(card_sets.CardSetError, match="^RESOURCE_NOT_FOUND$"):
        card_reviews.submit_review(learner, set_id, ids[0], "remembered", 1, "k2", dsn=dsn)
    card_sets.update_card_set(learner, set_id, "複習", 2, ids, dsn=dsn)
    with database_session(dsn) as db:
        db.get(CardSet, set_id).status = "ready"
    assert card_reviews.review_queue(learner, dsn=dsn)["sets"][0]["reviewed_count"] == 1
    # 生成中的卡組不列入佇列。
    with database_session(dsn) as db:
        db.get(CardSet, set_id).status = "pending"
    assert card_reviews.review_queue(learner, dsn=dsn)["sets"] == []
    with database_session(dsn) as db:
        db.get(CardSet, set_id).status = "ready"

    card_sets.delete_card_set(learner, set_id, dsn=dsn)
    assert card_reviews.review_queue(learner, dsn=dsn)["cards"] == []
    other_set, _ = ready_set(two_concepts, key="second")
    card_reviews.submit_review(learner, other_set, ids[0], "remembered", 0, "k3", dsn=dsn)
    assert request_material_discard(learner, source.material_id, dsn=dsn) == "removed"
    with psycopg.connect(dsn) as db:
        # 教材清除實際刪除卡組時，排程與事件一併刪除。
        assert db.execute("SELECT count(*) FROM card_review_states").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM card_review_events").fetchone() == (0,)
    assert card_reviews.review_queue(learner, dsn=dsn)["cards"] == []


def test_api_boundaries_and_owner_isolation(two_concepts, tmp_path, monkeypatch):
    owner, _, _, _, dsn, token = two_concepts
    set_id, ids = ready_set(two_concepts)
    client = TestClient(_app(dsn, tmp_path, monkeypatch), base_url=ORIGIN)
    url = f"/v1/card-sets/{set_id}/reviews"
    body = {"schema": "card-review-submit/v1", "concept_id": ids[0], "rating": "remembered", "expected_review_count": 0}
    keyed = {**HEADERS, "Idempotency-Key": "api-1"}
    assert client.post(url, headers=keyed, json=body).status_code == 401
    assert client.get("/v1/card-reviews/queue").status_code == 401
    client.cookies.set("studydy_session", token)
    assert client.post(url, json=body, headers={"Idempotency-Key": "api-1"}).status_code == 403
    assert client.post(url, headers=HEADERS, json=body).status_code == 400
    for change in [{"rating": "easy"}, {"expected_review_count": -1}, {"concept_id": "concept:bad"}, {"schema": "x"}, {"extra": 1}]:
        assert client.post(url, headers=keyed, json={**body, **change}).status_code == 400, change
    saved = client.post(url, headers=keyed, json=body)
    assert saved.status_code == 200, saved.json()
    assert saved.json()["schema"] == "card-review/v1" and saved.json()["review_count"] == 1
    assert client.post(url, headers=keyed, json=body).json() == saved.json()
    conflict = client.post(url, headers={**HEADERS, "Idempotency-Key": "api-2"}, json=body)
    assert conflict.status_code == 409 and conflict.json()["reason_code"] == "CARD_REVIEW_CONFLICT"
    queue = client.get("/v1/card-reviews/queue", params={"card_set_id": str(set_id)})
    assert queue.status_code == 200, queue.json()
    assert queue.json()["schema"] == "card-review-queue/v1" and queue.json()["new_count"] == len(ids) - 1
    assert client.get("/v1/card-reviews/queue", params={"card_set_id": "nope"}).status_code == 400
    assert client.get("/v1/card-reviews/queue", params={"limit": "5"}).status_code == 400

    other = register_account(f"other_{uuid4().hex[:8]}@example.com", "Synthetic test password 42", dsn=dsn)
    foreign = TestClient(_app(dsn, tmp_path, monkeypatch), base_url=ORIGIN)
    foreign.cookies.set("studydy_session", other.raw_token)
    assert foreign.get("/v1/card-reviews/queue").json()["cards"] == []
    assert foreign.get("/v1/card-reviews/queue", params={"card_set_id": str(set_id)}).status_code == 404
    stolen = foreign.post(url, headers={**HEADERS, "Idempotency-Key": "x"}, json={**body, "expected_review_count": 1})
    assert stolen.status_code == 404
