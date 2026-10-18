"""Podcast 真 DB／API 回歸；來源與音訊均為合成 fixture，不宣稱模型品質。"""
from runtime.errors import PodcastError
from migration_fixtures import legacy_sql
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
from runtime import podcasts, podcast_script
from runtime.learner_session import register_account
from runtime.material_discard import request_material_discard
from runtime.material_processing import claim_next_material_processing_run, _record_progress
from runtime.storage.knowledge_structures import _prune_unreferenced_structures, read_knowledge_structure
from runtime.storage.tables import Material, Podcast, Artifact, database_session


def create(fixture, *, delivery="solo", key="podcast"):
    owner, source, _, document, dsn, _ = fixture
    return podcasts.create_podcast(owner.learner_id, source.material_id, document["revision"],
        "合成教學", [c["concept_id"] for c in document["concepts"]], key, delivery=delivery, dsn=dsn)


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


def test_persistent_script_audio_retry_and_idempotency(closed_loop):
    owner, _, _, _, dsn, _ = closed_loop
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: create(closed_loop), range(2)))
    assert results[0] == results[1]
    identity = results[0]["podcast_id"]
    with pytest.raises(PodcastError, match="IDEMPOTENCY_CONFLICT"):
        create(closed_loop, delivery="dialogue")
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


def test_podcast_read_can_coexist_with_material_readers(closed_loop):
    owner,source,_,_,dsn,_=closed_loop
    identity=create(closed_loop)['podcast_id']
    with database_session(dsn) as db:
        db.scalar(select(Material).where(Material.material_id==source.material_id).with_for_update(read=True))
        with ThreadPoolExecutor(max_workers=1) as pool:
            view=pool.submit(podcasts.read_podcast,owner.learner_id,identity,dsn=dsn).result(timeout=2)
    assert view['podcast_id']==identity


def test_slow_podcast_source_verification_does_not_block_result_saving(closed_loop, monkeypatch):
    from runtime.storage.materials import require_material
    owner, source, _, _, dsn, _ = closed_loop
    identity = create(closed_loop)['podcast_id']
    verify = podcasts._read_verified_document
    def while_reading(*args, **kwargs):
        with database_session(dsn) as writer:
            require_material(writer, owner.learner_id, source.material_id)
        return verify(*args, **kwargs)
    monkeypatch.setattr(podcasts, '_read_verified_document', while_reading)
    assert podcasts.read_podcast(owner.learner_id, identity, dsn=dsn)['podcast_id'] == identity


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
    assert podcasts.list_podcasts(owner.learner_id, dsn=dsn)["podcasts"] == []
    with pytest.raises(PodcastError, match="RESOURCE_NOT_FOUND"):
        podcasts.read_podcast(owner.learner_id, identity, dsn=dsn)
    with pytest.raises(PodcastError, match="RESOURCE_NOT_FOUND"):
        podcasts.change_podcast(owner.learner_id, identity, "retry", view["version"], dsn=dsn)
    with pytest.raises(PodcastError, match="RESOURCE_NOT_FOUND"):
        create(closed_loop)


def test_reject_wrong_script_and_truncated_audio_without_publishing(closed_loop):
    owner, _, _, _, dsn, _ = closed_loop
    identity = create(closed_loop)["podcast_id"]
    claim = podcasts.claim_step(dsn=dsn)
    wrong = script(claim)
    wrong["segments"][0]["claim_id"] = "different-source"
    with pytest.raises(PodcastError, match="SCRIPT_INVALID"):
        podcasts.finish_step(claim, script=wrong, dsn=dsn)
    assert podcasts.finish_step(claim, script=script(claim), dsn=dsn)
    claim = podcasts.claim_step(dsn=dsn)
    with pytest.raises(PodcastError, match="AUDIO_INVALID"):
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
    body = {"schema": "podcast-create/v1", "name": "測試 Podcast", "delivery": "solo",
        "knowledge_structure_revision": document["revision"], "concept_ids": [c["concept_id"] for c in document["concepts"]]}
    headers = {"Origin": origin, "Idempotency-Key": "podcast-api"}
    assert client.post(url, headers=headers, json=body).status_code == 401
    client.cookies.set("studydy_session", token)
    assert client.post(url, json=body).status_code == 403
    assert client.post(url, headers={"Origin": origin}, json=body).status_code == 400
    response = client.post(url, headers=headers, json=body)
    assert response.status_code == 201, response.text
    assert 'mode' not in response.json()
    for retired_mode in ['quick', 'full']:
        assert client.post(url, headers=headers, json={**body, 'mode': retired_mode}).status_code == 400
    identity = response.json()["podcast_id"]
    detail = f"/v1/podcasts/{identity}"
    audio_url = detail + "/episodes/0/audio"
    assert client.get(audio_url).status_code == 404
    complete(dsn)
    view = client.get(detail)
    assert view.status_code == 200, view.text
    assert view.headers["cache-control"] == "private, no-store"
    assert client.get(audio_url).content == wav()
    # 舊欄位與已保存內容仍可讀取，歷史模式不會觸發重寫或重新生成。
    with database_session(dsn) as db:
        legacy = db.get(Podcast, identity)
        legacy.mode = 'full'
        stored_episodes = deepcopy(legacy.episodes)
    assert client.get(detail).json() == view.json()
    assert client.get(audio_url).content == wav()
    with database_session(dsn) as db:
        legacy = db.get(Podcast, identity)
        assert legacy.mode == 'full' and legacy.episodes == stored_episodes
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
    assert view.json()['generation'] == {'active':True,'completed_videos':0,'failed_videos':0,'preview_ready':False,'retryable_videos':False}
    cancelled = client.post(detail + '/actions', headers={'Origin':origin}, json={
        'schema':'podcast-action/v1','action':'cancel','expected_version':view.json()['version']})
    assert cancelled.status_code == 200 and cancelled.json() == {'schema':'podcast-deleted/v1','podcast_id':identity}
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
    from runtime.storage.artifacts import reconcile_new_artifacts
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




def test_dialogue_persists_turns_and_rejects_wrong_speaker_or_source(closed_loop):
    owner, source, _, document, dsn, _ = closed_loop
    saved = podcasts.create_podcast(owner.learner_id, source.material_id, document["revision"], "雙人解說",
        [c["concept_id"] for c in document["concepts"]], "dialogue", delivery="dialogue", dsn=dsn)
    claim = podcasts.claim_step(dsn=dsn)
    assert claim["episode"]["delivery"] == "dialogue"
    dialogue = {"provider": "synthetic-dialogue", "segments": [{"claim_id": c["claim_id"], "turns": [
        {"speaker": "host", "text": "可以用一個情境解釋這個重點嗎？"},
        {"speaker": "guest", "text": c["text"]},
    ]} for c in claim["episode"]["claims"]]}
    bad = deepcopy(dialogue); bad["segments"][0]["turns"][1]["speaker"] = "unknown"
    with pytest.raises(PodcastError, match="SCRIPT_INVALID"):
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
        db.execute(sql_text(legacy_sql("0009_podcast_turns.sql")))
    after = podcasts.read_podcast(owner.learner_id, identity, dsn=dsn)
    assert after == before


def test_extended_dialogue_and_actual_audio_provider_are_preserved(closed_loop):
    owner, source, _, document, dsn, _ = closed_loop
    result = podcasts.create_podcast(owner.learner_id, source.material_id, document["revision"],
        "長對談", [document["concepts"][0]["concept_id"]], "extended-dialogue", delivery="dialogue", dsn=dsn)
    claim = podcasts.claim_step(dsn=dsn)
    value = {"provider": "synthetic-text", "segments": [{"claim_id": c["claim_id"], "turns": [
        {"speaker": "host" if i % 2 == 0 else "guest", "text": "這裡可以再說明一下嗎？" if i % 2 == 0 else c["text"]}
        for i in range(8)]} for c in claim["episode"]["claims"]]}
    assert podcasts.finish_step(claim, script=value, dsn=dsn)
    claim = podcasts.claim_step(dsn=dsn)
    with pytest.raises(PodcastError, match="PODCAST_AUDIO_INVALID"):
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
    assert podcast_script.validate_script(value, episode) == value
    value["segments"][1]["turns"][0]["speaker"] = "guest"
    with pytest.raises(PodcastError, match="PODCAST_SCRIPT_INVALID"):
        podcast_script.validate_script(value, episode)


def test_v2_podcast_finishes_without_quality_verdicts_or_loudness_report(closed_loop):
    owner,_,settings,_,dsn,token=closed_loop
    identity=create(closed_loop)['podcast_id']
    state=podcasts.claim_step(dsn=dsn);claim=state['episode']['claims'][0]
    text=claim['text']
    ref={'source_index':0,'evidence_ids':[e['evidence_id'] for e in claim['evidence']]}
    turn={'speaker':'host','text':text,'parts':[{'text':text,'source_refs':[ref]}]}
    value={'schema':'podcast-script/v2','provider':'synthetic-single-generation',
           'segments':[{'beat_id':'beat-0','title':'合成重點','turns':[turn]}]}
    assert podcasts.finish_step(state,script=value,dsn=dsn)
    audio_state=podcasts.claim_step(dsn=dsn)
    assert podcasts.finish_step(audio_state,audio=wav(),audio_provider='synthetic',dsn=dsn)
    result=podcasts.read_podcast(owner.learner_id,identity,dsn=dsn)
    assert result['status']=='ready' and result['episodes'][0]['script']==value
    app=api.create_app(api.ApiSettings(profile='local',public_origin='http://127.0.0.1:4183',secure_cookie=False,local_config=settings,dsn=dsn))
    client=TestClient(app,base_url='http://127.0.0.1:4183')
    client.cookies.set('studydy_session',token)
    response=client.get(f'/v1/podcasts/{identity}')
    assert response.status_code==200
    assert response.json()['episodes'][0]['script']['review'] is None


def test_failed_podcast_with_waiting_scenes_does_not_poll_forever(closed_loop):
    from runtime.storage.tables import PodcastScenes
    owner, _, _, _, dsn, _ = closed_loop
    identity = create(closed_loop)['podcast_id']
    with database_session(dsn) as db:
        row = db.get(Podcast, identity)
        row.status = 'failed'
        row.error_code = 'PODCAST_CANCELLED'
        db.add(PodcastScenes(scene_id=uuid4(), podcast_id=identity, episode_index=0,
            input_sha256='a'*64, status='pending', version=1, created_at=datetime.now(UTC)))
    item = podcasts.list_podcasts(owner.learner_id, dsn=dsn)['podcasts'][0]
    assert item['status'] == 'failed'
    assert not item['generation']['active']
    with database_session(dsn) as db:
        assert db.scalar(select(PodcastScenes.status).where(PodcastScenes.podcast_id == identity)) == 'pending'


def test_playback_metadata_keeps_sources_without_building_all_concept_views(closed_loop, monkeypatch):
    owner, source, _, _, dsn, _ = closed_loop
    identity = create(closed_loop)['podcast_id']
    before = podcasts.read_podcast(owner.learner_id, identity, dsn=dsn)
    def unnecessary_view(*args, **kwargs):
        raise AssertionError("Playback metadata must not expand every concept")
    monkeypatch.setattr(podcasts, '_view', unnecessary_view)
    after = podcasts.read_podcast(owner.learner_id, identity, dsn=dsn)
    assert after == before
    assert after['source_resolver'].startswith(f'/v1/materials/{source.material_id}/')
