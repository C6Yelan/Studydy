"""真 API／隔離 DB：圖卡的來源身分、所有權與 learning authority。"""
from uuid import uuid4

from fastapi.testclient import TestClient

from assessment_fixtures import concept_fixture
from learning_adaptation.learner_progress import derive_learner_progress
from product_fixtures import ORIGIN, _app, closed_loop, library_materials, product_snapshot


def test_cards_exact_revision_owner_source_and_no_session(library_materials, tmp_path, monkeypatch):
    f = library_materials
    client = TestClient(_app(f["dsn"], tmp_path, monkeypatch), base_url=ORIGIN)
    material = str(f["first"].material_id)
    document = f["structure"]
    revision = document["revision"]
    concept = document["concepts"][0]["concept_id"]
    base = f"/v1/materials/{material}/knowledge-structures/{revision}"
    path = base + "/concept-cards"
    assert client.get(path, params={"concept_id": concept}).status_code == 401
    client.cookies.set("studydy_session", f["token"])
    before = product_snapshot(f["dsn"])
    assert before["study_sessions"][0] == 0
    response = client.get(path, params=[("concept_id", concept), ("concept_id", concept)])
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    cards = response.json()
    assert cards["selection"] == {
        "material_id": material, "content_material_id": document["material_id"],
        "knowledge_structure_revision": revision, "concept_ids": [concept],
        "claim_ids": [claim["claim_id"] for claim in document["concepts"][0]["claims"]],
        "relation_ids": [], "policy": "manual-published-order/v1",
    }
    assert cards["cards"] == client.get(base).json()["concepts"]
    assert cards["status"] == document["status"]
    # 舊版仍可用時固定舊版，不能換成目前 head。
    assert revision != f["second_structure"]["revision"]
    evidence = cards["cards"][0]["claims"][0]["evidence"][0]
    source_path = cards["source_resolver"] + "/" + evidence["evidence_id"] + "/source"
    resolved = client.get(source_path)
    assert resolved.status_code == 200
    assert resolved.json()["normalized_page"] == evidence["normalized_page"]
    assert resolved.json()["accuracy"] == "exact"
    for params in ({}, {"concept_id": "unknown"}, {"concept_id": concept, "learner_id": str(f["foreign"].learner_id)}):
        assert client.get(path, params=params).status_code == 400
    missing = base.replace(revision, "knowledge-structure:sha256:" + "f" * 64) + "/concept-cards"
    assert client.get(missing, params={"concept_id": concept}).status_code == 404
    mismatched = path.replace(material, str(f["uploaded"].material_id))
    assert client.get(mismatched, params={"concept_id": concept}).status_code == 404
    assert client.get(path.replace(material, str(uuid4())), params={"concept_id": concept}).status_code == 404
    client.cookies.clear()
    client.cookies.set("studydy_session", f["foreign"].raw_token)
    assert client.get(path, params={"concept_id": concept}).status_code == 404
    assert client.get(source_path).status_code == 404
    assert product_snapshot(f["dsn"]) == before


def test_cards_with_existing_progress_preserve_all_learning_rows(closed_loop, tmp_path, monkeypatch):
    f = concept_fixture(closed_loop, source_review_required=True)
    client = TestClient(_app(f["dsn"], tmp_path, monkeypatch), base_url=ORIGIN)
    client.cookies.set("studydy_session", f["token"])
    before = product_snapshot(f["dsn"])
    progress = derive_learner_progress(f["learner"], f["study"].study_session_id, dsn=f["dsn"])
    path = f"/v1/materials/{f['source'].material_id}/knowledge-structures/{f['document']['revision']}/concept-cards"
    ids = [c["concept_id"] for c in f["document"]["concepts"]]
    result = client.get(path, params=[("concept_id", id) for id in ids[::-1]])
    assert result.status_code == 200
    cards = result.json()
    assert cards["selection"]["concept_ids"] == ids
    assert cards["status"]["quality"] == "needs_review"
    assert cards["relations"] == []
    assert len(cards["cards"][0]["claims"]) == 3
    for card in cards["cards"]:
        for claim in card["claims"]:
            for evidence in claim["evidence"]:
                assert client.get(cards["source_resolver"] + "/" + evidence["evidence_id"] + "/source").status_code == 200
    assert derive_learner_progress(f["learner"], f["study"].study_session_id, dsn=f["dsn"]) == progress
    assert product_snapshot(f["dsn"]) == before
