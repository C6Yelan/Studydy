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
