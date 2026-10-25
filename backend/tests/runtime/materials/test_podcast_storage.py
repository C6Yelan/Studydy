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
    media_sources,
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


def storage(client):
    return client.get("/v1/account/storage").json()


def upload_part(client, draft, key, offset, chunk, total):
    return client.post(f"/v1/materials/{draft}/source-parts", content=chunk, headers={
        "Origin": ORIGIN, "Idempotency-Key": key, "Content-Type": "audio/wav",
        "X-Material-Name": "lecture.wav", "X-Upload-Offset": str(offset), "X-Upload-Total": str(total)})


def draft_for(client, key="quota"):
    return client.post("/v1/materials", json={"schema": "material-draft-create/v1", "display_name": key},
                       headers={"Origin": ORIGIN, "Idempotency-Key": key + "-draft"}).json()["material_id"]


def test_usage_counts_files_and_chunked_uploads_reserve_declared_size(closed_loop, monkeypatch):
    owner, _, _, _, dsn, _ = closed_loop
    client = client_for(closed_loop)
    root = artifacts._root()
    analysis = root / "analysis" / owner.learner_id.hex / "material"
    analysis.mkdir(parents=True)
    (analysis / "checkpoint.json").write_bytes(b"x" * 1000)
    with database_session(dsn) as db:
        stored = sum(a.size_bytes for a in db.scalars(select(Artifact).where(Artifact.learner_id == owner.learner_id)))
    usage = storage(client)
    assert usage == {"schema": "account-storage/v1", "used_bytes": stored + 1000, "reserved_bytes": 0,
                     "limit_bytes": 5 * 1024 ** 3, "remaining_bytes": 5 * 1024 ** 3 - stored - 1000}
    # Codex 重現：上限比已用多 1000 bytes，兩份宣告 800 bytes 的分段上傳只能接受一份。
    monkeypatch.setattr(storage_usage, "ACCOUNT_STORAGE_LIMIT_BYTES", usage["used_bytes"] + 1000)
    draft = draft_for(client)
    assert upload_part(client, draft, "first", 0, b"x", 800).status_code == 202
    after = storage(client)
    assert after["reserved_bytes"] == 800 and after["used_bytes"] == usage["used_bytes"] + 800
    second = upload_part(client, draft, "second", 0, b"x", 800)
    assert second.status_code == 409 and second.json()["reason_code"] == "STORAGE_QUOTA_EXCEEDED"
    assert second.json()["storage"]["used_bytes"] == usage["used_bytes"] + 800
    # 後續各段不得超過宣告大小；補到宣告大小內照常接受，保留額度不增加。
    assert upload_part(client, draft, "first", 1, b"x" * 800, 800).status_code == 400
    assert upload_part(client, draft, "first", 1, b"x" * 798, 800).status_code == 202
    assert storage(client)["used_bytes"] == usage["used_bytes"] + 800
    assert [p.name for p in (root / ".media-work" / "uploads").iterdir() if "second" in p.name] == []
    # 宣告大小剛好用完上限時可以；再多 1 byte 就拒絕。
    single = {"Origin": ORIGIN, "Content-Type": "text/plain", "X-Material-Name": "note.txt"}
    assert client.post(f"/v1/materials/{draft}/sources", content=b"a" * 201,
                       headers={**single, "Idempotency-Key": "over"}).json()["reason_code"] == "STORAGE_QUOTA_EXCEEDED"


def test_full_account_blocks_new_files_but_replays_reading_and_deletion_continue(closed_loop, monkeypatch):
    owner, source, _, document, dsn, _ = closed_loop
    client = client_for(closed_loop)
    draft = draft_for(client, "replay")
    single = {"Origin": ORIGIN, "Content-Type": "text/plain", "X-Material-Name": "note.txt", "Idempotency-Key": "note"}
    first = client.post(f"/v1/materials/{draft}/sources", content="一二三四".encode(), headers=single)
    assert first.status_code == 202, first.text
    body = {"schema": "podcast-create/v1", "name": "節目", "delivery": "solo", "video": True,
            "knowledge_structure_revision": document["revision"],
            "concept_ids": [c["concept_id"] for c in document["concepts"]]}
    url = f"/v1/materials/{source.material_id}/podcasts"
    created = client.post(url, headers={"Origin": ORIGIN, "Idempotency-Key": "first-podcast"}, json=body)
    assert created.status_code == 201
    identity = created.json()["podcast_id"]
    # 每集預估：全站還沒有成品時用預設值；未產生的分集以音訊＋影片預估保留。
    assert storage(client)["reserved_bytes"] == storage_usage.DEFAULT_EPISODE_AUDIO_BYTES + storage_usage.DEFAULT_EPISODE_VIDEO_BYTES
    monkeypatch.setattr(storage_usage, "ACCOUNT_STORAGE_LIMIT_BYTES", storage(client)["used_bytes"])
    # 冪等重送：先回傳原結果，不因帳號隨後額滿而失敗。
    assert client.post(f"/v1/materials/{draft}/sources", content="一二三四".encode(), headers=single).json() == first.json()
    assert client.post(url, headers={"Origin": ORIGIN, "Idempotency-Key": "first-podcast"}, json=body).json()["podcast_id"] == identity
    blocked = client.post(url, headers={"Origin": ORIGIN, "Idempotency-Key": "second-podcast"}, json=body)
    assert blocked.status_code == 409 and blocked.json()["reason_code"] == "STORAGE_QUOTA_EXCEEDED"
    other = client.post(f"/v1/materials/{draft}/sources", content="五六".encode(), headers={**single, "Idempotency-Key": "other"})
    assert other.json()["reason_code"] == "STORAGE_QUOTA_EXCEEDED"
    # 已接受的節目照常產檔：實際成品取代保留額度。
    complete(dsn)
    usage = storage(client)
    with database_session(dsn) as db:
        audio = db.scalar(select(Artifact.size_bytes).where(Artifact.kind == "podcast_audio"))
    assert usage["reserved_bytes"] == storage_usage.DEFAULT_EPISODE_VIDEO_BYTES
    video = client.get(f"/v1/podcasts/{identity}/episodes/0/video").json()
    cancelled = client.post(f"/v1/podcasts/{identity}/episodes/0/video/actions", headers={"Origin": ORIGIN},
                            json={"action": "cancel", "expected_version": video["version"]})
    assert cancelled.status_code == 200 and storage(client)["reserved_bytes"] == 0
    # 取消後空出預估額度；上限低於「已用＋一集影片預估」時拒絕重試、補建與手動生成。
    files = storage(client)["used_bytes"]
    estimate = storage_usage.DEFAULT_EPISODE_VIDEO_BYTES
    monkeypatch.setattr(storage_usage, "ACCOUNT_STORAGE_LIMIT_BYTES", files + estimate - 1)
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
    # 空間足夠一集影片預估時，勾選影片節目的「接續生成」補建缺少的影片工作。
    monkeypatch.setattr(storage_usage, "ACCOUNT_STORAGE_LIMIT_BYTES", files + estimate)
    view = client.get(f"/v1/podcasts/{identity}").json()
    assert view["generation"]["retryable_videos"] and not view["generation"]["preview_ready"]
    assert view["generation"]["expected_videos"] == 1
    assert client.post(f"/v1/podcasts/{identity}/actions", headers={"Origin": ORIGIN},
                       json={"schema": "podcast-action/v1", "action": "retry", "expected_version": view["version"]}).status_code == 200
    assert client.get(f"/v1/podcasts/{identity}/episodes/0/video").json()["status"] == "pending"
    assert audio > 0
    assert client.get(f"/v1/podcasts/{identity}/episodes/0/audio").status_code == 200
    assert client.get("/v1/podcasts").status_code == 200
    assert client.delete(f"/v1/podcasts/{identity}", headers={"Origin": ORIGIN}).status_code == 200
    assert storage(client)["used_bytes"] < files


def test_podcast_without_video_never_backfills_videos(closed_loop):
    owner, _, _, _, dsn, _ = closed_loop
    identity = create(closed_loop)["podcast_id"]
    complete(dsn)
    view = podcasts.read_podcast(owner.learner_id, identity, dsn=dsn)
    assert view["generation"]["expected_videos"] == 0 and view["generation"]["preview_ready"]
    with pytest.raises(podcasts.PodcastError, match="PODCAST_CONFLICT"):
        podcasts.change_podcast(owner.learner_id, identity, "retry", view["version"], dsn=dsn)


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


def test_conversion_run_lock_cleans_temporaries_and_checks_peak_space(closed_loop, monkeypatch, tmp_path):
    from types import SimpleNamespace
    from sqlalchemy import text

    owner, _, _, _, dsn, _ = closed_loop
    identity, wav = legacy_wav_podcast(closed_loop, monkeypatch, key="legacy-lock")
    audio_id = podcasts.read_podcast(owner.learner_id, identity, dsn=dsn)["episodes"][0]["audio"]["artifact_id"]
    root = artifacts._root()
    path = root / "objects" / audio_id.replace("-", "")
    backup = tmp_path / "backup"
    quiet = lambda _: None
    # 另一個轉檔程序持有執行鎖時不得開始，也不得收尾別人的暫存。
    staging = root / ".staging"
    leftovers = [staging / f"{audio_id.replace('-', '')}.old.flac-new", staging / f"{audio_id.replace('-', '')}.old.flac-new.old.tmp"]
    for leftover in leftovers:
        leftover.write_bytes(b"partial")
    backup.mkdir()
    (backup / "stale.wav.old.tmp").write_bytes(b"partial")
    with database_session(dsn) as other:
        other.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": podcast_audio_flac._RUN_LOCK})
        with pytest.raises(podcast_audio_flac.ConversionError, match="ANOTHER_CONVERSION_RUNNING"):
            podcast_audio_flac.convert(backup_dir=backup, dsn=dsn, report=quiet)
        assert all(leftover.exists() for leftover in leftovers)
    # 峰值空間：同一個檔案系統時備份＋FLAC 暫存合併計算；分開時兩邊各自核對。
    candidates = [(None, None, 500), (None, None, 300)]
    peak = 800 + 500 + podcast_audio_flac._FLAC_OVERHEAD
    monkeypatch.setattr(podcast_audio_flac.shutil, "disk_usage", lambda _: SimpleNamespace(free=peak - 1))
    with pytest.raises(podcast_audio_flac.ConversionError, match="DISK_SPACE_INSUFFICIENT"):
        podcast_audio_flac._require_space(root, backup, candidates)
    monkeypatch.setattr(podcast_audio_flac.shutil, "disk_usage", lambda _: SimpleNamespace(free=peak))
    podcast_audio_flac._require_space(root, backup, candidates)
    real_stat = os.stat
    monkeypatch.setattr(podcast_audio_flac.os, "stat", lambda p, *a, **k: SimpleNamespace(st_dev=1 if str(p) == str(backup) else 2)
                        if str(p) in (str(backup), str(root)) else real_stat(p, *a, **k))
    free = {str(backup): 800, str(root): 500 + podcast_audio_flac._FLAC_OVERHEAD - 1}
    monkeypatch.setattr(podcast_audio_flac.shutil, "disk_usage", lambda p: SimpleNamespace(free=free[str(p)]))
    with pytest.raises(podcast_audio_flac.ConversionError, match="ARTIFACT_SPACE_INSUFFICIENT"):
        podcast_audio_flac._require_space(root, backup, candidates)
    free[str(root)] += 1
    free[str(backup)] -= 1
    with pytest.raises(podcast_audio_flac.ConversionError, match="BACKUP_SPACE_INSUFFICIENT"):
        podcast_audio_flac._require_space(root, backup, candidates)
    monkeypatch.undo()
    monkeypatch.setenv("STUDYDY_ARTIFACT_ROOT", str(root))
    # fsync 失敗：該檔保留原狀、不留下 .tmp；下次執行先清掉中斷的暫存再正常轉換。
    real_fsync = os.fsync

    def failing(descriptor):
        # 只讓暫存檔（*.tmp）的 fsync 失敗，目錄同步照常。
        if os.readlink(f"/proc/self/fd/{descriptor}").endswith(".tmp"):
            raise OSError("synthetic fsync failure")
        return real_fsync(descriptor)

    monkeypatch.setattr(podcast_audio_flac.os, "fsync", failing)
    summary = podcast_audio_flac.convert(backup_dir=backup, dsn=dsn, report=quiet)
    assert summary["failed"] == 1 and path.read_bytes() == wav
    assert not list(backup.glob("*.tmp")) and not list(staging.glob("*.tmp"))
    monkeypatch.setattr(podcast_audio_flac.os, "fsync", real_fsync)
    summary = podcast_audio_flac.convert(backup_dir=backup, dsn=dsn, report=quiet)
    assert summary["converted"] == 1 and flac_to_wav(path.read_bytes()) == wav
    assert not any(leftover.exists() for leftover in leftovers)
    assert not list(backup.glob("*.tmp")) and not [p for p in staging.iterdir() if not p.name.endswith(".pending")]


def test_slow_upload_does_not_hold_the_account_lock_and_lock_timeout_is_retryable(closed_loop, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import time
    from sqlalchemy import text

    owner, _, _, _, dsn, _ = closed_loop
    client = client_for(closed_loop)
    first_draft, second_draft = draft_for(client, "slow"), draft_for(client, "fast")
    real = api.upload_source

    def slow(*args, **kwargs):
        if args[1] == first_draft:
            time.sleep(6)
        return real(*args, **kwargs)

    monkeypatch.setattr(api, "upload_source", slow)
    headers = {"Origin": ORIGIN, "Content-Type": "text/plain", "X-Material-Name": "note.txt"}

    def send(draft, key, content):
        started = time.monotonic()
        response = client_for(closed_loop).post(f"/v1/materials/{draft}/sources", content=content,
                                                headers={**headers, "Idempotency-Key": key})
        return response, time.monotonic() - started

    with ThreadPoolExecutor(max_workers=2) as pool:
        slow_upload = pool.submit(send, first_draft, "slow", "慢速寫入".encode())
        time.sleep(1)
        fast, elapsed = pool.submit(send, second_draft, "fast", "另一份教材".encode()).result(timeout=30)
        assert fast.status_code == 202 and elapsed < 4
        assert slow_upload.result(timeout=30)[0].status_code == 202
    # 帳號鎖被長時間占用（例如另一個程序卡住）時，回可重試的 STORAGE_BUSY，不是 500。
    with database_session(dsn) as holder:
        storage_usage.lock_account(holder, owner.learner_id)
        busy, elapsed = send(second_draft, "busy", "等待帳號鎖".encode())
    assert busy.status_code == 503 and elapsed >= 4.5
    assert busy.json()["reason_code"] == "STORAGE_BUSY" and busy.json()["retryable"] is True


def test_same_key_sent_twice_at_once_has_one_writer_and_replays_the_result(closed_loop, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import time

    owner, _, _, _, dsn, _ = closed_loop
    client = client_for(closed_loop)
    draft = draft_for(client, "twice")
    content = "同一份上傳".encode()
    monkeypatch.setattr(storage_usage, "ACCOUNT_STORAGE_LIMIT_BYTES", storage(client)["used_bytes"] + len(content))
    real, calls = api.upload_source, []

    def delayed(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            time.sleep(1.5)
        return real(*args, **kwargs)

    monkeypatch.setattr(api, "upload_source", delayed)
    headers = {"Origin": ORIGIN, "Content-Type": "text/plain", "X-Material-Name": "note.txt", "Idempotency-Key": "same"}
    send = lambda: client_for(closed_loop).post(f"/v1/materials/{draft}/sources", content=content, headers=headers)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(send)
        time.sleep(0.5)
        second = pool.submit(send)
        results = [first.result(timeout=30), second.result(timeout=30)]
    # 同 key 同意圖只有一個寫入者：第二個請求不落盤，回可重試的處理中；重試即取得原結果。
    assert [r.status_code for r in results] == [202, 503], [r.text for r in results]
    assert results[1].json()["reason_code"] == "STORAGE_BUSY" and results[1].json()["retryable"] is True
    assert len(calls) == 1
    retried = send()
    assert retried.status_code == 202 and retried.json() == results[0].json()
    assert len(results[0].json()["sources"]) == 1
    usage = storage(client)
    assert usage["reserved_bytes"] == 0 and usage["remaining_bytes"] == 0


def test_reservations_are_fixed_at_acceptance_and_settled_with_the_artifact(closed_loop):
    from sqlalchemy import text

    owner, source, _, _, dsn, _ = closed_loop
    identity = create(closed_loop, video=True)["podcast_id"]
    estimate = storage_usage.DEFAULT_EPISODE_AUDIO_BYTES + storage_usage.DEFAULT_EPISODE_VIDEO_BYTES
    assert storage_usage.account_usage(owner.learner_id, dsn=dsn)["reserved_bytes"] == estimate
    # 其他帳號的成品改變全站平均，不重新定價已接受的工作。
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "INSERT INTO artifacts (artifact_id,learner_id,material_id,kind,media_type,sha256,size_bytes,created_at) "
            "VALUES (gen_random_uuid(),%s,%s,'podcast_audio','audio/flac',%s,%s,now())",
            (owner.learner_id, source.material_id, b"9" * 32, 7))
    usage = storage_usage.account_usage(owner.learner_id, dsn=dsn)
    assert usage["reserved_bytes"] == estimate
    # 成品寫入的同一交易刪除該集音訊保留，改計實際大小；影片保留仍在。
    complete(dsn)
    after = storage_usage.account_usage(owner.learner_id, dsn=dsn)
    with database_session(dsn) as db:
        kinds = db.scalars(text("SELECT kind FROM storage_reservations WHERE podcast_id = :p"), {"p": identity}).all()
    assert kinds == ["podcast_video"] and after["reserved_bytes"] == storage_usage.DEFAULT_EPISODE_VIDEO_BYTES
    monkey_bundle = videos.claim(dsn=dsn)
    assert videos.finish(monkey_bundle, bundle=bundle(monkey_bundle), dsn=dsn)
    assert storage_usage.account_usage(owner.learner_id, dsn=dsn)["reserved_bytes"] == 0
    podcasts.delete_podcast(owner.learner_id, identity, dsn=dsn)
    with database_session(dsn) as db:
        assert not db.scalar(text("SELECT count(*) FROM storage_reservations WHERE podcast_id = :p"), {"p": identity})


def test_resuming_failed_or_cancelled_podcast_backfills_and_reserves_videos(closed_loop, monkeypatch):
    from sqlalchemy import text

    owner, _, _, _, dsn, _ = closed_loop
    identity = create(closed_loop, video=True)["podcast_id"]
    with database_session(dsn) as db:
        row = db.get(Podcast, identity)
        row.episodes = [deepcopy(row.episodes[0]) for _ in range(2)]
    # 第一集完成後失敗；模擬 0020 前缺少影片工作與保留的舊分集。
    complete(dsn)
    with database_session(dsn) as db:
        db.execute(text("DELETE FROM podcast_videos WHERE podcast_id = :p"), {"p": identity})
        db.execute(text("DELETE FROM storage_reservations WHERE podcast_id = :p AND kind = 'podcast_video'"), {"p": identity})
        row = db.get(Podcast, identity)
        row.status, row.error_code = "failed", "PODCAST_PROVIDER_FAILED"
    view = podcasts.read_podcast(owner.learner_id, identity, dsn=dsn)
    # 空間不足一集影片預估時，接續生成被拒絕且不新增保留。
    files = storage_usage.account_usage(owner.learner_id, dsn=dsn)["used_bytes"]
    monkeypatch.setattr(storage_usage, "ACCOUNT_STORAGE_LIMIT_BYTES", files + storage_usage.DEFAULT_EPISODE_VIDEO_BYTES - 1)
    with pytest.raises(storage_usage.StorageQuotaError):
        podcasts.change_podcast(owner.learner_id, identity, "retry", view["version"], dsn=dsn)
    assert storage_usage.account_usage(owner.learner_id, dsn=dsn)["used_bytes"] == files
    monkeypatch.setattr(storage_usage, "ACCOUNT_STORAGE_LIMIT_BYTES", files + storage_usage.DEFAULT_EPISODE_VIDEO_BYTES)
    podcasts.change_podcast(owner.learner_id, identity, "retry", view["version"], dsn=dsn)
    with database_session(dsn) as db:
        jobs = db.execute(text("SELECT episode_index, status FROM podcast_videos WHERE podcast_id = :p"), {"p": identity}).all()
        reserved = db.scalars(text("SELECT episode_index FROM storage_reservations WHERE podcast_id = :p AND kind = 'podcast_video'"), {"p": identity}).all()
    assert [tuple(j) for j in jobs] == [(0, "pending")] and reserved == [0]
    # cancelled 的影片恢復成 pending 前也要核對並保留；已滿時拒絕，不會讓已用＋保留超過上限。
    view = podcasts.read_podcast(owner.learner_id, identity, dsn=dsn)
    state = videos.read(owner.learner_id, identity, 0, dsn=dsn)
    videos.action(owner.learner_id, identity, 0, "cancel", state["version"], dsn=dsn)
    with database_session(dsn) as db:
        row = db.get(Podcast, identity)
        row.status, row.error_code = "cancelled", None
    files = storage_usage.account_usage(owner.learner_id, dsn=dsn)["used_bytes"]
    monkeypatch.setattr(storage_usage, "ACCOUNT_STORAGE_LIMIT_BYTES", files)
    view = podcasts.read_podcast(owner.learner_id, identity, dsn=dsn)
    with pytest.raises(storage_usage.StorageQuotaError):
        podcasts.change_podcast(owner.learner_id, identity, "retry", view["version"], dsn=dsn)
    assert storage_usage.account_usage(owner.learner_id, dsn=dsn)["used_bytes"] == files
    assert videos.read(owner.learner_id, identity, 0, dsn=dsn)["status"] == "cancelled"


def test_conversion_stops_before_a_flac_that_no_longer_fits(closed_loop, monkeypatch, tmp_path):
    from types import SimpleNamespace

    owner, _, _, _, dsn, _ = closed_loop
    first, wav = legacy_wav_podcast(closed_loop, monkeypatch, key="grow-1")
    second, _ = legacy_wav_podcast(closed_loop, monkeypatch, key="grow-2")
    ids = [podcasts.read_podcast(owner.learner_id, p, dsn=dsn)["episodes"][0]["audio"]["artifact_id"].replace("-", "")
           for p in (first, second)]
    root = artifacts._root()
    staging = root / ".staging"
    checks = []
    real = podcast_audio_flac.shutil.disk_usage

    def usage(path):
        # 第二檔落地前，artifact 盤只剩不到一份 FLAC 的空間（多檔累積膨脹）。
        if str(path) == str(staging):
            checks.append(path)
            if len(checks) >= 2:
                return SimpleNamespace(free=10)
        return real(path)

    monkeypatch.setattr(podcast_audio_flac.shutil, "disk_usage", usage)
    summary = podcast_audio_flac.convert(backup_dir=tmp_path / "backup", dsn=dsn, report=lambda _: None)
    assert summary["converted"] == 1 and summary["failed"] == 1
    assert summary["stopped"] == "ARTIFACT_SPACE_INSUFFICIENT"
    assert (root / "objects" / ids[0]).read_bytes()[:4] == b"fLaC"
    assert (root / "objects" / ids[1]).read_bytes() == wav
    with database_session(dsn) as db:
        assert db.get(Artifact, podcasts.UUID(ids[1])).media_type == "audio/wav"
    assert not [p for p in staging.iterdir() if not p.name.endswith(".pending")]


def test_reservation_is_bound_to_the_request_intent(closed_loop, monkeypatch):
    owner, _, _, _, dsn, _ = closed_loop
    client = client_for(closed_loop)
    draft = draft_for(client, "intent")
    base = storage(client)["used_bytes"]
    monkeypatch.setattr(storage_usage, "ACCOUNT_STORAGE_LIMIT_BYTES", base + 2)
    # Codex 反例：同 key 先開 2 bytes 的分段上傳，再用普通上傳送另一份 1000 bytes，不能被當成重送。
    assert upload_part(client, draft, "K", 0, b"x", 2).status_code == 202
    other = client.post(f"/v1/materials/{draft}/sources", content=b"a" * 1000, headers={
        "Origin": ORIGIN, "Content-Type": "text/plain", "X-Material-Name": "note.txt", "Idempotency-Key": "K"})
    assert other.status_code == 409 and other.json()["reason_code"] == "STORAGE_QUOTA_EXCEEDED"
    assert storage(client)["used_bytes"] == base + 2
    # 同入口同 key 但內容不同、原寫入者仍在進行：依冪等規則回衝突，不重新核對或落盤。
    monkeypatch.setattr(storage_usage, "ACCOUNT_STORAGE_LIMIT_BYTES", base + 10_000)
    intent = media_sources.upload_intent("sources", "note.txt", "text/plain", 5, "0" * 64)
    media_sources.accept_upload("sources", owner.learner_id, podcasts.UUID(draft), "S", 5, intent, dsn=dsn)
    reserved_before_conflict = storage(client)
    conflict = client.post(f"/v1/materials/{draft}/sources", content=b"bbbbb", headers={
        "Origin": ORIGIN, "Content-Type": "text/plain", "X-Material-Name": "note.txt", "Idempotency-Key": "S"})
    assert conflict.status_code == 409 and conflict.json()["reason_code"] == "IDEMPOTENCY_CONFLICT"
    assert storage(client) == reserved_before_conflict


def test_rejected_chunked_restart_keeps_the_previous_upload(closed_loop, monkeypatch):
    client = client_for(closed_loop)
    draft = draft_for(client, "restart")
    base = storage(client)["used_bytes"]
    monkeypatch.setattr(storage_usage, "ACCOUNT_STORAGE_LIMIT_BYTES", base + 1000)
    assert upload_part(client, draft, "K", 0, b"x", 800).status_code == 202
    rejected = upload_part(client, draft, "K", 0, b"y", 1100)
    assert rejected.status_code == 409 and rejected.json()["reason_code"] == "STORAGE_QUOTA_EXCEEDED"
    # 先核准再破壞：舊的分段、意圖與 800 bytes 保留都還在，可以接著上傳。
    usage = storage(client)
    assert usage["reserved_bytes"] == 800
    uploads = artifacts._root() / ".media-work" / "uploads"
    assert sorted(p.suffix for p in uploads.iterdir()) == [".json", ".part"]
    assert upload_part(client, draft, "K", 1, b"x" * 10, 800).json()["received_bytes"] == 11
    # 核准後的重新開始才移除舊暫存並換成新的宣告。
    restarted = upload_part(client, draft, "K", 0, b"z", 900)
    assert restarted.status_code == 202 and restarted.json()["received_bytes"] == 1
    assert storage(client)["reserved_bytes"] == 900


def test_uploads_to_missing_material_are_not_found_before_any_reservation(closed_loop):
    from uuid import uuid4

    client = client_for(closed_loop)
    missing = uuid4()
    single = client.post(f"/v1/materials/{missing}/sources", content=b"abc", headers={
        "Origin": ORIGIN, "Content-Type": "text/plain", "X-Material-Name": "note.txt", "Idempotency-Key": "m"})
    parts = upload_part(client, missing, "p", 0, b"x", 10)
    assert single.status_code == parts.status_code == 404
    assert single.json()["reason_code"] == parts.json()["reason_code"] == "RESOURCE_NOT_FOUND"
    assert storage(client)["reserved_bytes"] == 0


def test_account_then_material_lock_order_has_no_deadlock(closed_loop, monkeypatch):
    """Codex 反例：上傳保留（帳號鎖→教材 KEY SHARE）與建立 Podcast／手動生成影片（原為教材鎖→帳號鎖）
    同時進行。兩個真實交易交錯，統一順序後都完成，不出現 40P01。"""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    import time

    owner, source, _, document, dsn, _ = closed_loop
    identity = create(closed_loop, video=False, key="lock-order")["podcast_id"]
    complete(dsn)
    inside, real = Event(), storage_usage.require_room

    def paused(db, *args, **kwargs):
        if not inside.is_set():
            inside.set()
            time.sleep(1.5)
        return real(db, *args, **kwargs)

    monkeypatch.setattr(storage_usage, "require_room", paused)
    intent = media_sources.upload_intent("sources", "note.txt", "text/plain", 3, "1" * 64)
    with ThreadPoolExecutor(max_workers=3) as pool:
        upload = pool.submit(media_sources.accept_upload, "sources", owner.learner_id, source.material_id,
                             "lock", 3, intent, dsn=dsn)
        assert inside.wait(5)
        podcast = pool.submit(podcasts.create_podcast, owner.learner_id, source.material_id, document["revision"],
                              "鎖順序", [c["concept_id"] for c in document["concepts"]], "lock-podcast",
                              delivery="solo", video=True, dsn=dsn)
        video = pool.submit(videos.prepare, owner.learner_id, identity, 0, dsn=dsn)
        assert upload.result(timeout=30) is not None
        assert podcast.result(timeout=30)["video_enabled"] is True
        assert video.result(timeout=30)["status"] == "pending"


def test_requeue_and_prepare_only_reserve_jobs_that_are_really_queued(closed_loop, monkeypatch):
    from sqlalchemy import text

    owner, _, _, _, dsn, _ = closed_loop
    identity = create(closed_loop, video=True, key="requeue")["podcast_id"]
    with database_session(dsn) as db:
        row = db.get(Podcast, identity)
        row.episodes = [deepcopy(row.episodes[0]) for _ in range(2)]
        # 合成兩集節目：補上建立時第二集應有的保留。
        for kind in ("podcast_audio", "podcast_video"):
            storage_usage.reserve(db, owner.learner_id, row.material_id, kind, 10, podcast_id=identity, episode_index=1)
    complete(dsn)

    def video_reservations():
        with database_session(dsn) as db:
            return db.scalars(text("SELECT episode_index FROM storage_reservations "
                                   "WHERE podcast_id = :p AND kind = 'podcast_video' ORDER BY episode_index"), {"p": identity}).all()

    # 第一集影片失敗（同交易釋放影片保留），之後第二集音訊也失敗：整份節目 failed。
    state = videos.claim(dsn=dsn)
    assert videos.finish(state, error="VIDEO_RENDER_FAILED", dsn=dsn)
    claim = podcasts.claim_step(dsn=dsn)
    podcasts.finish_step(claim, script=script(claim), dsn=dsn)
    claim = podcasts.claim_step(dsn=dsn)
    podcasts.finish_step(claim, error="PODCAST_PROVIDER_FAILED", dsn=dsn)
    view = podcasts.read_podcast(owner.learner_id, identity, dsn=dsn)
    assert view["status"] == "failed" and video_reservations() == [1]
    # Codex 反例：接續生成同時恢復 failed 影片並重新保留，不必再按一次。
    podcasts.change_podcast(owner.learner_id, identity, "retry", view["version"], dsn=dsn)
    assert videos.read(owner.learner_id, identity, 0, dsn=dsn)["status"] == "pending"
    assert video_reservations() == [0, 1]
    # 取消後同來源的 prepare 不會排隊，也不占額度。
    state = videos.read(owner.learner_id, identity, 0, dsn=dsn)
    videos.action(owner.learner_id, identity, 0, "cancel", state["version"], dsn=dsn)
    assert video_reservations() == [1]
    before = storage_usage.account_usage(owner.learner_id, dsn=dsn)["used_bytes"]
    assert videos.prepare(owner.learner_id, identity, 0, dsn=dsn)["status"] == "cancelled"
    assert video_reservations() == [1]
    assert storage_usage.account_usage(owner.learner_id, dsn=dsn)["used_bytes"] == before


@pytest.mark.parametrize("old_fails", [True, False])
def test_expired_upload_cannot_publish_or_release_successor(closed_loop, monkeypatch, old_fails):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    owner, _, _, _, dsn, _ = closed_loop
    client = client_for(closed_loop)
    draft = draft_for(client, "lease-takeover")
    content = b"lease takeover content"
    base = storage(client)["used_bytes"]
    monkeypatch.setattr(storage_usage, "ACCOUNT_STORAGE_LIMIT_BYTES", base + len(content))
    real = api.upload_source
    entered = [Event(), Event()]
    resume = [Event(), Event()]
    calls = []

    def delayed(*args, **kwargs):
        index = len(calls)
        calls.append(kwargs["upload_lease"])
        entered[index].set()
        assert resume[index].wait(15)
        if index == 0 and old_fails:
            raise media_sources.SourceError("REQUEST_INVALID")
        return real(*args, **kwargs)

    monkeypatch.setattr(api, "upload_source", delayed)
    headers = {"Origin": ORIGIN, "Content-Type": "text/plain", "X-Material-Name": "note.txt", "Idempotency-Key": "lease"}
    def send():
        return client_for(closed_loop).post(f"/v1/materials/{draft}/sources", content=content, headers=headers)

    with ThreadPoolExecutor(max_workers=2) as pool:
        try:
            first = pool.submit(send)
            assert entered[0].wait(10)
            with psycopg.connect(dsn) as db:
                db.execute("UPDATE storage_reservations SET expires_at = now() - interval '1 second' WHERE reservation_id = %s",
                           (calls[0].reservation_id,))
            second = pool.submit(send)
            assert entered[1].wait(10)
            assert calls[0].writer_id != calls[1].writer_id
            resume[0].set()
            rejected = first.result(timeout=10)
            assert rejected.status_code == (400 if old_fails else 503), rejected.text
            assert storage(client)["used_bytes"] == base + len(content)
            assert storage(client)["reserved_bytes"] == len(content)
            # A 的遲到失敗／發布不得釋放 B 的額度，C 仍不能新增。
            blocked = client.post(f"/v1/materials/{draft}/sources", content=b"another", headers={**headers, "Idempotency-Key": "C"})
            assert blocked.status_code == 409 and blocked.json()["reason_code"] == "STORAGE_QUOTA_EXCEEDED"
            resume[1].set()
            completed = second.result(timeout=10)
            assert completed.status_code == 202 and len(completed.json()["sources"]) == 1
        finally:
            for event in resume:
                event.set()
    assert storage(client)["reserved_bytes"] == 0
    assert storage(client)["used_bytes"] == base + len(content)


def test_maintenance_expired_upload_cannot_publish_without_reservation(closed_loop):
    owner, _, _, _, dsn, _ = closed_loop
    client = client_for(closed_loop)
    draft = draft_for(client, "lease-cleanup")
    content = b"expired upload"
    lease = media_sources.accept_upload("sources", owner.learner_id, draft, "expired", len(content),
        media_sources.upload_intent("sources", "note.txt", "text/plain", len(content), sha256(content).hexdigest()), dsn=dsn)
    with psycopg.connect(dsn) as db:
        db.execute("UPDATE storage_reservations SET expires_at = now() - interval '1 second' WHERE reservation_id = %s",
                   (lease.reservation_id,))
    storage_usage.release_stale_upload_reservations(dsn=dsn)
    with pytest.raises(storage_usage.StorageBusyError):
        api.upload_source(owner.learner_id, draft, content, "note.txt", "text/plain", "expired", dsn=dsn, upload_lease=lease)
    assert client.get(f"/v1/materials/{draft}/sources").json()["sources"] == []


@pytest.mark.parametrize("legacy_metadata", [False, True])
def test_chunk_after_reservation_cleanup_requires_restart(closed_loop, legacy_metadata):
    import json

    owner, _, _, _, dsn, _ = closed_loop
    client = client_for(closed_loop)
    draft = draft_for(client, "chunk-lease-cleanup")
    content = ffmpeg_wav()
    assert upload_part(client, draft, "chunk", 0, content[:1], len(content)).status_code == 202
    with psycopg.connect(dsn) as db:
        db.execute("UPDATE storage_reservations SET expires_at = now() - interval '1 second' WHERE material_id = %s", (draft,))
    storage_usage.release_stale_upload_reservations(dsn=dsn)
    if legacy_metadata:
        _, meta = media_sources._upload_paths(owner.learner_id, podcasts.UUID(draft), "chunk")
        metadata = json.loads(meta.read_text())
        del metadata["writer_id"]
        meta.write_text(json.dumps(metadata))
    rejected = upload_part(client, draft, "chunk", 1, content[1:], len(content))
    assert rejected.status_code == (409 if legacy_metadata else 503)
    assert rejected.json()["reason_code"] == ("UPLOAD_OFFSET_MISMATCH" if legacy_metadata else "STORAGE_BUSY")
    assert upload_part(client, draft, "chunk", 0, content, len(content)).status_code == 202
    assert storage(client)["reserved_bytes"] == 0
