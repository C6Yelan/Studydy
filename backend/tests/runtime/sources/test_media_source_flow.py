"""隔離 DB：圖片組、音訊分段上傳、影片音軌與 YouTube 參照的轉換及來源回查。

語音辨識與 YouTube 讀取以替身取代，不連網、不呼叫模型；沙箱轉檔使用真實 PyAV／PyMuPDF。
"""

import io
import json
import os
import time
from urllib.parse import quote
import zipfile

import av
import psycopg
import pymupdf
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import runtime.api.app as api
from runtime import media_sources
from runtime.source_normalization import create_draft, normalize_next, read_sources
from runtime.source_revisions import create_revision
from runtime.source_resolver import _input, bind_structure_input
from runtime.storage.artifacts import open_verified_artifact
from runtime.storage.database import database_session
from runtime.storage.knowledge_reads import resolve_evidence_source
from runtime.storage.tables import Artifact
from runtime.material_processing import claim_next_material_processing_run, _record_progress
from product_fixtures import publish_fixture_structure, closed_loop  # noqa: F401

HEADERS = {"Origin": "http://127.0.0.1:4173"}
TRANSCRIPT = [
    {"start": 3.0, "end": 9.0, "text": "堆疊遵守後進先出，最後放入的資料最先取出。"},
    {"start": 131.0, "end": 140.0, "text": "佇列遵守先進先出，最早排入的資料最先取出。"},
]


def _wave(seconds):
    buffer = io.BytesIO()
    with av.open(buffer, "w", format="wav") as output:
        stream = output.add_stream("pcm_s16le", rate=8000)
        stream.layout = "mono"
        frame = av.AudioFrame(format="s16", layout="mono", samples=8000)
        frame.sample_rate = 8000
        for plane in frame.planes:
            plane.update(bytes(plane.buffer_size))
        for second in range(seconds):
            frame.pts = second * 8000
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode(None):
            output.mux(packet)
    return buffer.getvalue()


def _video(path, seconds=3):
    import numpy

    with av.open(str(path), "w") as output:
        video = output.add_stream("mpeg4", rate=5)
        video.width, video.height, video.pix_fmt = 64, 48, "yuv420p"
        audio = output.add_stream("aac", rate=48000)
        frame = av.AudioFrame(format="fltp", layout="stereo", samples=1024)
        frame.sample_rate = 48000
        for plane in frame.planes:
            plane.update(bytes(plane.buffer_size))
        for index in range(seconds * 48000 // 1024):
            frame.pts = index * 1024
            for packet in audio.encode(frame):
                output.mux(packet)
        for index in range(seconds * 5):
            image = av.VideoFrame.from_ndarray(numpy.full((48, 64, 3), index * 10 % 255, numpy.uint8), format="rgb24")
            for packet in video.encode(image):
                output.mux(packet)
        for stream in (video, audio):
            for packet in stream.encode(None):
                output.mux(packet)


def _client(closed_loop, *, upload_max_bytes=api.MAX_FILE_BYTES):
    _, _, settings, _, dsn, token = closed_loop
    app = api.create_app(
        api.ApiSettings(
            profile="local",
            public_origin=HEADERS["Origin"],
            secure_cookie=False,
            local_config=settings,
            dsn=dsn,
            upload_max_bytes=upload_max_bytes,
        )
    )
    client = TestClient(app)
    client.cookies.set("studydy_session", token)
    return client


def _draft(client, key):
    response = client.post(
        "/v1/materials",
        json={"schema": "material-draft-create/v1", "display_name": key},
        headers={**HEADERS, "Idempotency-Key": key},
    )
    assert response.status_code == 201, response.text
    return response.json()["material_id"]


def _fake_stt(monkeypatch, segments=TRANSCRIPT):
    calls = []

    def transcribe(job, speech, check):
        check()
        calls.append({"job": dict(job), "speech": speech[:4]})
        return {"segments": segments, "language": "zh", "provider": "fake-stt", "device": "test"}

    monkeypatch.setattr(media_sources, "transcribe_speech", transcribe)
    return calls


def _publish(closed_loop, material_id, normalization_id, key, monkeypatch, tmp_path):
    """沿正式來源綁定與分析流程發布，語意模型以替身回傳第一個 Evidence。"""
    from pdf_evidence import material_pipeline

    learner, _, settings, _, dsn, _ = closed_loop
    owner = learner.learner_id
    run = create_revision(owner, material_id, [normalization_id], key, settings, dsn=dsn)
    claim = claim_next_material_processing_run(dsn=dsn)
    assert claim.run.run_id == run.run_id
    for stage in ("evidence", "semantics", "publishing"):
        _record_progress(run.run_id, stage, 1, 1, dsn=dsn)
    monkeypatch.setattr(material_pipeline, "material_request_fits", lambda *_, **__: True)
    with open_verified_artifact(owner, run.source_artifact_id, dsn=dsn) as pdf:
        source_path = tmp_path / f"{key}.pdf"
        source_path.write_bytes(pdf.file.read())
        source_sha = pdf.sha256

    def semantics(_client, **request):
        handles = [item[0] for section in request["request"]["sections"] for item in section["evidence"]]
        return {
            "concepts": [{"l": "資料結構", "a": [], "c": [{"m": "堆疊與佇列", "s": handles}]}],
            "relations": [],
        }

    document = material_pipeline.analyze_material(
        [{"media_type": "application/pdf", "source_path": str(source_path), "expected_source_sha256": source_sha}],
        _input(owner, run.run_id, dsn=dsn),
        settings,
        run_id=str(run.run_id),
        semantic_call=semantics,
    )
    document = bind_structure_input(owner, run.run_id, document, dsn=dsn)
    return document, publish_fixture_structure(owner, material_id, run.run_id, document, dsn=dsn)


def test_chunked_audio_becomes_time_pages_and_plays_from_the_cited_segment(closed_loop, monkeypatch, tmp_path):
    learner, _, _, _, dsn, _ = closed_loop
    owner = learner.learner_id
    client = _client(closed_loop, upload_max_bytes=1024 * 1024)
    material = _draft(client, "lecture-audio")
    data = _wave(200)
    part = 1024 * 1024
    path = f"/v1/materials/{material}/source-parts"

    def send(offset, chunk, key="audio-upload", total=len(data)):
        return client.post(
            path,
            content=chunk,
            headers={
                **HEADERS,
                "Idempotency-Key": key,
                "Content-Type": "audio/wav",
                "X-Material-Name": "lecture.wav",
                "X-Upload-Offset": str(offset),
                "X-Upload-Total": str(total),
            },
        )

    first = send(0, data[:part])
    assert first.status_code == 202 and first.json() == {
        "schema": "source-upload-progress/v1", "received_bytes": part, "source_id": None,
    }
    skipped = send(2 * part, data[2 * part : 3 * part])
    assert skipped.status_code == 409 and skipped.json()["reason_code"] == "UPLOAD_OFFSET_MISMATCH"
    assert read_sources(owner, material, dsn=dsn)["sources"] == []
    for offset in range(part, len(data), part):
        response = send(offset, data[offset : offset + part])
        assert response.status_code == 202, response.text
    source_id = response.json()["source_id"]
    assert source_id and response.json()["received_bytes"] == len(data)
    # 回應遺失後重送最後一段：不重複建立來源。
    assert send(offset, data[offset:]).json()["source_id"] == source_id
    assert not list((media_sources.work_root() / "uploads").iterdir())
    too_large = send(0, b"x", key="too-large", total=media_sources.MAX_AUDIO_BYTES + 1)
    assert too_large.status_code == 413

    calls = _fake_stt(monkeypatch)
    assert normalize_next(dsn=dsn)
    source, = read_sources(owner, material, dsn=dsn)["sources"]
    assert source["status"] == "ready", source["error_code"]
    assert source["media_type"] == "audio/wav" and source["page_count"] == 2
    # 送到 provider 的是沙箱轉好的 Opus，並帶工作 lease 以便取消。
    assert calls[0]["speech"] == b"OggS" and set(calls[0]["job"]) == {"job_id", "job_token"}
    with open_verified_artifact(owner, source["normalized_artifact_id"], dsn=dsn) as pdf:
        with pymupdf.open(stream=pdf.file.read(), filetype="pdf") as document:
            assert "後進先出" in document[0].get_text() and "先進先出" in document[1].get_text()

    media = client.get(f"/v1/artifacts/{source['original_artifact_id']}/media", headers={"Range": "bytes=0-99"})
    assert media.status_code == 206 and media.headers["content-type"] == "audio/wav"
    assert media.content == data[:100]
    foreign = TestClient(client.app)
    assert foreign.get(f"/v1/artifacts/{source['original_artifact_id']}/media").status_code == 401

    document, published = _publish(closed_loop, material, source["normalization_id"], "audio-run", monkeypatch, tmp_path)
    second = next(item for item in document["evidence"] if item["page"] == 2)
    resolved = resolve_evidence_source(owner, material, published.revision, second["evidence_id"], dsn=dsn)
    assert resolved["format"] == "audio" and resolved["normalized_page"] == 2
    assert resolved["label"] == "02:00–03:20（第 2 段逐字稿）"
    assert resolved["playback"] == {
        "kind": "audio",
        "url": f"/v1/artifacts/{source['original_artifact_id']}/media",
        "start_seconds": 120,
        "end_seconds": 200.0,
    }
    api.EvidenceSourceView.model_validate({"schema": "evidence-source/v1", **{k: v for k, v in resolved.items() if k != "schema"}})


def test_video_upload_keeps_only_the_audio_track(closed_loop, tmp_path):
    learner, _, _, _, dsn, _ = closed_loop
    owner = learner.learner_id
    client = _client(closed_loop)
    material = _draft(client, "lecture-video")
    video = tmp_path / "lecture.mp4"
    _video(video)
    response = client.post(
        f"/v1/materials/{material}/sources",
        content=video.read_bytes(),
        headers={**HEADERS, "Idempotency-Key": "video", "Content-Type": "video/mp4", "X-Material-Name": "lecture.mp4"},
    )
    assert response.status_code == 202, response.text
    source, = response.json()["sources"]
    assert source["media_type"] == "video/mp4" and source["original_name"] == "lecture.mp4"
    with database_session(dsn) as session:
        artifact = session.get(Artifact, source["original_artifact_id"])
        assert artifact.media_type == "audio/mp4" and artifact.size_bytes < video.stat().st_size
    with open_verified_artifact(owner, artifact.artifact_id, dsn=dsn) as stored:
        with av.open(io.BytesIO(stored.file.read())) as container:
            assert [stream.type for stream in container.streams] == ["audio"]
    download = client.get(f"/v1/artifacts/{source['original_artifact_id']}/download")
    assert download.status_code == 200 and download.headers["content-type"] == "audio/mp4"
    assert download.headers["content-disposition"].endswith("lecture.m4a")
    rejected = client.post(
        f"/v1/materials/{material}/sources",
        content=b"{}",
        headers={**HEADERS, "Idempotency-Key": "reference", "Content-Type": "application/vnd.studydy.youtube+json", "X-Material-Name": "x.youtube"},
    )
    assert rejected.status_code == 415


def _png(text):
    page = pymupdf.open().new_page(width=300, height=200)
    page.insert_text((20, 100), text, fontsize=24)
    return page.get_pixmap().tobytes("png")


def test_image_set_becomes_one_source_with_one_page_per_image(closed_loop):
    learner, _, _, _, dsn, _ = closed_loop
    owner = learner.learner_id
    client = _client(closed_loop)
    material = _draft(client, "slides")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("0001.png", _png("Stack"))
        archive.writestr("0002.png", _png("Queue"))
    response = client.post(
        f"/v1/materials/{material}/sources",
        content=buffer.getvalue(),
        headers={**HEADERS, "Idempotency-Key": "images", "Content-Type": "application/zip", "X-Material-Name": quote("Stack 等 2 張圖片.zip")},
    )
    assert response.status_code == 202, response.text
    assert normalize_next(dsn=dsn)
    source, = read_sources(owner, material, dsn=dsn)["sources"]
    assert source["status"] == "ready" and source["page_count"] == 2
    with open_verified_artifact(owner, source["normalized_artifact_id"], dsn=dsn) as pdf:
        with pymupdf.open(stream=pdf.file.read(), filetype="pdf") as document:
            assert [len(page.get_images()) for page in document] == [1, 1]


def _youtube(monkeypatch, **changes):
    info = {
        "id": "iG9CE55wbtY",
        "title": "資料結構/堆疊與佇列",
        "channel": "公開課程",
        "duration": 200,
        "live_status": "not_live",
        "availability": "public",
        "age_limit": 0,
        "language": "zh-TW",
        "subtitles": {"zh-TW": [{"ext": "vtt", "url": "https://example.invalid/zh-TW.vtt"}]},
        **changes,
    }
    monkeypatch.setattr(media_sources, "youtube_info", lambda video_id: info)
    return info


def _add_youtube(client, material, url, key="youtube"):
    return client.post(
        f"/v1/materials/{material}/youtube-sources",
        json={"schema": "youtube-source-create/v1", "url": url},
        headers={**HEADERS, "Idempotency-Key": key},
    )


def test_youtube_uses_uploader_subtitles_and_stores_only_reference_and_transcript(closed_loop, monkeypatch, tmp_path):
    learner, _, _, _, dsn, _ = closed_loop
    owner = learner.learner_id
    client = _client(closed_loop)
    material = _draft(client, "youtube-subtitles")
    _youtube(monkeypatch)
    monkeypatch.setattr(media_sources, "_subtitle_segments", lambda info, language: TRANSCRIPT)
    monkeypatch.setattr(media_sources, "_download_audio", lambda *_: pytest.fail("subtitles must avoid audio download"))
    response = _add_youtube(client, material, "https://youtu.be/iG9CE55wbtY")
    assert response.status_code == 202, response.text
    source, = response.json()["sources"]
    assert source["original_name"] == "資料結構／堆疊與佇列.youtube"
    assert normalize_next(dsn=dsn)
    source, = read_sources(owner, material, dsn=dsn)["sources"]
    assert source["status"] == "ready" and source["page_count"] == 2
    with database_session(dsn) as session:
        kinds = sorted(
            (row.kind, row.media_type)
            for row in session.scalars(select(Artifact).where(Artifact.material_id == material))
        )
    assert kinds == [
        ("normalized_pdf", "application/pdf"),
        ("original", "application/vnd.studydy.youtube+json"),
        ("source_mapping", "application/json"),
    ]
    with open_verified_artifact(owner, source["original_artifact_id"], dsn=dsn) as stored:
        assert json.loads(stored.file.read())["video_id"] == "iG9CE55wbtY"
    document, published = _publish(closed_loop, material, source["normalization_id"], "youtube-run", monkeypatch, tmp_path)
    first = next(item for item in document["evidence"] if item["page"] == 1)
    resolved = resolve_evidence_source(owner, material, published.revision, first["evidence_id"], dsn=dsn)
    assert resolved["format"] == "youtube" and resolved["label"] == "00:00–02:00（第 1 段逐字稿）"
    assert resolved["playback"] == {"kind": "youtube", "video_id": "iG9CE55wbtY", "start_seconds": 0, "end_seconds": 120}


def test_youtube_without_uploader_subtitles_transcribes_then_deletes_the_audio(closed_loop, monkeypatch):
    learner, _, _, _, dsn, _ = closed_loop
    owner = learner.learner_id
    client = _client(closed_loop)
    material = _draft(client, "youtube-audio")
    _youtube(monkeypatch, subtitles={}, automatic_captions={"zh-TW": [{"ext": "vtt", "url": "x"}]})
    downloaded = []

    def download(video_id, work, check):
        check()
        path = work / "youtube-audio.wav"
        path.write_bytes(_wave(150))
        downloaded.append(path)
        return path

    monkeypatch.setattr(media_sources, "_download_audio", download)
    calls = _fake_stt(monkeypatch)
    assert _add_youtube(client, material, "https://www.youtube.com/shorts/iG9CE55wbtY").status_code == 202
    assert normalize_next(dsn=dsn)
    source, = read_sources(owner, material, dsn=dsn)["sources"]
    assert source["status"] == "ready", source["error_code"]
    assert len(calls) == 1 and not downloaded[0].exists()
    assert not [path for path in media_sources.work_root().iterdir() if path.name != "uploads"]
    with database_session(dsn) as session:
        mapping_id = session.scalar(
            select(Artifact.artifact_id).where(Artifact.material_id == material, Artifact.kind == "source_mapping")
        )
    with open_verified_artifact(owner, mapping_id, dsn=dsn) as stored:
        mapping = json.loads(stored.file.read())
    assert mapping["media"]["transcript_source"] == "whisper" and mapping["media"]["stt"] == "fake-stt"


@pytest.mark.parametrize(
    "url, changes, status, reason",
    [
        ("https://www.youtube.com/playlist?list=PL0123456789", {}, 422, "YOUTUBE_PLAYLIST_REJECTED"),
        ("https://www.youtube.com/watch?v=iG9CE55wbtY", {"live_status": "is_live"}, 422, "YOUTUBE_LIVE"),
        ("https://www.youtube.com/watch?v=iG9CE55wbtY", {"availability": "subscriber_only"}, 422, "YOUTUBE_MEMBERS_ONLY"),
        ("https://www.youtube.com/watch?v=iG9CE55wbtY", {"age_limit": 18}, 422, "YOUTUBE_AGE_RESTRICTED"),
        ("https://www.youtube.com/watch?v=iG9CE55wbtY", {"duration": 7300}, 422, "MEDIA_TOO_LONG"),
    ],
)
def test_youtube_restrictions_are_rejected_before_creating_a_source(closed_loop, monkeypatch, url, changes, status, reason):
    learner, _, _, _, dsn, _ = closed_loop
    client = _client(closed_loop)
    material = _draft(client, "youtube-rejected")
    _youtube(monkeypatch, **changes)
    response = _add_youtube(client, material, url)
    assert response.status_code == status and response.json()["reason_code"] == reason
    assert read_sources(learner.learner_id, material, dsn=dsn)["sources"] == []


def test_stt_failure_is_recorded_and_retry_uses_the_saved_audio(closed_loop, monkeypatch):
    learner, _, _, _, dsn, _ = closed_loop
    owner = learner.learner_id
    from runtime.source_normalization import retry_normalization, upload_source

    material = create_draft(owner, "retry", "retry-draft", dsn=dsn)
    upload_source(owner, material, _wave(10), "short.wav", "audio/wav", "retry-upload", dsn=dsn)

    def unavailable(job, speech, check):
        raise media_sources.SourceError("STT_PROVIDER_UNAVAILABLE")

    monkeypatch.setattr(media_sources, "transcribe_speech", unavailable)
    assert normalize_next(dsn=dsn)
    failed, = read_sources(owner, material, dsn=dsn)["sources"]
    assert failed["status"] == "failed" and failed["error_code"] == "STT_PROVIDER_UNAVAILABLE"
    retry_normalization(owner, material, failed["normalization_id"], dsn=dsn)
    _fake_stt(monkeypatch, [{"start": 1, "end": 2, "text": "重試後的逐字稿內容足夠長"}])
    assert normalize_next(dsn=dsn)
    ready, = read_sources(owner, material, dsn=dsn)["sources"]
    assert ready["status"] == "ready" and ready["page_count"] == 1


def test_stale_uploads_and_interrupted_conversion_files_are_removed(closed_loop):
    root = media_sources.work_root()
    folder = root / "uploads"
    folder.mkdir(exist_ok=True)
    stale, fresh = folder / "stale.part", folder / "fresh.part"
    stale.write_bytes(b"x")
    fresh.write_bytes(b"y")
    # 程序被終止時留下的轉換目錄（原檔副本、YouTube 音軌）與沙箱輸出。
    interrupted = root / "normalize-abc"
    interrupted.mkdir()
    (interrupted / "youtube-audio.m4a").write_bytes(b"audio")
    sandbox = root / "studydy-media-xyz"
    (sandbox / "output").mkdir(parents=True)
    running = root / "normalize-current"
    running.mkdir()
    old = time.time() - 2 * 24 * 3600
    for path in (stale, interrupted, sandbox):
        os.utime(path, (old, old))
    media_sources.cleanup_stale_uploads()
    assert not stale.exists() and fresh.exists()
    assert not interrupted.exists() and not sandbox.exists() and running.exists()
    assert folder.is_dir()


def test_artifact_constraints_allow_larger_audio_originals_only(closed_loop):
    learner, source, _, _, dsn, _ = closed_loop
    from uuid import uuid4

    def insert(kind, media, size):
        with psycopg.connect(dsn) as connection:
            connection.execute(
                """INSERT INTO artifacts (artifact_id,learner_id,material_id,kind,media_type,sha256,size_bytes,created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,now())""",
                (uuid4(), learner.learner_id, source.material_id, kind, media, bytes(32), size),
            )

    insert("original", "audio/mpeg", 300 * 1024 * 1024)
    insert("original", "application/zip", 100 * 1024 * 1024)
    insert("original", "application/vnd.studydy.youtube+json", 100)
    for kind, media, size, constraint in [
        ("original", "audio/mpeg", 300 * 1024 * 1024 + 1, "artifacts_size_bytes_check"),
        ("original", "application/pdf", 100 * 1024 * 1024 + 1, "artifacts_size_bytes_check"),
        ("podcast_audio", "audio/wav", 100 * 1024 * 1024 + 1, "artifacts_size_bytes_check"),
        ("original", "video/mp4", 1000, "artifact_role_media"),
    ]:
        with pytest.raises(psycopg.errors.CheckViolation, match=constraint):
            insert(kind, media, size)
