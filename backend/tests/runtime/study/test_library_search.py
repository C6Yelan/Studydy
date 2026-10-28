"""全站搜尋：只查自己未刪除教材的目前版本，依名稱相符程度排序。"""
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text

from assessment_fixtures import concept_fixture
from product_fixtures import ORIGIN, _app, closed_loop
from runtime.learner_session import register_account
from runtime.storage import knowledge_reads
from runtime.storage.database import database_session


def _client(fixture, tmp_path, monkeypatch, token=None):
    client = TestClient(_app(fixture["dsn"], tmp_path, monkeypatch), base_url=ORIGIN)
    client.cookies.set("studydy_session", token or fixture["token"])
    return client


def test_library_search_ranks_concepts_and_stays_owner_scoped(closed_loop, tmp_path, monkeypatch):
    fixture = concept_fixture(closed_loop, 3)
    mid, rev = fixture["source"].material_id, fixture["document"]["revision"]
    client = _client(fixture, tmp_path, monkeypatch)

    body = client.get("/v1/search", params={"q": "  topic "}).json()
    assert body["schema"] == "library-search/v1" and body["query"] == "topic"
    other = next(c for c in fixture["document"]["concepts"] if c["label"] == "Other topic")
    assert [c["concept_id"] for c in body["concepts"]] == [other["concept_id"]]
    hit = body["concepts"][0]
    assert (hit["material_id"], hit["knowledge_structure_revision"], hit["run_id"]) == (
        str(mid), rev, fixture["document"]["run_id"])
    assert hit["label"] == "Other topic" and hit["claim_count"] == 1
    assert body["concepts_truncated"] is False

    # 完全相符排在部分相符之前。
    with database_session(fixture["dsn"]) as db:
        ranked = knowledge_reads.search_library(db, fixture["learner"].learner_id, "signals")
    assert [c["label"] for c in ranked["concepts"]][0] == "Signals"
    assert client.get("/v1/search", params={"q": "%"}).json()["concepts"] == []
    assert client.get("/v1/search", params={"q": "_"}).json()["concepts"] == []

    with database_session(fixture["dsn"]) as db:
        name = db.execute(text("SELECT display_name FROM materials WHERE material_id=:m"), {"m": mid}).scalar_one()
    found = client.get("/v1/search", params={"q": name[:6]}).json()["materials"]
    assert {m["material_id"] for m in found} >= {str(mid)}
    assert next(m for m in found if m["material_id"] == str(mid))["knowledge_structure_revision"] == rev

    monkeypatch.setattr(knowledge_reads, "LIBRARY_SEARCH_CONCEPTS", 1)
    capped = client.get("/v1/search", params={"q": "s"}).json()
    assert len(capped["concepts"]) == 1 and capped["concepts_truncated"] is True

    stranger = register_account(f"other_{uuid4().hex[:8]}@example.com", "Synthetic test password 42", dsn=fixture["dsn"])
    foreign = _client(fixture, tmp_path, monkeypatch, token=stranger.raw_token).get("/v1/search", params={"q": "topic"}).json()
    assert foreign["concepts"] == [] and foreign["materials"] == []

    # 刪除中的教材不再出現。
    with database_session(fixture["dsn"]) as db:
        db.execute(text("UPDATE materials SET discard_requested_at = now() WHERE material_id = :m"), {"m": mid})
    assert client.get("/v1/search", params={"q": "topic"}).json()["concepts"] == []

    assert client.get("/v1/search", params={"q": "   "}).status_code == 400
    assert client.get("/v1/search").status_code == 400
    assert client.get("/v1/search", params={"q": "x" * 201}).status_code == 400
    assert client.get("/v1/search", params={"q": "a", "learner_id": str(uuid4())}).status_code == 400
    client.cookies.clear()
    assert client.get("/v1/search", params={"q": "topic"}).status_code == 401


def test_library_search_pages_all_categories_with_exact_totals(closed_loop, tmp_path, monkeypatch):
    from copy import deepcopy
    from hashlib import sha256
    from runtime import card_sets, podcasts

    fixture = concept_fixture(closed_loop, 3)
    owner, source, document, dsn = fixture["learner"], fixture["source"], fixture["document"], fixture["dsn"]
    client = _client(fixture, tmp_path, monkeypatch)
    with database_session(dsn) as db:
        db.execute(text("UPDATE materials SET display_name='搜尋教材' WHERE material_id=:m"), {"m": source.material_id})
        db.execute(text("""INSERT INTO materials
            (material_id, learner_id, display_name, upload_idempotency_key_sha256, upload_request_fingerprint, created_at)
            VALUES (:id, :owner, '搜尋教材', :key, :key, now())"""),
            [{"id": uuid4(), "owner": owner.learner_id, "key": sha256(str(i).encode()).digest()} for i in range(34)])
        outline = db.execute(text("SELECT outline FROM knowledge_read_indexes WHERE material_id=:m AND structure_revision=:r"),
                             {"m": source.material_id, "r": document["revision"]}).scalar_one()
        concept = outline["concepts"][0]
        outline = {**outline, "concepts": [{**deepcopy(concept), "concept_id": "concept:sha256:" + sha256(str(i).encode()).hexdigest(),
                                          "label": f"搜尋概念 {i}"} for i in range(35)]}
        import json
        db.execute(text("UPDATE knowledge_read_indexes SET outline=CAST(:outline AS jsonb) WHERE material_id=:m AND structure_revision=:r"),
                   {"outline": json.dumps(outline), "m": source.material_id, "r": document["revision"]})
    selected = [document["concepts"][0]["concept_id"]]
    for i in range(36):
        card = card_sets.create_card_set(owner.learner_id, source.material_id, document["revision"], f"搜尋卡組 {i}", selected, str(i), dsn=dsn)
        podcast = podcasts.create_podcast(owner.learner_id, source.material_id, document["revision"], f"搜尋節目 {i}", selected, str(i), delivery="solo", dsn=dsn)
    card_sets.delete_card_set(owner.learner_id, card["card_set_id"], dsn=dsn)
    podcasts.delete_podcast(owner.learner_id, podcast["podcast_id"], dsn=dsn)
    categories = {"materials": "material_id", "concepts": "concept_id", "card_sets": "card_set_id", "podcasts": "podcast_id"}
    legacy = client.get("/v1/search", params={"q": "搜尋"}).json()
    assert len(legacy["materials"]) == 10 and len(legacy["concepts"]) == 30
    first = client.get("/v1/search", params={"q": "搜尋", "category": "all", "limit": 30}).json()
    assert first["totals"] == {category: 35 for category in categories}
    assert all(len(first[category]) == 30 for category in categories)
    for category, identity in categories.items():
        pages = [client.get("/v1/search", params={"q": "搜尋", "category": category, "offset": offset, "limit": 30}).json()
                 for offset in (0, 30, 60)]
        assert [len(page[category]) for page in pages] == [30, 5, 0]
        assert all(page["totals"] == first["totals"] for page in pages)
        assert len({item[identity] for page in pages for item in page[category]}) == 35
        assert all(not page[other] for page in pages for other in categories if other != category)
    for params in ({"category": "bad"}, {"offset": -1}, {"limit": 0}, {"limit": 31}):
        assert client.get("/v1/search", params={"q": "搜尋", **params}).status_code == 400
    stranger = register_account(f"pages_{uuid4().hex[:8]}@example.com", "Synthetic test password 42", dsn=dsn)
    foreign = _client(fixture, tmp_path, monkeypatch, token=stranger.raw_token).get("/v1/search", params={"q": "搜尋", "category": "all"}).json()
    assert foreign["totals"] == {category: 0 for category in categories}
    with database_session(dsn) as db:
        db.execute(text("UPDATE materials SET discard_requested_at=now() WHERE material_id=:m"), {"m": source.material_id})
    hidden = client.get("/v1/search", params={"q": "搜尋", "category": "all"}).json()
    assert hidden["totals"] == {"materials": 34, "concepts": 0, "card_sets": 0, "podcasts": 0}
