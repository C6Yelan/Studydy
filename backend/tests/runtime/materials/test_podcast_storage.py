"""Podcast 影片選項、刪除影片、FLAC 保存與 WAV 轉檔、帳號空間上限；隔離 DB 與合成音訊，不呼叫模型。"""
import base64
from copy import deepcopy
from hashlib import sha256
import io
import math
import os
import wave

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import runtime.api.app as api
from product_fixtures import closed_loop  # noqa: F401
from materials.test_podcasts import create, complete, script
from materials.test_podcast_videos import bundle
from runtime import (
    podcast_audio_flac,
    podcast_progress,
    podcast_scenes as scenes,
    podcast_videos as videos,
    podcasts,
    storage_usage,
)
from runtime.audio import episode_wav, flac_to_wav, wav_to_flac
from runtime.podcast_script import SCHEMA
from runtime.scene_alignment import normalized
from runtime.storage import artifacts
from runtime.storage.database import database_session
from runtime.storage.tables import Artifact, Podcast, PodcastVideo

ORIGIN = "http://127.0.0.1:4183"


def ffmpeg_wav(seconds=2):
    """與 provider 的 FFmpeg 母帶輸出同形：fmt 之後還有 LIST/INFO 區塊。"""
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"".join(
            int(6000 * math.sin(i / 9)).to_bytes(2, "little", signed=True) for i in range(24000 * seconds)))
    data = output.getvalue()
    info = b"INFOISFT\x0e\x00\x00\x00Lavf61.1.100\x00\x00"
    chunk = b"LIST" + len(info).to_bytes(4, "little") + info
    body = data[12:36] + chunk + data[36:]
    return b"RIFF" + (len(body) + 4).to_bytes(4, "little") + b"WAVE" + body


def client_for(fixture):
    _, _, settings, _, dsn, token = fixture
    client = TestClient(api.create_app(api.ApiSettings(
        profile="local", public_origin=ORIGIN, secure_cookie=False, local_config=settings, dsn=dsn)),
        base_url=ORIGIN)
    client.cookies.set("studydy_session", token)
    return client


def finish_audio(dsn, audio):
    state = podcasts.claim_step(dsn=dsn)
    assert podcasts.finish_step(state, script=script(state), dsn=dsn)
    state = podcasts.claim_step(dsn=dsn)
    assert podcasts.finish_step(state, audio=audio, audio_provider="synthetic-test", dsn=dsn)


def test_flac_codec_restores_exact_wav_and_rejects_damage():
    wav = ffmpeg_wav()
    flac = wav_to_flac(wav)
    assert flac[:4] == b"fLaC" and len(flac) < len(wav)
    assert flac_to_wav(flac) == wav
    damaged = bytearray(flac)
    damaged[-40] ^= 0xFF
    with pytest.raises(Exception):
        assert flac_to_wav(bytes(damaged)) == wav


def test_video_is_opt_in_and_audio_only_episode_plays_flac(closed_loop, monkeypatch):
    owner, source, _, document, dsn, _ = closed_loop
    client = client_for(closed_loop)
    body = {"schema": "podcast-create/v1", "name": "只有音訊", "delivery": "solo",
            "knowledge_structure_revision": document["revision"],
            "concept_ids": [c["concept_id"] for c in document["concepts"]]}
    url = f"/v1/materials/{source.material_id}/podcasts"
    assert client.post(url, headers={"Origin": ORIGIN, "Idempotency-Key": "bad"},
                       json={**body, "video": "yes"}).status_code == 400
    created = client.post(url, headers={"Origin": ORIGIN, "Idempotency-Key": "audio-only"}, json=body)
    assert created.status_code == 201 and created.json()["video_enabled"] is False
    identity = created.json()["podcast_id"]
    wav = ffmpeg_wav()
    finish_audio(dsn, wav)
    with database_session(dsn) as db:
        assert not db.scalars(select(PodcastVideo).where(PodcastVideo.podcast_id == identity)).all()
    view = client.get(f"/v1/podcasts/{identity}").json()
    assert view["generation"]["preview_ready"] is True and view["generation"]["completed_videos"] == 0
    assert client.get(f"/v1/podcasts/{identity}/episodes/0/video").json()["status"] == "unprepared"
    audio = client.get(f"/v1/podcasts/{identity}/episodes/0/audio", headers={"Range": "bytes=0-3"})
    assert audio.status_code == 206 and audio.content == b"fLaC"
    assert audio.headers["content-type"] == "audio/flac"
    # 匯出 MP3 直接解碼 FLAC，不落地暫存。
    mp3 = client.get(f"/v1/podcasts/{identity}/episodes/0/export/mp3")
    assert mp3.status_code == 200 and len(mp3.content) > 1000
    # 收聽進度依分集秒數，不受保存格式影響。
    progress = podcast_progress.save_progress(owner.learner_id, identity, 0, 1.5, 1, dsn=dsn)
    assert progress["episodes"][0]["position_seconds"] == 1.5


def test_video_worker_receives_original_wav_and_delete_keeps_audio(closed_loop, monkeypatch):
    owner, _, _, _, dsn, _ = closed_loop
    identity = create(closed_loop, video=True)["podcast_id"]
    wav = ffmpeg_wav()
    finish_audio(dsn, wav)
    sent = []

    def provider(state, audio):
        sent.append(audio)
        return bundle(state)

    monkeypatch.setattr(videos, "provider", provider)
    assert videos.step(dsn=dsn)
    assert sent == [wav]
    client = client_for(closed_loop)
    path = f"/v1/podcasts/{identity}/episodes/0/video"
    ready = client.get(path).json()
    assert ready["status"] == "ready" and ready["video"]["audio_sha256"] == sha256(wav).hexdigest()
    artifact_id = ready["video"]["artifact_id"]
    before = client.get(f"/v1/podcasts/{identity}").json()
    assert before["generation"]["preview_ready"] is True
    audio_before = client.get(f"/v1/podcasts/{identity}/episodes/0/audio").content
    timeline_before = client.get(f"/v1/podcasts/{identity}/episodes/0/timeline")
    assert timeline_before.status_code == 200

    removed = client.delete(path, headers={"Origin": ORIGIN})
    assert removed.status_code == 200 and removed.json()["status"] == "removed"
    assert client.delete(path, headers={"Origin": ORIGIN}).json()["reason_code"] == "VIDEO_CONFLICT"
    with database_session(dsn) as db:
        assert db.get(Artifact, artifact_id) is None
        assert db.scalar(select(PodcastVideo.artifact_id).where(PodcastVideo.podcast_id == identity)) is None
    # 舊版講稿的時間軸只在影片 manifest：刪影片後逐字稿時間軸仍相同。
    assert client.get(f"/v1/podcasts/{identity}/episodes/0/timeline").json() == timeline_before.json()
    assert client.get(f"/v1/podcasts/{identity}/episodes/0/export/md").status_code == 200
    assert client.get(f"/v1/podcasts/{identity}/episodes/0/export/mp4").status_code == 404
    assert videos.prepare(owner.learner_id, identity, 0, dsn=dsn)["status"] == "removed"
    root = artifacts._root()
    assert not (root / "objects" / artifact_id.replace("-", "")).exists()
    assert not list((root / ".trash").iterdir())
    after = client.get(f"/v1/podcasts/{identity}").json()
    assert after["episodes"] == before["episodes"]
    assert after["generation"]["preview_ready"] is True and after["generation"]["retryable_videos"] is False
    assert client.get(f"/v1/podcasts/{identity}/episodes/0/audio").content == audio_before
    assert client.get(path + "/media").status_code == 404
    # 刪掉影片不會被「接續生成」自動重建。
    with pytest.raises(podcasts.PodcastError, match="PODCAST_CONFLICT"):
        podcasts.change_podcast(owner.learner_id, identity, "retry", after["version"], dsn=dsn)


def test_stream_ranges_reuse_saved_hash_until_file_changes(closed_loop, monkeypatch):
    _, _, _, _, dsn, _ = closed_loop
    identity = create(closed_loop)["podcast_id"]
    complete(dsn)
    client = client_for(closed_loop)
    url = f"/v1/podcasts/{identity}/episodes/0/audio"
    hashed = []
    original = artifacts.sha256

    def counting(*args):
        hashed.append(bool(args))
        return original(*args)

    monkeypatch.setattr(artifacts, "sha256", counting)
    first = client.get(url, headers={"Range": "bytes=0-9"})
    assert first.status_code == 206 and hashed.count(False) == 1
    for value in ("bytes=10-19", "bytes=-5", "bytes=0-"):
        assert client.get(url, headers={"Range": value}).status_code == 206
    assert hashed.count(False) == 1
    # 檔案被改寫（ctime／mtime 變了）就重新整檔核對，內容不符即拒絕。
    view = podcasts.read_podcast(closed_loop[0].learner_id, identity, dsn=dsn)
    path = artifacts._root() / "objects" / view["episodes"][0]["audio"]["artifact_id"].replace("-", "")
    os.chmod(path, 0o600)
    data = bytearray(path.read_bytes())
    data[-1] ^= 0xFF
    path.write_bytes(bytes(data))
    assert client.get(url, headers={"Range": "bytes=0-9"}).status_code == 404
    assert hashed.count(False) == 2


def test_account_storage_quota_blocks_new_files_but_keeps_reading(closed_loop, monkeypatch, tmp_path):
    owner, source, _, document, dsn, _ = closed_loop
    client = client_for(closed_loop)
    identity = create(closed_loop, video=True)["podcast_id"]
    complete(dsn)
    root = artifacts._root()
    analysis = root / "analysis" / owner.learner_id.hex / "material"
    analysis.mkdir(parents=True)
    (analysis / "checkpoint.json").write_bytes(b"x" * 1000)
    with database_session(dsn) as db:
        stored = sum(a.size_bytes for a in db.scalars(select(Artifact).where(Artifact.learner_id == owner.learner_id)))
    usage = client.get("/v1/account/storage").json()
    assert usage["used_bytes"] == stored + 1000
    assert usage["limit_bytes"] == 5 * 1024 ** 3
    assert usage["remaining_bytes"] == usage["limit_bytes"] - usage["used_bytes"]

    # 上傳：已用＋本次大小 > 上限才拒絕，等於上限仍可。
    limit = usage["used_bytes"] + 10
    monkeypatch.setattr(storage_usage, "ACCOUNT_STORAGE_LIMIT_BYTES", limit)
    draft = client.post("/v1/materials", json={"schema": "material-draft-create/v1", "display_name": "quota"},
                        headers={"Origin": ORIGIN, "Idempotency-Key": "quota-draft"}).json()["material_id"]
    upload = {"Origin": ORIGIN, "Content-Type": "text/plain", "X-Material-Name": "note.txt"}
    blocked = client.post(f"/v1/materials/{draft}/sources", content="一二三四".encode(),
                          headers={**upload, "Idempotency-Key": "quota-text"})
    assert blocked.status_code == 409
    assert blocked.json()["reason_code"] == "STORAGE_QUOTA_EXCEEDED"
    assert blocked.json()["storage"] == {"used_bytes": usage["used_bytes"], "limit_bytes": limit}
    parts = client.post(f"/v1/materials/{draft}/source-parts", content=b"x" * 8, headers={
        "Origin": ORIGIN, "Idempotency-Key": "quota-parts", "Content-Type": "audio/wav",
        "X-Material-Name": "lecture.wav", "X-Upload-Offset": "0", "X-Upload-Total": "11"})
    assert parts.json()["reason_code"] == "STORAGE_QUOTA_EXCEEDED"
    assert not list((root / ".media-work" / "uploads").iterdir())
    # 尚未收齊的分段上傳計入已用空間。
    accepted = client.post(f"/v1/materials/{draft}/source-parts", content=b"x" * 4, headers={
        "Origin": ORIGIN, "Idempotency-Key": "quota-parts-ok", "Content-Type": "audio/wav",
        "X-Material-Name": "lecture.wav", "X-Upload-Offset": "0", "X-Upload-Total": "10"})
    assert accepted.status_code == 202, accepted.text
    assert client.get("/v1/account/storage").json()["used_bytes"] >= usage["used_bytes"] + 4

    # 空間已滿：建立 Podcast、生成或重試影片都拒絕；閱讀、播放與刪除照常。
    monkeypatch.setattr(storage_usage, "ACCOUNT_STORAGE_LIMIT_BYTES", usage["used_bytes"])
    body = {"schema": "podcast-create/v1", "name": "新節目", "delivery": "solo",
            "knowledge_structure_revision": document["revision"],
            "concept_ids": [c["concept_id"] for c in document["concepts"]]}
    created = client.post(f"/v1/materials/{source.material_id}/podcasts",
                          headers={"Origin": ORIGIN, "Idempotency-Key": "quota-podcast"}, json=body)
    assert created.status_code == 409 and created.json()["reason_code"] == "STORAGE_QUOTA_EXCEEDED"
    video = client.get(f"/v1/podcasts/{identity}/episodes/0/video").json()
    cancelled = client.post(f"/v1/podcasts/{identity}/episodes/0/video/actions", headers={"Origin": ORIGIN},
                            json={"action": "cancel", "expected_version": video["version"]})
    assert cancelled.status_code == 200
    retry = client.post(f"/v1/podcasts/{identity}/episodes/0/video/actions", headers={"Origin": ORIGIN},
                        json={"action": "retry", "expected_version": cancelled.json()["version"]})
    assert retry.json()["reason_code"] == "STORAGE_QUOTA_EXCEEDED"
    view = client.get(f"/v1/podcasts/{identity}").json()
    retry_all = client.post(f"/v1/podcasts/{identity}/actions", headers={"Origin": ORIGIN},
                            json={"schema": "podcast-action/v1", "action": "retry", "expected_version": view["version"]})
    assert retry_all.json()["reason_code"] == "STORAGE_QUOTA_EXCEEDED"
    with database_session(dsn) as db:
        db.query(PodcastVideo).filter(PodcastVideo.podcast_id == identity).delete()
    prepared = client.post(f"/v1/podcasts/{identity}/episodes/0/video", headers={"Origin": ORIGIN})
    assert prepared.json()["reason_code"] == "STORAGE_QUOTA_EXCEEDED"
    assert client.get(f"/v1/podcasts/{identity}/episodes/0/audio").status_code == 200
    assert client.get("/v1/podcasts").status_code == 200
    assert client.delete(f"/v1/podcasts/{identity}", headers={"Origin": ORIGIN}).status_code == 200
    assert client.get("/v1/account/storage").json()["used_bytes"] < usage["used_bytes"]


def legacy_wav_podcast(fixture, monkeypatch, *, key):
    """以舊版保存方式（audio/wav）建立已完成影片與分鏡對齊的節目。"""
    owner, _, _, _, dsn, _ = fixture
    encode, media = podcasts.wav_to_flac, podcasts.FLAC_MEDIA_TYPE
    monkeypatch.setattr(podcasts, "wav_to_flac", lambda audio: audio)
    monkeypatch.setattr(podcasts, "FLAC_MEDIA_TYPE", "audio/wav")
    identity = create(fixture, video=True, key=key)["podcast_id"]
    wav = ffmpeg_wav()
    while state := podcasts.claim_step(dsn=dsn):
        if not state["episode"]["script"]:
            claims = state["episode"]["claims"]
            turns = [{"speaker": "host", "text": c["text"], "parts": [{"text": c["text"], "source_refs": [
                {"source_index": i, "evidence_ids": [e["evidence_id"] for e in c["evidence"]]}]}]}
                for i, c in enumerate(claims)]
            podcasts.finish_step(state, script={
                "schema": SCHEMA, "provider": "synthetic", "segments": [
                    {"beat_id": "beat-0", "title": "整合教學重點", "turns": turns}],
                "review": {k: {"passed": True, "reason": "synthetic"} for k in ("correctness", "teaching_quality")}},
                dsn=dsn)
        else:
            podcasts.finish_step(state, audio=wav, audio_provider="synthetic", dsn=dsn)
    monkeypatch.setattr(podcasts, "wav_to_flac", encode)
    monkeypatch.setattr(podcasts, "FLAC_MEDIA_TYPE", media)
    monkeypatch.setattr(videos, "provider", lambda state, audio: bundle(state))
    assert videos.step(dsn=dsn)
    state = scenes.claim(dsn=dsn)
    quote = normalized(" ".join(t["text"] for t in state["episode"]["script"]["segments"][0]["turns"]))[:12]
    scenes.finish(state, {"starts": [0], "anchors": [{"script_offset": 0, "text": quote, "audio_start": 0,
        "boundary_script_offset": 0, "boundary_text": quote, "boundary_audio_start": 0}],
        "duration": 2, "method": "synthetic", "producer": "fixture"}, dsn=dsn)
    podcast_progress.save_progress(owner.learner_id, identity, 0, 1.25, 1, dsn=dsn)
    return identity, wav


def snapshot(fixture, identity):
    owner, _, _, _, dsn, _ = fixture
    client = client_for(fixture)
    return {
        "podcast": client.get(f"/v1/podcasts/{identity}").json(),
        "video": client.get(f"/v1/podcasts/{identity}/episodes/0/video").json(),
        "timeline": client.get(f"/v1/podcasts/{identity}/episodes/0/timeline").json(),
        "scenes": scenes.read(owner.learner_id, identity, dsn=dsn),
        "progress": podcast_progress.read_progress(owner.learner_id, identity, dsn=dsn),
    }


def test_wav_to_flac_maintenance_keeps_video_alignment_and_progress(closed_loop, monkeypatch, tmp_path):
    owner, _, _, _, dsn, _ = closed_loop
    identity, wav = legacy_wav_podcast(closed_loop, monkeypatch, key="legacy")
    broken, _ = legacy_wav_podcast(closed_loop, monkeypatch, key="legacy-broken")
    episode = podcasts.read_podcast(owner.learner_id, identity, dsn=dsn)["episodes"][0]
    audio_id = episode["audio"]["artifact_id"]
    broken_id = podcasts.read_podcast(owner.learner_id, broken, dsn=dsn)["episodes"][0]["audio"]["artifact_id"]
    root = artifacts._root()
    obj = lambda identity: root / "objects" / identity.replace("-", "")
    before = snapshot(closed_loop, identity)
    assert before["video"]["status"] == "ready" and before["timeline"]["audio_sha256"] == sha256(wav).hexdigest()
    with database_session(dsn) as db:
        rows = {str(a.artifact_id): (a.media_type, bytes(a.sha256), a.size_bytes) for a in db.scalars(select(Artifact))}
    assert rows[audio_id][0] == "audio/wav"
    # 第二檔的實體檔案損毀：轉檔必須保留原狀並回報。
    os.chmod(obj(broken_id), 0o600)
    damaged = bytearray(obj(broken_id).read_bytes())
    damaged[100] ^= 0xFF
    obj(broken_id).write_bytes(bytes(damaged))

    lines = []
    dry = podcast_audio_flac.convert(dry_run=True, dsn=dsn, report=lines.append)
    assert dry["candidates"] == 2 and dry["converted"] == 1 and dry["failed"] == 1
    assert 0 < dry["flac_bytes"] < dry["wav_bytes"] and dry["saved_bytes"] == dry["wav_bytes"] - dry["flac_bytes"]
    assert obj(audio_id).read_bytes() == wav
    with database_session(dsn) as db:
        assert {str(a.artifact_id): (a.media_type, bytes(a.sha256), a.size_bytes) for a in db.scalars(select(Artifact))} == rows

    backup = tmp_path / "backup"
    summary = podcast_audio_flac.convert(backup_dir=backup, dsn=dsn, report=lines.append)
    assert summary["converted"] == 1 and summary["failed"] == 1
    assert [line for line in lines[-3:] if '"failed"' in line and broken_id in line]
    assert (backup / f"{audio_id.replace('-', '')}.wav").read_bytes() == wav
    flac = obj(audio_id).read_bytes()
    assert flac[:4] == b"fLaC" and flac_to_wav(flac) == wav
    with database_session(dsn) as db:
        row = db.get(Artifact, podcasts.UUID(audio_id))
        assert (row.media_type, bytes(row.sha256), row.size_bytes) == ("audio/flac", sha256(flac).digest(), len(flac))
        assert db.get(Artifact, podcasts.UUID(broken_id)).media_type == "audio/wav"
    assert obj(broken_id).read_bytes() == bytes(damaged)
    assert not [p for p in (root / ".staging").iterdir() if not p.name.endswith(".pending")]
    # artifact ID 與分集保存的 WAV 雜湊不變：影片、對齊、時間軸與進度照樣有效。
    assert snapshot(closed_loop, identity) == before
    assert episode_wav(owner.learner_id, episode, dsn=dsn) == wav
    played = client_for(closed_loop).get(f"/v1/podcasts/{identity}/episodes/0/audio")
    assert played.headers["content-type"] == "audio/flac" and played.content == flac
    # 已轉換的不再處理；修好的檔案下一輪才轉。
    assert podcast_audio_flac.convert(backup_dir=backup, dsn=dsn, report=lines.append)["converted"] == 0


def test_failed_database_update_restores_wav_and_interrupted_run_recovers(closed_loop, monkeypatch, tmp_path):
    owner, _, _, _, dsn, _ = closed_loop
    identity, wav = legacy_wav_podcast(closed_loop, monkeypatch, key="legacy-restore")
    audio_id = podcasts.read_podcast(owner.learner_id, identity, dsn=dsn)["episodes"][0]["audio"]["artifact_id"]
    path = artifacts._root() / "objects" / audio_id.replace("-", "")
    before = snapshot(closed_loop, identity)
    # DB 約束拒絕更新（替換已發生）：必須還原原 WAV。
    monkeypatch.setattr(podcast_audio_flac, "FLAC_MEDIA_TYPE", "audio/ogg")
    summary = podcast_audio_flac.convert(backup_dir=tmp_path / "backup", dsn=dsn, report=lambda _: None)
    assert summary["failed"] == 1 and path.read_bytes() == wav
    monkeypatch.setattr(podcast_audio_flac, "FLAC_MEDIA_TYPE", "audio/flac")
    assert snapshot(closed_loop, identity) == before
    # 模擬程序在替換後、提交前被終止：下次執行先還原，再正常轉換。
    staging = artifacts._root() / ".staging"
    os.link(path, staging / f"{audio_id.replace('-', '')}.wav-before-flac")
    path.unlink()
    path.write_bytes(wav_to_flac(wav))
    summary = podcast_audio_flac.convert(backup_dir=tmp_path / "backup", dsn=dsn, report=lambda _: None)
    assert summary["recovered"] == 1 and summary["converted"] == 1
    assert flac_to_wav(path.read_bytes()) == wav
    assert snapshot(closed_loop, identity) == before


def test_flac_constraint_is_limited_to_podcast_audio(closed_loop):
    owner, source, _, _, dsn, _ = closed_loop
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "INSERT INTO artifacts (artifact_id,learner_id,material_id,kind,media_type,sha256,size_bytes,created_at) "
            "VALUES (gen_random_uuid(),%s,%s,'podcast_audio','audio/flac',%s,10,now())",
            (owner.learner_id, source.material_id, b"0" * 32))
    with pytest.raises(psycopg.errors.CheckViolation):
        with psycopg.connect(dsn) as connection:
            connection.execute(
                "INSERT INTO artifacts (artifact_id,learner_id,material_id,kind,media_type,sha256,size_bytes,created_at) "
                "VALUES (gen_random_uuid(),%s,%s,'original','audio/flac',%s,10,now())",
                (owner.learner_id, source.material_id, b"0" * 32))
