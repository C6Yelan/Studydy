"""教材新版承接的真 DB／API：預覽、確認、冪等重送、無法對應、舊版保留、帳號隔離與 XP 不重算。"""
from copy import deepcopy
from uuid import uuid4

import psycopg
import pymupdf
import pytest
from fastapi.testclient import TestClient

from assessment_fixtures import answer, concept_fixture, create, finish
from product_fixtures import HEADERS, ORIGIN, _app, closed_loop, publish_fixture_structure
from learning_adaptation.learner_progress import derive_learner_progress
from runtime import card_generation, card_reviews, card_sets, version_moves
from runtime.learner_session import TrustedLearner, register_account
from runtime.material_processing import _record_progress, claim_next_material_processing_run
from runtime.source_normalization import normalize_next, read_sources, upload_source
from runtime.source_revisions import create_revision
from runtime.storage.database import database_session
from runtime.learning_activity import learning_activity, learning_rhythm
from runtime.storage.materials import MaterialLibraryError


def new_revision(fixture, mutation):
    """以追加來源產生新版本；mutation 改寫舊概念，模擬新版改寫的結果。"""
    owner, material, dsn = fixture["learner"].learner_id, fixture["source"].material_id, fixture["dsn"]
    old = fixture["document"]
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((72, 72), "Extra topic uses NEW.")
        payload = pdf.tobytes()
    source_id = upload_source(owner, material, payload, "Additional.pdf", "application/pdf", str(uuid4()), dsn=dsn)
    assert normalize_next(dsn=dsn)
    source = next(s for s in read_sources(owner, material, dsn=dsn)["sources"] if s["source_id"] == source_id)
    run = create_revision(owner, material, [source["normalization_id"]], str(uuid4()),
                          fixture["settings"], base_revision=old["revision"], dsn=dsn)
    assert claim_next_material_processing_run(dsn=dsn).run.run_id == run.run_id
    document = deepcopy(old)
    document["page_count"] += 1
    evidence = deepcopy(document["evidence"][0])
    evidence.update(evidence_id="extra-evidence", exact_text="Extra topic uses NEW.",
                    page=document["page_count"], block_order=0)
    evidence["source_locator"]["page"] = document["page_count"]
    document["evidence"].append(evidence)
    document["concepts"].append({"concept_id": "extra-concept", "label": "Extra topic", "aliases": [], "claims": [{
        "claim_id": "extra-claim", "text": evidence["exact_text"], "projection": "semantic_meaning",
        "evidence_refs": [evidence["evidence_id"]],
        "source_spans": [{"evidence_id": evidence["evidence_id"], "quote": evidence["exact_text"]}],
    }]})
    signals = next(c for c in document["concepts"] if c["label"] == "Signals")
    if mutation == "rewrite":
        # 新版改寫名稱與重點文字，原文區塊不變。
        signals["label"] = "Signal codes"
        for index, claim in enumerate(signals["claims"]):
            claim["text"] = f"Signal {index} is sent with code{index}."
    elif mutation == "split":
        first, second = deepcopy(signals), deepcopy(signals)
        first.update(concept_id="first-signal", label="First signal", claims=signals["claims"][:1])
        second.update(concept_id="second-signal", label="Second signal", claims=signals["claims"][1:])
        document["concepts"] = [c for c in document["concepts"] if c is not signals] + [first, second]
    for stage in ("evidence", "semantics", "publishing"):
        _record_progress(run.run_id, stage, document["page_count"], document["page_count"], dsn=dsn)
    publish_fixture_structure(owner, material, run.run_id, document, dsn=dsn)
    return document


def card_response(path, body):
    """合成卡面設計與核對回應（同 materials/test_card_generation.py），不呼叫模型。"""
    assert path == "/semantics" and body["task"] == "concept_card"
    if "pages" not in body["schema"]["properties"]:
        return {"supported": True, "core_preserved": True, "issues": []}
    refs = [item["ref"] for item in body["request"]["sources"] if item["kind"] == "claim"]
    return {"pages": [{"title": "訊號代碼", "layout": "focus",
        "takeaway": {"text": "每個訊號使用自己的代碼。", "source_refs": refs},
        "blocks": [{"heading": "代碼", "text": "依訊號選用代碼。", "icon": "list-check", "source_refs": refs}],
        "links": []}], "details_refs": []}


def finish_cards(dsn, calls):
    def call(path, body):
        calls.append(body["request"]["sources"][0]["text"])
        return card_response(path, body)
    for _ in range(20):
        if not card_generation.step(dsn=dsn, call=call):
            return
    raise AssertionError("card generation did not settle")


def learned(closed_loop):
    """舊版：Signals 已完成、兩張卡都已生成，Signals 標記過一次。"""
    fixture = concept_fixture(closed_loop, 2)
    owner, dsn = fixture["learner"].learner_id, fixture["dsn"]
    root = create(fixture)
    finish(fixture)
    answer(fixture, root)
    ids = [c["concept_id"] for c in fixture["document"]["concepts"]]
    old_set = card_sets.create_card_set(owner, fixture["source"].material_id, fixture["document"]["revision"],
                                        "訊號卡", ids, "cards", dsn=dsn)["card_set_id"]
    finish_cards(dsn, [])
    signals = fixture["concept"]["concept_id"]
    card_reviews.submit_review(owner, old_set, signals, "remembered", 0, "old-review", dsn=dsn)
    return fixture, old_set


def activity(fixture):
    with database_session(fixture["dsn"]) as db:
        owner = fixture["learner"].learner_id
        # 本週學習節奏與學習活動一起比對。
        return {**learning_activity(db, owner), "week": learning_rhythm(db, owner, period="week")["days"]}


def snapshot(fixture, old_set):
    with psycopg.connect(fixture["dsn"]) as db:
        return {
            "set": db.execute("SELECT knowledge_structure_revision, concept_ids, version, status, generation "
                              "FROM card_sets WHERE card_set_id=%s", (old_set,)).fetchone(),
            "states": db.execute("SELECT * FROM card_review_states WHERE card_set_id=%s ORDER BY concept_id",
                                 (old_set,)).fetchall(),
            "events": db.execute("SELECT count(*) FROM card_review_events").fetchone(),
            "answers": db.execute("SELECT count(*) FROM answer_events").fetchone(),
            "study": db.execute("SELECT * FROM study_sessions WHERE study_session_id=%s",
                                (fixture["study"].study_session_id,)).fetchone(),
        }


def test_preview_then_move_cards_and_progress_without_touching_the_old_version(closed_loop):
    fixture, old_set = learned(closed_loop)
    learner, dsn = fixture["learner"], fixture["dsn"]
    owner, material = learner.learner_id, fixture["source"].material_id
    assert version_moves.preview_version_move(learner, material, dsn=dsn)["card_sets"] == []
    document = new_revision(fixture, "rewrite")
    head = document["revision"]
    before_xp = activity(fixture)
    before = snapshot(fixture, old_set)
    old_progress = derive_learner_progress(learner, fixture["study"].study_session_id, dsn=dsn)

    preview = version_moves.preview_version_move(learner, material, dsn=dsn)
    assert preview["to_revision"] == head and preview["preview_digest"].startswith("version-move:sha256:")
    item, = preview["card_sets"]
    assert item["card_set_id"] == old_set and item["movable"] and item["unmapped"] == []
    assert {(m["from_label"], m["to_label"], m["basis"]) for m in item["mapped"]} == {
        ("Signals", "Signal codes", "evidence"), ("Other topic", "Other topic", "evidence")}
    # 只有 Signals 有標記；Other topic 的來源輸入沒變，卡面直接沿用。
    assert (item["carried_reviews"], item["reused_designs"], item["target_count"]) == (1, 1, 2)
    study = preview["study"]
    assert study["target_study_session_id"] is None and study["completed_count"] == 1
    assert [(c["label"], c["from_labels"]) for c in study["carried"]] == [("Signal codes", ["Signals"])]
    assert study["not_carried"] == [] and study["current_concept"]["label"] == "Signal codes"
    # 預覽是唯讀的。
    assert snapshot(fixture, old_set) == before

    result = version_moves.execute_version_move(learner, material, head, [old_set], True,
                                                preview["preview_digest"], "move-1", dsn=dsn)
    assert result["schema"] == "version-move/v1" and result["to_revision"] == head
    moved, = result["card_sets"]
    assert moved["from_card_set_id"] == str(old_set) and moved["status"] == "pending"
    # 重送同一個鍵取得同一結果，不會再建立卡組或學習。
    assert version_moves.execute_version_move(learner, material, head, [old_set], True,
                                              preview["preview_digest"], "move-1", dsn=dsn) == result
    with pytest.raises(card_sets.CardSetError, match="^IDEMPOTENCY_CONFLICT$"):
        version_moves.execute_version_move(learner, material, head, [], True,
                                           preview["preview_digest"], "move-1", dsn=dsn)
    with pytest.raises(card_sets.CardSetError, match="^VERSION_MOVE_STALE$"):
        version_moves.execute_version_move(learner, material, head, [old_set], True,
                                           preview["preview_digest"], "move-2", dsn=dsn)
    with psycopg.connect(dsn) as db:
        assert db.execute("SELECT count(*) FROM card_sets WHERE material_id=%s", (material,)).fetchone() == (2,)
        assert db.execute("SELECT count(*) FROM study_sessions WHERE material_id=%s", (material,)).fetchone() == (2,)
        assert db.execute("SELECT count(*) FROM material_version_moves").fetchone() == (1,)
    after = version_moves.preview_version_move(learner, material, dsn=dsn)
    assert (after["card_sets"], after["study"], after["preview_digest"]) == ([], None, None)

    # 舊版卡組、標記、事件與學習都沒有被改寫。
    assert snapshot(fixture, old_set) == before
    assert derive_learner_progress(learner, fixture["study"].study_session_id, dsn=dsn) == old_progress
    new_set = moved["card_set_id"]
    listed = {s["card_set_id"]: s for s in card_sets.list_card_sets(owner, dsn=dsn)["card_sets"]}
    assert str(listed[old_set]["moved_to_card_set_id"]) == new_set
    assert listed[old_set]["is_current_revision"] is False
    view = card_sets.read_card_set(owner, new_set, dsn=dsn)
    assert view["knowledge_structure_revision"] == head and view["is_current_revision"]
    assert [c["label"] for c in view["cards"]] == ["Signal codes", "Other topic"]
    assert view["generation"]["completed"] == 1 and view["generation"]["cached"] == 1
    signal_codes = view["cards"][0]["concept_id"]
    with psycopg.connect(dsn) as db:
        state = db.execute("SELECT review_count, last_reviewed_at, last_rating FROM card_review_states "
                           "WHERE card_set_id=%s", (new_set,)).fetchall()
        old_state = db.execute("SELECT review_count, last_reviewed_at, last_rating FROM card_review_states "
                               "WHERE card_set_id=%s", (old_set,)).fetchall()
    assert state == old_state and len(state) == 1

    # 舊卡組不再出現在標記統計；新版卡組只為改寫過的 Signals 重新製作卡面。
    assert all(s["card_set_id"] != old_set for s in card_reviews.review_summary(owner, dsn=dsn)["sets"])
    calls = []
    finish_cards(dsn, calls)
    assert len(calls) == 2 and all("code" in text for text in calls)
    summary = card_reviews.review_summary(owner, dsn=dsn)
    assert [{**s, "card_set_id": str(s["card_set_id"])} for s in summary["sets"]] == [
        {"card_set_id": new_set, "card_count": 2, "forgot_count": 0, "remembered_count": 1}]

    progress = derive_learner_progress(learner, result["study_session_id"], dsn=dsn)
    states = {s.label: s.status for s in progress.concept_states}
    assert states["Signal codes"] == "completed" and states["Other topic"] == "not_started"
    assert progress.current_concept_id == signal_codes

    # 承接不產生作答或標記事件，XP、徽章與連續天數都不變。
    moved_xp = activity(fixture)
    for key in ("xp", "badges", "streak_days", "total_answers", "week"):
        assert moved_xp[key] == before_xp[key], key
    # 同一天在新版卡組再標同一張卡：仍是同一張卡，標記張數與 XP 都不重複計算。
    card_reviews.submit_review(owner, new_set, signal_codes, "remembered", 1, "new-review", dsn=dsn)
    reviewed = activity(fixture)
    assert reviewed["xp"]["sources"]["cards"] == before_xp["xp"]["sources"]["cards"]
    assert reviewed["week"] == before_xp["week"]

    # 刪除新版卡組後，舊卡組可以再次移到新版。
    card_sets.delete_card_set(owner, new_set, dsn=dsn)
    assert activity(fixture)["xp"] == reviewed["xp"]
    again = version_moves.preview_version_move(learner, material, dsn=dsn)
    assert [s["card_set_id"] for s in again["card_sets"]] == [old_set] and again["study"] is None


def test_preview_reuses_verified_structures_but_still_reads_current_records(closed_loop, monkeypatch):
    """預覽沿用已驗證結構推導的對應；卡組與標記仍即時讀取，確認承接時重新驗證結構。"""
    fixture, old_set = learned(closed_loop)
    learner, dsn = fixture["learner"], fixture["dsn"]
    owner, material = learner.learner_id, fixture["source"].material_id
    head = new_revision(fixture, "rewrite")["revision"]
    first = version_moves.preview_version_move(learner, material, dsn=dsn)
    reads = []
    original = version_moves._read_verified_document
    monkeypatch.setattr(version_moves, "_read_verified_document",
                        lambda *args, **kwargs: reads.append(kwargs.get("lock")) or original(*args, **kwargs))
    assert version_moves.preview_version_move(learner, material, dsn=dsn) == first and reads == []
    # 之後標記另一張卡：預覽即時反映新的標記，不沿用先前結果。
    other = next(c["concept_id"] for c in fixture["document"]["concepts"]
                 if c["concept_id"] != fixture["concept"]["concept_id"])
    card_reviews.submit_review(owner, old_set, other, "remembered", 0, "later-review", dsn=dsn)
    changed = version_moves.preview_version_move(learner, material, dsn=dsn)
    assert reads == [] and first["card_sets"][0]["carried_reviews"] == 1
    assert changed["card_sets"][0]["carried_reviews"] == 2
    version_moves.execute_version_move(learner, material, head, [old_set], True,
                                       changed["preview_digest"], "cached-move", dsn=dsn)
    assert True in reads


def test_unmapped_concepts_are_listed_and_not_carried(closed_loop):
    fixture, old_set = learned(closed_loop)
    learner, dsn = fixture["learner"], fixture["dsn"]
    material = fixture["source"].material_id
    document = new_revision(fixture, "split")
    preview = version_moves.preview_version_move(learner, material, dsn=dsn)
    item, = preview["card_sets"]
    assert [(m["from_label"], m["to_label"]) for m in item["mapped"]] == [("Other topic", "Other topic")]
    assert [(u["label"], u["reason"]) for u in item["unmapped"]] == [("Signals", "split")]
    assert item["carried_reviews"] == 0 and item["target_count"] == 1
    study = preview["study"]
    assert study["carried"] == [] and study["current_concept"] is None
    assert [(u["label"], u["reason"]) for u in study["not_carried"]] == [("Signals", "split")]
    before_xp = activity(fixture)
    result = version_moves.execute_version_move(learner, material, document["revision"], [old_set], True,
                                                preview["preview_digest"], "split-move", dsn=dsn)
    progress = derive_learner_progress(learner, result["study_session_id"], dsn=dsn)
    with psycopg.connect(dsn) as db:
        assert db.execute("SELECT completed_concept_ids FROM study_session_moves").fetchall() == [([],)]
    # 概念層級不承接；未改寫的重點仍依既有規則沿用作答證據（inherited_progress）。
    states = {s.label: s for s in progress.concept_states}
    assert states["First signal"].attempts == states["Second signal"].attempts == 1
    # 目前概念對不到時，新版學習從新版學習路徑的第一個概念開始。
    assert progress.current_concept_id == document["initial_learning_path"][0]["concept_id"]
    assert activity(fixture)["xp"] == before_xp["xp"]


def test_api_boundaries_and_owner_isolation(closed_loop, tmp_path, monkeypatch):
    fixture, old_set = learned(closed_loop)
    material = fixture["source"].material_id
    document = new_revision(fixture, "rewrite")
    url = f"/v1/materials/{material}/version-move"
    client = TestClient(_app(fixture["dsn"], tmp_path, monkeypatch), base_url=ORIGIN)
    assert client.get(url).status_code == 401
    client.cookies.set("studydy_session", fixture["token"])
    preview = client.get(url)
    assert preview.status_code == 200, preview.json()
    assert preview.json()["schema"] == "version-move-preview/v2"
    body = {"schema": "version-move-create/v1", "to_revision": document["revision"],
            "card_set_ids": [str(old_set)], "include_study": True,
            "preview_digest": preview.json()["preview_digest"]}
    keyed = {**HEADERS, "Idempotency-Key": "api-move"}
    assert client.post(url, json=body, headers={"Idempotency-Key": "x"}).status_code == 403
    assert client.post(url, json=body, headers=HEADERS).status_code == 400
    for change in [{"schema": "x"}, {"include_study": "yes"}, {"preview_digest": "nope"}, {"extra": 1},
                   {"card_set_ids": ["not-a-uuid"]}]:
        assert client.post(url, headers=keyed, json={**body, **change}).status_code == 400, change
    stale = client.post(url, headers={**HEADERS, "Idempotency-Key": "stale"},
                        json={**body, "preview_digest": "version-move:sha256:" + "0" * 64})
    assert stale.status_code == 409 and stale.json()["reason_code"] == "VERSION_MOVE_STALE"

    other = register_account(f"other_{uuid4().hex[:8]}@example.com", "Synthetic test password 42", dsn=fixture["dsn"])
    foreign = TestClient(_app(fixture["dsn"], tmp_path, monkeypatch), base_url=ORIGIN)
    foreign.cookies.set("studydy_session", other.raw_token)
    assert foreign.get(url).status_code == 404
    assert foreign.post(url, headers=keyed, json=body).status_code == 404
    with pytest.raises(MaterialLibraryError, match="^RESOURCE_NOT_FOUND$"):
        version_moves.preview_version_move(TrustedLearner(other.learner_id), material, dsn=fixture["dsn"])

    saved = client.post(url, headers=keyed, json=body)
    assert saved.status_code == 200, saved.json()
    assert client.post(url, headers=keyed, json=body).json() == saved.json()
    assert client.get(url).json()["card_sets"] == []
    # 他人的標記統計與卡組清單看不到承接結果。
    assert foreign.get("/v1/card-reviews").json()["sets"] == []


def test_concurrent_confirmations_move_once(closed_loop):
    from concurrent.futures import ThreadPoolExecutor

    fixture, old_set = learned(closed_loop)
    learner, dsn = fixture["learner"], fixture["dsn"]
    material = fixture["source"].material_id
    document = new_revision(fixture, "rewrite")
    digest = version_moves.preview_version_move(learner, material, dsn=dsn)["preview_digest"]

    def confirm(key):
        try:
            return version_moves.execute_version_move(
                learner, material, document["revision"], [old_set], True, digest, key, dsn=dsn)["move_id"]
        except card_sets.CardSetError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(confirm, ["same", "same", "other"]))
    # 同一個鍵的兩次送出取得同一筆結果；先後另一個鍵看到已承接的預覽而被拒絕。
    stale = "VERSION_MOVE_STALE"
    assert (results[0] == results[1] != stale and results[2] == stale) or (
        results[0] == results[1] == stale and results[2] != stale)
    with psycopg.connect(dsn) as db:
        assert db.execute("SELECT count(*) FROM material_version_moves").fetchone() == (1,)
        assert db.execute("SELECT count(*) FROM card_set_moves").fetchone() == (1,)
        assert db.execute("SELECT count(*) FROM study_session_moves").fetchone() == (1,)
