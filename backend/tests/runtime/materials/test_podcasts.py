"""Podcast 真 DB／API 回歸；來源與音訊均為合成 fixture，不宣稱模型品質。"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import io
import wave
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import runtime.api.app as api
from product_fixtures import closed_loop, seed_run, _structure, publish_fixture_structure
from runtime import podcasts
from runtime.learner_session import register_account
from runtime.material_discard import request_material_discard
from runtime.material_processing import claim_next_material_processing_run, _record_progress
from runtime.storage.knowledge_structures import _prune_unreferenced_structures, read_knowledge_structure
from runtime.storage.tables import Material, Podcast, Artifact, database_session


def create(fixture, *, mode="quick", key="podcast"):
    owner, source, _, document, dsn, _ = fixture
    return podcasts.create_podcast(owner.learner_id, source.material_id, document["revision"],
        "合成教學", [c["concept_id"] for c in document["concepts"]], mode, key, delivery="solo", dsn=dsn)


def wav():
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\x00\x01" * 24000)
    return output.getvalue()


def script(claim):
    return {"segments": [{"claim_id": c["claim_id"], "turns": [{"speaker": "host", "text": c["text"]}]}
        for c in claim["episode"]["claims"]], "provider": "synthetic-test"}


def complete(dsn):
    claim = podcasts.claim_step(dsn=dsn)
    assert podcasts.finish_step(claim, script=script(claim), dsn=dsn)
    claim = podcasts.claim_step(dsn=dsn)
    assert podcasts.finish_step(claim, audio=wav(), audio_provider="synthetic-test", dsn=dsn)


def test_split_all_claims_in_selection_order_without_omissions():
    concepts = [{"concept_id": f"concept-{i}", "label": f"概念 {i}", "claims": [
        {"claim_id": f"claim-{i}-{j}", "text": "來源支持的重點。", "evidence": [{"quote": "來源"}]}
        for j in range(7)]} for i in range(2)]
    episodes = podcasts.plan_episodes({"concepts": concepts}, ["concept-1", "concept-0"])
    assert [len(e["claims"]) for e in episodes] == [6, 6, 2]
    assert [c["claim_id"] for e in episodes for c in e["claims"]] == [
        c["claim_id"] for concept in reversed(concepts) for c in concept["claims"]]
    with pytest.raises(podcasts.PodcastError, match="SOURCE_INSUFFICIENT"):
        podcasts.plan_episodes({"concepts": [{**concepts[0], "claims": []}]}, ["concept-0"])


def test_persistent_script_audio_retry_and_idempotency(closed_loop):
    owner, _, _, _, dsn, _ = closed_loop
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: create(closed_loop), range(2)))
    assert results[0] == results[1]
    identity = results[0]["podcast_id"]
    with pytest.raises(podcasts.PodcastError, match="IDEMPOTENCY_CONFLICT"):
        create(closed_loop, mode="full")
    claim = podcasts.claim_step(dsn=dsn)
    assert podcasts.claim_step(dsn=dsn) is None
    saved_script = script(claim)
    assert podcasts.finish_step(claim, script=saved_script, dsn=dsn)
    audio_claim = podcasts.claim_step(dsn=dsn)
    assert podcasts.finish_step(audio_claim, error="PODCAST_PROVIDER_FAILED", dsn=dsn)
    view = podcasts.read_podcast(owner.learner_id, identity, dsn=dsn)
    assert view["status"] == "failed" and view["episodes"][0]["script"] == saved_script
    podcasts.change_podcast(owner.learner_id, identity, "retry", view["version"], dsn=dsn)
    retry = podcasts.claim_step(dsn=dsn)
    assert retry["episode"]["script"] == saved_script
    assert podcasts.finish_step(retry, audio=wav(), audio_provider="synthetic-test", dsn=dsn)
    view = podcasts.read_podcast(owner.learner_id, identity, dsn=dsn)
    assert view["status"] == "ready" and view["completed_episodes"] == 1
    assert view["episodes"][0]["audio"]["duration_seconds"] == 1
    assert not podcasts.finish_step(audio_claim, audio=wav(), audio_provider="synthetic-test", dsn=dsn)
    with psycopg.connect(dsn) as db:
        assert db.execute("SELECT count(*) FROM study_sessions").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM artifacts WHERE kind='podcast_audio'").fetchone() == (1,)


def test_cancel_expired_lease_and_delete_fence_late_results(closed_loop):
    owner, _, _, _, dsn, _ = closed_loop
    identity = create(closed_loop)["podcast_id"]
    old = podcasts.claim_step(dsn=dsn)
    with database_session(dsn) as db:
        db.get(Podcast, identity).lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    new = podcasts.claim_step(dsn=dsn)
    assert new["token"] != old["token"]
    assert not podcasts.finish_step(old, script=script(old), dsn=dsn)
    view = podcasts.read_podcast(owner.learner_id, identity, dsn=dsn)
    podcasts.change_podcast(owner.learner_id, identity, "cancel", view["version"], dsn=dsn)
    assert not podcasts.finish_step(new, script=script(new), dsn=dsn)
    cancelled = podcasts.read_podcast(owner.learner_id, identity, dsn=dsn)
    with pytest.raises(podcasts.PodcastError, match="PODCAST_CONFLICT"):
        podcasts.change_podcast(owner.learner_id, identity, "retry", view["version"], dsn=dsn)
    podcasts.change_podcast(owner.learner_id, identity, "retry", cancelled["version"], dsn=dsn)
    claim = podcasts.claim_step(dsn=dsn)
    podcasts.delete_podcast(owner.learner_id, identity, dsn=dsn)
    assert not podcasts.finish_step(claim, script=script(claim), dsn=dsn)
    assert podcasts.list_podcasts(owner.learner_id, dsn=dsn)["podcasts"] == []
    with pytest.raises(podcasts.PodcastError, match="RESOURCE_NOT_FOUND"):
        create(closed_loop)


def test_reject_wrong_script_and_truncated_audio_without_publishing(closed_loop):
    owner, _, _, _, dsn, _ = closed_loop
    identity = create(closed_loop)["podcast_id"]
    claim = podcasts.claim_step(dsn=dsn)
    wrong = script(claim)
    wrong["segments"][0]["claim_id"] = "different-source"
    with pytest.raises(podcasts.PodcastError, match="SCRIPT_INVALID"):
        podcasts.finish_step(claim, script=wrong, dsn=dsn)
    assert podcasts.finish_step(claim, script=script(claim), dsn=dsn)
    claim = podcasts.claim_step(dsn=dsn)
    with pytest.raises(podcasts.PodcastError, match="AUDIO_INVALID"):
        podcasts.finish_step(claim, audio=wav()[:-10], audio_provider="synthetic-test", dsn=dsn)
    assert podcasts.read_podcast(owner.learner_id, identity, dsn=dsn)["completed_episodes"] == 0
    assert podcasts.finish_step(claim, audio=wav(), audio_provider="synthetic-test", dsn=dsn)


def test_saved_podcast_pins_source_revision_and_deletion_releases_it(closed_loop):
    owner, source, settings, original, dsn, _ = closed_loop
    identity = create(closed_loop)["podcast_id"]
    run = seed_run(owner.learner_id, source.material_id, "podcast-new-head", settings, dsn=dsn)
    assert claim_next_material_processing_run(dsn=dsn).run.run_id == run.run_id
    for stage in ("evidence", "semantics", "publishing"):
        _record_progress(run.run_id, stage, 1, 1, dsn=dsn)
    updated = _structure(str(run.run_id), source.sha256, settings["runtime_lock"], partial=True)
    publish_fixture_structure(owner.learner_id, source.material_id, run.run_id, updated, dsn=dsn)
    with database_session(dsn) as db:
        material = db.scalar(select(Material).where(Material.material_id == source.material_id).with_for_update())
        _prune_unreferenced_structures(db, owner.learner_id, source.material_id, material.head_revision)
    view = podcasts.read_podcast(owner.learner_id, identity, dsn=dsn)
    assert view["knowledge_structure_revision"] == original["revision"] and not view["is_current_revision"]
    expected = read_knowledge_structure(owner.learner_id, source.material_id, revision=original["revision"], dsn=dsn).view
    assert view["source_resolver"] == expected["source_resolver"]
    complete(dsn)
    podcasts.delete_podcast(owner.learner_id, identity, dsn=dsn)
    with psycopg.connect(dsn) as db:
        assert db.execute("SELECT count(*) FROM knowledge_structures WHERE structure_revision=%s", (original["revision"],)).fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM artifacts WHERE kind='podcast_audio'").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM materials").fetchone() == (1,)


def test_api_owner_audio_ranges_and_origin_boundary(closed_loop, monkeypatch):
    owner, source, settings, document, dsn, token = closed_loop
    monkeypatch.setattr(api, "runtime_binding", lambda _: {})
    origin = "http://127.0.0.1:4183"
    app = api.create_app(api.ApiSettings(profile="local", public_origin=origin, secure_cookie=False, local_config=settings, dsn=dsn))
    client = TestClient(app, base_url=origin)
    url = f"/v1/materials/{source.material_id}/podcasts"
    body = {"schema": "podcast-create/v1", "name": "測試 Podcast", "mode": "full", "delivery": "solo",
        "knowledge_structure_revision": document["revision"], "concept_ids": [c["concept_id"] for c in document["concepts"]]}
    headers = {"Origin": origin, "Idempotency-Key": "podcast-api"}
    assert client.post(url, headers=headers, json=body).status_code == 401
    client.cookies.set("studydy_session", token)
    assert client.post(url, json=body).status_code == 403
    assert client.post(url, headers={"Origin": origin}, json=body).status_code == 400
    response = client.post(url, headers=headers, json=body)
    assert response.status_code == 201, response.text
    identity = response.json()["podcast_id"]
    detail = f"/v1/podcasts/{identity}"
    audio_url = detail + "/episodes/0/audio"
    assert client.get(audio_url).status_code == 404
    complete(dsn)
    view = client.get(detail)
    assert view.status_code == 200, view.text
    assert view.headers["cache-control"] == "private, no-store"
    assert client.get(audio_url).content == wav()
    # 重建 API instance，確認播放 bytes／已保存版本不依賴程序內記憶體。
    reopened = TestClient(api.create_app(api.ApiSettings(profile="local", public_origin=origin,
        secure_cookie=False, local_config=settings, dsn=dsn)), base_url=origin)
    reopened.cookies.set("studydy_session", token)
    assert reopened.get(detail).json() == view.json()
    assert reopened.get(audio_url).content == wav()
    for value, expected in [("bytes=0-43", wav()[:44]), ("bytes=44-", wav()[44:]), ("bytes=-20", wav()[-20:])]:
        response = client.get(audio_url, headers={"Range": value})
        assert response.status_code == 206 and response.content == expected
        assert response.headers["content-range"].endswith(f"/{len(wav())}")
        assert int(response.headers["content-length"]) == len(expected)
    for value in ("bytes=999999-", "bytes=-0", "bytes=20-1", "bytes=1-2,5-6", "invalid"):
        assert client.get(audio_url, headers={"Range": value}).status_code == 416
    stranger = register_account("podcast-other@example.com", "Synthetic other password 42", dsn=dsn)
    client.cookies.set("studydy_session", stranger.raw_token)
    assert client.get("/v1/podcasts").json()["podcasts"] == []
    assert client.get(detail).status_code == client.get(audio_url).status_code == 404
    assert client.delete(detail, headers={"Origin": origin}).status_code == 404
    client.cookies.set("studydy_session", token)
    assert client.delete(detail, headers={"Origin": origin}).status_code == 200
    assert client.get(audio_url).status_code == 404
    assert client.post(url, headers=headers, json=body).status_code == 404


def test_material_discard_fences_podcast_and_removes_its_artifacts(closed_loop):
    owner, source, _, _, dsn, _ = closed_loop
    identity = create(closed_loop)["podcast_id"]
    complete(dsn)
    other = create(closed_loop, key="running")["podcast_id"]
    claim = podcasts.claim_step(dsn=dsn)
    assert request_material_discard(owner.learner_id, source.material_id, dsn=dsn) == "removed"
    assert not podcasts.finish_step(claim, script=script(claim), dsn=dsn)
    with database_session(dsn) as db:
        assert db.get(Podcast, identity) is db.get(Podcast, other) is None
        assert db.scalar(select(Artifact)) is None


def test_artifact_write_failure_rolls_back_and_reconciles_orphan(closed_loop, monkeypatch):
    from runtime.storage.source_artifacts import reconcile_new_artifacts
    from runtime.storage.artifacts import _object_path, _root
    owner, _, _, _, dsn, _ = closed_loop
    identity = create(closed_loop)["podcast_id"]
    claim = podcasts.claim_step(dsn=dsn)
    podcasts.finish_step(claim, script=script(claim), dsn=dsn)
    claim = podcasts.claim_step(dsn=dsn)
    original_write = podcasts.write_blob
    staged = []
    def fail_after_write(*args, **kwargs):
        artifact = original_write(*args, **kwargs)
        staged.append(artifact.artifact_id)
        raise OSError("synthetic commit boundary failure")
    with monkeypatch.context() as patch:
        patch.setattr(podcasts, "write_blob", fail_after_write)
        with pytest.raises(OSError):
            podcasts.finish_step(claim, audio=wav(), audio_provider="synthetic-test", dsn=dsn)
    assert podcasts.read_podcast(owner.learner_id, identity, dsn=dsn)["completed_episodes"] == 0
    reconcile_new_artifacts(dsn=dsn)
    assert not _object_path(_root(), staged[0]).exists()
    assert podcasts.finish_step(claim, audio=wav(), audio_provider="synthetic-test", dsn=dsn)


def test_source_context_keeps_same_page_headers_without_changing_claims(closed_loop):
    owner, _, _, _, dsn, _ = closed_loop
    identity = create(closed_loop)["podcast_id"]
    view = podcasts.read_podcast(owner.learner_id, identity, dsn=dsn)
    claim = podcasts.claim_step(dsn=dsn)
    assert set(claim["source_context"]) == {e["page_ref"] for c in claim["episode"]["claims"] for e in c["evidence"]}
    for c in claim["episode"]["claims"]:
        for e in c["evidence"]:
            context = claim["source_context"][e["page_ref"]]
            assert context["source_id"] == e["source_id"]
            assert context["normalized_page"] == e["normalized_page"]
            block = next(b for b in context["blocks"] if b["evidence_id"] == e["evidence_id"])
            assert block["text"] == e["quote"]
    assert podcasts.read_podcast(owner.learner_id, identity, dsn=dsn)["episodes"] == view["episodes"]


def test_table_context_includes_headers_but_excludes_unrelated_pages():
    document = {"input_binding": {"manifest": {"items": [{"source_id": "source-a", "original_name": "table.pdf"}]},
        "bundle": {"pages": [{"source_id": "source-a", "normalized_page": i} for i in (1, 2)]}},
        "evidence": [{"page_ref": page, "page": number, "block_order": order, "evidence_id": identity,
            "exact_text": text, "source_locator": {"region": [0, 0, 1, 1]}}
            for page, number, order, identity, text in [("table-page", 1, 1, "header", "欄位：甲協定"),
                ("table-page", 1, 2, "cell", "不提供同等能力"), ("other-page", 2, 1, "unrelated", "其他頁內容")]]}
    context = podcasts.source_context(document, {"claims": [{"evidence": [{"page_ref": "table-page"}]}]})
    assert list(context) == ["table-page"]
    assert [b["evidence_id"] for b in context["table-page"]["blocks"]] == ["header", "cell"]


def test_dialogue_persists_turns_and_rejects_wrong_speaker_or_source(closed_loop):
    owner, source, _, document, dsn, _ = closed_loop
    saved = podcasts.create_podcast(owner.learner_id, source.material_id, document["revision"], "雙人解說",
        [c["concept_id"] for c in document["concepts"]], "full", "dialogue", delivery="dialogue", dsn=dsn)
    claim = podcasts.claim_step(dsn=dsn)
    assert claim["episode"]["delivery"] == "dialogue"
    dialogue = {"provider": "synthetic-dialogue", "segments": [{"claim_id": c["claim_id"], "turns": [
        {"speaker": "host", "text": "可以用一個情境解釋這個重點嗎？"},
        {"speaker": "guest", "text": c["text"]},
    ]} for c in claim["episode"]["claims"]]}
    bad = deepcopy(dialogue); bad["segments"][0]["turns"][1]["speaker"] = "unknown"
    with pytest.raises(podcasts.PodcastError, match="SCRIPT_INVALID"):
        podcasts.finish_step(claim, script=bad, dsn=dsn)
    assert podcasts.finish_step(claim, script=dialogue, dsn=dsn)
    audio_claim = podcasts.claim_step(dsn=dsn)
    assert podcasts.finish_step(audio_claim, audio=wav(), audio_provider="synthetic-test", dsn=dsn)
    view = podcasts.read_podcast(owner.learner_id, saved["podcast_id"], dsn=dsn)
    assert view["delivery"] == "dialogue" and view["episodes"][0]["script"] == dialogue
    assert view["episodes"][0]["audio"]["provider"] == "synthetic-test"


def test_turn_migration_preserves_saved_text_references_and_audio(closed_loop, migrations_dir):
    from sqlalchemy import text as sql_text
    owner, _, _, _, dsn, _ = closed_loop
    identity = create(closed_loop)["podcast_id"]
    complete(dsn)
    before = podcasts.read_podcast(owner.learner_id, identity, dsn=dsn)
    # 重現升級前的已保存資料，驗證 migration 本身，不增加正式版相容分支。
    with database_session(dsn) as db:
        row = db.get(Podcast, identity)
        episodes = deepcopy(row.episodes)
        for e in episodes:
            e.pop("delivery")
            for segment in e["script"]["segments"]:
                segment["text"] = segment.pop("turns")[0]["text"]
        row.episodes = episodes
        db.flush()
        db.execute(sql_text((migrations_dir / "0009_podcast_turns.sql").read_text()))
    after = podcasts.read_podcast(owner.learner_id, identity, dsn=dsn)
    assert after == before


def test_extended_dialogue_and_actual_audio_provider_are_preserved(closed_loop):
    owner, source, _, document, dsn, _ = closed_loop
    result = podcasts.create_podcast(owner.learner_id, source.material_id, document["revision"],
        "長對談", [document["concepts"][0]["concept_id"]], "full", "extended-dialogue", delivery="dialogue", dsn=dsn)
    claim = podcasts.claim_step(dsn=dsn)
    value = {"provider": "synthetic-text", "segments": [{"claim_id": c["claim_id"], "turns": [
        {"speaker": "host" if i % 2 == 0 else "guest", "text": "這裡可以再說明一下嗎？" if i % 2 == 0 else c["text"]}
        for i in range(8)]} for c in claim["episode"]["claims"]]}
    assert podcasts.finish_step(claim, script=value, dsn=dsn)
    claim = podcasts.claim_step(dsn=dsn)
    with pytest.raises(podcasts.PodcastError, match="PODCAST_AUDIO_INVALID"):
        podcasts.finish_step(claim, audio=wav(), dsn=dsn)
    assert podcasts.finish_step(claim, audio=wav(), audio_provider="synthetic-voice/exact-engine", dsn=dsn)
    view = podcasts.read_podcast(owner.learner_id, result["podcast_id"], dsn=dsn)
    assert len(view["episodes"][0]["script"]["segments"][0]["turns"]) == 8
    assert view["episodes"][0]["audio"]["provider"] == "synthetic-voice/exact-engine"


def test_dialogue_roles_are_required_per_episode_not_per_claim():
    episode = {"delivery": "dialogue", "claims": [{"claim_id": "one"}, {"claim_id": "two"}]}
    value = {"provider": "synthetic", "segments": [
        {"claim_id": "one", "turns": [{"speaker": "guest", "text": "先把一個觀念說清楚，再讓對方自然接話。"}]},
        {"claim_id": "two", "turns": [{"speaker": "host", "text": "原來我剛才把兩種順序混在一起了。"}]},
    ]}
    assert podcasts.validate_script(value, episode) == value
    value["segments"][1]["turns"][0]["speaker"] = "guest"
    with pytest.raises(podcasts.PodcastError, match="PODCAST_SCRIPT_INVALID"):
        podcasts.validate_script(value, episode)
