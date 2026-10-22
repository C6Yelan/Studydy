"""概念卡學習標記的真 API／DB：需要加強清單、冪等、並行、卡組改版／刪除與帳號隔離。"""
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


def test_marks_weak_list_and_replay(two_concepts):
    owner, _, _, document, dsn, _ = two_concepts
    set_id, ids = ready_set(two_concepts)
    learner = owner.learner_id
    empty = card_reviews.review_summary(learner, dsn=dsn)
    # 沒標記的卡不算需要加強，也沒有「新卡」。
    assert (empty["forgot_count"], empty["cards"], empty["marks"]) == (0, [], [])
    assert empty["sets"] == [{"card_set_id": set_id, "card_count": len(ids), "forgot_count": 0, "remembered_count": 0}]

    now = datetime(2026, 10, 21, 20, 0, tzinfo=TAIPEI)
    first = card_reviews.submit_review(learner, set_id, ids[1], "forgot", 0, "r1", dsn=dsn, now=now)
    assert (first["schema"], first["rating"], first["review_count"]) == ("card-review/v2", "forgot", 1)
    # 同一個鍵重送取得同一筆結果，不會多算一次。
    assert card_reviews.submit_review(learner, set_id, ids[1], "forgot", 0, "r1", dsn=dsn, now=now + timedelta(hours=1)) == first
    with pytest.raises(card_sets.CardSetError, match="^IDEMPOTENCY_CONFLICT$"):
        card_reviews.submit_review(learner, set_id, ids[1], "remembered", 0, "r1", dsn=dsn)
    # 另一個分頁還停在舊次數時拒絕。
    with pytest.raises(card_sets.CardSetError, match="^CARD_REVIEW_CONFLICT$"):
        card_reviews.submit_review(learner, set_id, ids[1], "remembered", 0, "r2", dsn=dsn)
    card_reviews.submit_review(learner, set_id, ids[0], "forgot", 0, "r3", dsn=dsn, now=now + timedelta(minutes=1))

    summary = card_reviews.review_summary(learner, dsn=dsn)
    assert summary["forgot_count"] == 2
    # 較久沒標記的在前；只有指定卡組時才列出各卡標記。
    assert [c["card"]["concept_id"] for c in summary["cards"]] == [ids[1], ids[0]]
    labels = {c["concept_id"]: c["label"] for c in document["concepts"]}
    assert summary["cards"][0]["card"]["label"] == labels[ids[1]] and summary["cards"][0]["card"]["claims"]
    assert summary["marks"] == []
    one = card_reviews.review_summary(learner, card_set_id=set_id, dsn=dsn)
    assert one["marks"] == [
        {"card_set_id": set_id, "concept_id": ids[0], "rating": "forgot", "review_count": 1},
        {"card_set_id": set_id, "concept_id": ids[1], "rating": "forgot", "review_count": 1},
    ]

    # 標成熟悉就離開需要加強清單。
    second = card_reviews.submit_review(learner, set_id, ids[0], "remembered", 1, "r4", dsn=dsn, now=now + timedelta(days=1))
    assert second["review_count"] == 2
    later = card_reviews.review_summary(learner, dsn=dsn)
    assert [c["card"]["concept_id"] for c in later["cards"]] == [ids[1]]
    assert later["sets"][0]["remembered_count"] == 1
    with psycopg.connect(dsn) as db:
        assert db.execute("SELECT count(*) FROM card_review_events WHERE card_set_id=%s", (set_id,)).fetchone() == (3,)
        assert db.execute("SELECT concept_id, review_count, last_rating FROM card_review_states ORDER BY concept_id").fetchall() == sorted(
            [(ids[0], 2, "remembered"), (ids[1], 1, "forgot")])

    with pytest.raises(card_sets.CardSetError, match="^RESOURCE_NOT_FOUND$"):
        card_reviews.submit_review(learner, set_id, f"concept:sha256:{'f' * 64}", "remembered", 0, "r5", dsn=dsn)
    with pytest.raises(card_sets.CardSetError, match="^REQUEST_INVALID$"):
        card_reviews.submit_review(learner, set_id, ids[0], "easy", 2, "r6", dsn=dsn)
    # 只剩不熟／熟悉兩種標記。
    with pytest.raises(card_sets.CardSetError, match="^REQUEST_INVALID$"):
        card_reviews.submit_review(learner, set_id, ids[0], "fuzzy", 2, "r7", dsn=dsn)


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
    card_reviews.submit_review(learner, set_id, ids[0], "forgot", 0, "k1", dsn=dsn)
    # 移出卡組後不再列出；加回來沿用原本標記。
    card_sets.update_card_set(learner, set_id, "複習", 1, ids[1:], dsn=dsn)
    with database_session(dsn) as db:
        db.get(CardSet, set_id).status = "ready"
    summary = card_reviews.review_summary(learner, dsn=dsn)
    assert summary["cards"] == [] and summary["sets"][0]["forgot_count"] == 0
    with pytest.raises(card_sets.CardSetError, match="^RESOURCE_NOT_FOUND$"):
        card_reviews.submit_review(learner, set_id, ids[0], "remembered", 1, "k2", dsn=dsn)
    card_sets.update_card_set(learner, set_id, "複習", 2, ids, dsn=dsn)
    with database_session(dsn) as db:
        db.get(CardSet, set_id).status = "ready"
    assert card_reviews.review_summary(learner, dsn=dsn)["sets"][0]["forgot_count"] == 1
    # 生成中的卡組不列出。
    with database_session(dsn) as db:
        db.get(CardSet, set_id).status = "pending"
    assert card_reviews.review_summary(learner, dsn=dsn)["sets"] == []
    with database_session(dsn) as db:
        db.get(CardSet, set_id).status = "ready"

    card_sets.delete_card_set(learner, set_id, dsn=dsn)
    assert card_reviews.review_summary(learner, dsn=dsn)["cards"] == []
    other_set, _ = ready_set(two_concepts, key="second")
    card_reviews.submit_review(learner, other_set, ids[0], "remembered", 0, "k3", dsn=dsn)
    assert request_material_discard(learner, source.material_id, dsn=dsn) == "removed"
    with psycopg.connect(dsn) as db:
        # 教材清除實際刪除卡組時，標記與事件一併刪除。
        assert db.execute("SELECT count(*) FROM card_review_states").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM card_review_events").fetchone() == (0,)
    assert card_reviews.review_summary(learner, dsn=dsn)["sets"] == []


def test_api_boundaries_and_owner_isolation(two_concepts, tmp_path, monkeypatch):
    owner, _, _, _, dsn, token = two_concepts
    set_id, ids = ready_set(two_concepts)
    client = TestClient(_app(dsn, tmp_path, monkeypatch), base_url=ORIGIN)
    url = f"/v1/card-sets/{set_id}/reviews"
    body = {"schema": "card-review-submit/v1", "concept_id": ids[0], "rating": "remembered", "expected_review_count": 0}
    keyed = {**HEADERS, "Idempotency-Key": "api-1"}
    assert client.post(url, headers=keyed, json=body).status_code == 401
    assert client.get("/v1/card-reviews").status_code == 401
    client.cookies.set("studydy_session", token)
    assert client.post(url, json=body, headers={"Idempotency-Key": "api-1"}).status_code == 403
    assert client.post(url, headers=HEADERS, json=body).status_code == 400
    for change in [{"rating": "easy"}, {"rating": "fuzzy"}, {"expected_review_count": -1}, {"concept_id": "concept:bad"}, {"schema": "x"}, {"extra": 1}]:
        assert client.post(url, headers=keyed, json={**body, **change}).status_code == 400, change
    saved = client.post(url, headers=keyed, json=body)
    assert saved.status_code == 200, saved.json()
    assert saved.json()["schema"] == "card-review/v2" and saved.json()["review_count"] == 1
    assert client.post(url, headers=keyed, json=body).json() == saved.json()
    conflict = client.post(url, headers={**HEADERS, "Idempotency-Key": "api-2"}, json=body)
    assert conflict.status_code == 409 and conflict.json()["reason_code"] == "CARD_REVIEW_CONFLICT"
    summary = client.get("/v1/card-reviews", params={"card_set_id": str(set_id)})
    assert summary.status_code == 200, summary.json()
    assert summary.json()["schema"] == "card-reviews/v2"
    assert [mark["rating"] for mark in summary.json()["marks"]] == ["remembered"]
    assert client.get("/v1/card-reviews", params={"card_set_id": "nope"}).status_code == 400
    assert client.get("/v1/card-reviews", params={"limit": "5"}).status_code == 400

    other = register_account(f"other_{uuid4().hex[:8]}@example.com", "Synthetic test password 42", dsn=dsn)
    foreign = TestClient(_app(dsn, tmp_path, monkeypatch), base_url=ORIGIN)
    foreign.cookies.set("studydy_session", other.raw_token)
    assert foreign.get("/v1/card-reviews").json()["sets"] == []
    assert foreign.get("/v1/card-reviews", params={"card_set_id": str(set_id)}).status_code == 404
    stolen = foreign.post(url, headers={**HEADERS, "Idempotency-Key": "x"}, json={**body, "expected_review_count": 1})
    assert stolen.status_code == 404
