"""卡組的真 API／DB、來源保留與刪除回歸；只使用合成教材。"""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import runtime.api.app as api
from product_fixtures import closed_loop, seed_run, _structure, publish_fixture_structure
from runtime import card_sets
from runtime.learner_session import register_account
from runtime.material_discard import request_material_discard
from runtime.material_processing import claim_next_material_processing_run, _record_progress
from runtime.storage.knowledge_structures import _prune_unreferenced_structures, read_knowledge_structure, KnowledgeStructureStoreError
from runtime.storage.tables import Material, database_session


def create(fixture, key="cards", name="概念複習", ids=None):
    learner, source, _, document, dsn, _ = fixture
    return card_sets.create_card_set(learner.learner_id, source.material_id, document["revision"], name,
                                     ids if ids is not None else [c["concept_id"] for c in document["concepts"]], key, dsn=dsn)


def test_save_reopen_preserves_exact_claims_and_does_not_create_learning_state(closed_loop):
    owner, source, _, document, dsn, _ = closed_loop
    first = create(closed_loop)
    second = create(closed_loop, key="second", name="考前再看")
    saved = card_sets.read_card_set(owner.learner_id, first["card_set_id"], dsn=dsn)
    expected = read_knowledge_structure(owner.learner_id, source.material_id, revision=document["revision"], dsn=dsn).view
    assert saved["cards"] == [{key: c[key] for key in ("concept_id", "label", "claims")} for c in expected["concepts"]]
    assert saved["source_resolver"] == expected["source_resolver"]
    assert saved["status"] == expected["status"] and "relations" not in saved
    assert {s["card_set_id"] for s in card_sets.list_card_sets(owner.learner_id, dsn=dsn)["card_sets"]} == {first["card_set_id"], second["card_set_id"]}
    with psycopg.connect(dsn) as db:
        assert db.execute("SELECT count(*) FROM study_sessions").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM answer_events").fetchone() == (0,)


def test_concurrent_replay_conflict_and_delete_cannot_resurrect(closed_loop):
    owner, _, _, _, dsn, _ = closed_loop
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: create(closed_loop), range(2)))
    assert results[0] == results[1]
    with pytest.raises(card_sets.CardSetError, match="IDEMPOTENCY_CONFLICT"):
        create(closed_loop, name="不同意圖")
    identity = results[0]["card_set_id"]
    assert card_sets.delete_card_set(owner.learner_id, identity, dsn=dsn) == card_sets.delete_card_set(owner.learner_id, identity, dsn=dsn)
    assert card_sets.list_card_sets(owner.learner_id, dsn=dsn)["card_sets"] == []
    with pytest.raises(card_sets.CardSetError, match="RESOURCE_NOT_FOUND"):
        create(closed_loop)
    with pytest.raises(card_sets.CardSetError, match="RESOURCE_NOT_FOUND"):
        card_sets.read_card_set(owner.learner_id, identity, dsn=dsn)


@pytest.mark.parametrize("name,ids", [(" ", None), ("x\n", None), ("x" * 201, None), ("卡組", []), ("卡組", ["concept:sha256:" + "0" * 64])])
def test_invalid_selection_or_name_does_not_save(closed_loop, name, ids):
    with pytest.raises(card_sets.CardSetError, match="REQUEST_INVALID"):
        create(closed_loop, name=name, ids=ids)


def test_last_card_set_releases_old_revision_but_never_deletes_material(closed_loop):
    owner, source, settings, original, dsn, _ = closed_loop
    first = create(closed_loop)
    second = create(closed_loop, key="second")
    run = seed_run(owner.learner_id, source.material_id, "updated", settings, dsn=dsn)
    assert claim_next_material_processing_run(dsn=dsn).run.run_id == run.run_id
    for stage in ("evidence", "semantics", "publishing"):
        _record_progress(run.run_id, stage, 1, 1, dsn=dsn)
    updated = _structure(str(run.run_id), source.sha256, settings["runtime_lock"], partial=True)
    publish_fixture_structure(owner.learner_id, source.material_id, run.run_id, updated, dsn=dsn)
    with database_session(dsn) as db:
        material = db.scalar(select(Material).where(Material.material_id == source.material_id).with_for_update())
        _prune_unreferenced_structures(db, owner.learner_id, source.material_id, material.head_revision)
    assert not card_sets.read_card_set(owner.learner_id, first["card_set_id"], dsn=dsn)["is_current_revision"]
    card_sets.delete_card_set(owner.learner_id, first["card_set_id"], dsn=dsn)
    assert card_sets.read_card_set(owner.learner_id, second["card_set_id"], dsn=dsn)["knowledge_structure_revision"] == original["revision"]
    card_sets.delete_card_set(owner.learner_id, second["card_set_id"], dsn=dsn)
    with pytest.raises(KnowledgeStructureStoreError, match="KNOWLEDGE_STRUCTURE_UNAVAILABLE"):
        read_knowledge_structure(owner.learner_id, source.material_id, revision=original["revision"], dsn=dsn)
    assert read_knowledge_structure(owner.learner_id, source.material_id, revision=updated["revision"], dsn=dsn)
    with psycopg.connect(dsn) as db:
        assert db.execute("SELECT count(*) FROM materials").fetchone() == (1,)
        assert db.execute("SELECT count(*) FROM artifacts").fetchone()[0] > 0


def test_discard_synthetic_material_cleans_sets_and_blocks_replay(closed_loop):
    owner, source, _, _, dsn, _ = closed_loop
    saved = create(closed_loop)
    assert request_material_discard(owner.learner_id, source.material_id, dsn=dsn) == "removed"
    assert card_sets.list_card_sets(owner.learner_id, dsn=dsn)["card_sets"] == []
    with pytest.raises(card_sets.CardSetError, match="RESOURCE_NOT_FOUND"):
        create(closed_loop)
    with psycopg.connect(dsn) as db:
        assert db.execute("SELECT count(*) FROM card_sets WHERE card_set_id=%s", (saved["card_set_id"],)).fetchone() == (0,)


def test_api_auth_provenance_validation_and_owner_isolation(closed_loop, monkeypatch):
    owner, source, settings, document, dsn, token = closed_loop
    monkeypatch.setattr(api, "runtime_binding", lambda _: {})
    origin = "http://127.0.0.1:4183"
    app = api.create_app(api.ApiSettings(profile="local", public_origin=origin, secure_cookie=False, local_config=settings, dsn=dsn))
    client = TestClient(app, base_url=origin)
    url = f"/v1/materials/{source.material_id}/card-sets"
    headers = {"Origin": origin, "Idempotency-Key": "api-create"}
    body = {"schema": "card-set-create/v1", "knowledge_structure_revision": document["revision"], "name": "複習", "concept_ids": [c["concept_id"] for c in document["concepts"]]}
    assert client.post(url, headers=headers, json=body).status_code == 401
    client.cookies.set("studydy_session", token)
    assert client.post(url, json=body).status_code == 403
    assert client.post(url, headers={"Origin": origin}, json=body).status_code == 400
    assert client.post(url, headers=headers, json={**body, "learner_id": str(owner.learner_id)}).status_code == 400
    assert client.post(url, headers=headers, json={**body, "concept_ids": body["concept_ids"] * 2}).status_code == 400
    response = client.post(url, headers=headers, json=body)
    assert response.status_code == 201
    saved = response.json()
    detail = f"/v1/card-sets/{saved['card_set_id']}"
    edit = {"schema": "card-set-update/v1", "name": "重新命名", "expected_version": saved["version"]}
    assert client.post(detail + "/update", json=edit).status_code == 403
    assert client.post(detail + "/update", headers={"Origin": origin}, json={**edit, "concept_ids": []}).status_code == 400
    view = client.get(detail)
    assert view.status_code == 200 and view.headers["cache-control"] == "private, no-store"
    evidence = view.json()["cards"][0]["claims"][0]["evidence"][0]
    resolved = client.get(view.json()["source_resolver"] + f"/{evidence['evidence_id']}/source")
    assert resolved.status_code == 200 and resolved.json()["normalized_page"] == evidence["normalized_page"]
    assert client.post(url, headers=headers, json=body).json() == saved
    assert client.get(detail + "?owner=x").status_code == 400
    stranger = register_account("cards-other@example.com", "Synthetic other password 42", dsn=dsn)
    client.cookies.set("studydy_session", stranger.raw_token)
    assert client.get("/v1/card-sets").json()["card_sets"] == []
    assert client.get(detail).status_code == 404
    assert client.post(detail + "/update", headers={"Origin": origin}, json=edit).status_code == 404
    assert client.delete(detail, headers={"Origin": origin}).status_code == 404
    assert client.post(url, headers=headers, json=body).status_code == 404
    client.cookies.set("studydy_session", token)
    updated = client.post(detail + "/update", headers={"Origin": origin}, json=edit)
    assert updated.status_code == 200 and updated.json()["version"] == 2
    assert client.post(detail + "/update", headers={"Origin": origin}, json=edit).json() == updated.json()
    assert client.post(detail + "/update", headers={"Origin": origin}, json={**edit, "name": "舊頁面覆蓋"}).status_code == 409
    assert client.request("DELETE", detail, headers={"Origin": origin}, json={}).status_code == 400
    assert client.delete(detail, headers={"Origin": origin}).status_code == 200
    assert client.get(detail).status_code == 404
    assert client.post(url, headers=headers, json=body).status_code == 404
    operation = app.openapi()["paths"]["/v1/materials/{material_id}/card-sets"]["post"]
    assert any(p["name"] == "Idempotency-Key" and p["required"] for p in operation["parameters"])


def test_edit_selection_stays_on_original_revision_and_deleted_set_cannot_be_edited(closed_loop):
    owner, source, _, document, dsn, _ = closed_loop
    saved = create(closed_loop)
    original = card_sets.read_card_set(owner.learner_id, saved["card_set_id"], dsn=dsn)
    identity = saved["card_set_id"]
    with pytest.raises(card_sets.CardSetError, match="REQUEST_INVALID"):
        card_sets.update_card_set(owner.learner_id, identity, "編輯", 1, ["concept:sha256:" + "f" * 64], dsn=dsn)
    updated = card_sets.update_card_set(owner.learner_id, identity, "編輯", 1,
                                       [c["concept_id"] for c in original["cards"]], dsn=dsn)
    assert updated["version"] == 2 and updated["knowledge_structure_revision"] == document["revision"]
    assert card_sets.read_card_set(owner.learner_id, identity, dsn=dsn)["cards"] == original["cards"]
    card_sets.delete_card_set(owner.learner_id, identity, dsn=dsn)
    with pytest.raises(card_sets.CardSetError, match="RESOURCE_NOT_FOUND"):
        card_sets.update_card_set(owner.learner_id, identity, "編輯", 2, dsn=dsn)


def test_add_remove_concepts_and_concurrent_edit_conflict(closed_loop):
    owner, source, settings, document, dsn, _ = closed_loop
    old = create(closed_loop)
    run = seed_run(owner.learner_id, source.material_id, "two-concepts", settings, dsn=dsn)
    assert claim_next_material_processing_run(dsn=dsn).run.run_id == run.run_id
    for stage in ("evidence", "semantics", "publishing"):
        _record_progress(run.run_id, stage, 1, 1, dsn=dsn)
    candidate = deepcopy(document)
    candidate.pop("input_binding")
    other = deepcopy(candidate["concepts"][0])
    other.update(concept_id="concept:sha256:" + "f" * 64, label="LIFO")
    candidate["concepts"].append(other)
    publish_fixture_structure(owner.learner_id, source.material_id, run.run_id, candidate, dsn=dsn)
    ids = [c["concept_id"] for c in candidate["concepts"]]
    with pytest.raises(card_sets.CardSetError, match="REQUEST_INVALID"):
        card_sets.update_card_set(owner.learner_id, old["card_set_id"], "舊版本", 1, ids, dsn=dsn)
    saved = card_sets.create_card_set(owner.learner_id, source.material_id, candidate["revision"], "編輯", ids[:1], "two", dsn=dsn)
    identity = saved["card_set_id"]
    updated = card_sets.update_card_set(owner.learner_id, identity, "兩張", 1, ids, dsn=dsn)
    assert updated["card_count"] == 2 and updated["version"] == 2
    assert card_sets.update_card_set(owner.learner_id, identity, "兩張", 1, ids, dsn=dsn) == updated
    def edit(name):
        try:
            return card_sets.update_card_set(owner.learner_id, identity, name, 2, ids[1:], dsn=dsn)
        except card_sets.CardSetError as error:
            return str(error)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(edit, ["第一頁", "第二頁"]))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert "CARD_SET_CONFLICT" in results
    final = card_sets.read_card_set(owner.learner_id, identity, dsn=dsn)
    assert final["card_count"] == 1 and final["version"] == 3
    assert final["cards"][0]["concept_id"] == ids[1]
    # 推薦順序可與教材原始排列不同；建立、編輯及重開都必須保持確認後的順序。
    reversed_ids = list(reversed(ids))
    ordered = card_sets.create_card_set(owner.learner_id, source.material_id, candidate["revision"], "推薦順序", reversed_ids, "ordered", dsn=dsn)
    assert [c["concept_id"] for c in card_sets.read_card_set(owner.learner_id, ordered["card_set_id"], dsn=dsn)["cards"]] == reversed_ids
    card_sets.update_card_set(owner.learner_id, identity, "推薦順序", 3, reversed_ids, dsn=dsn)
    assert [c["concept_id"] for c in card_sets.read_card_set(owner.learner_id, identity, dsn=dsn)["cards"]] == reversed_ids
