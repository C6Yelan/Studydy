"""圖片組、音訊、影片與 YouTube 來源。

- 大檔分段上傳：暫存在 artifact 磁碟的 `.media-work`，收齊後才建立來源。
- 影片只保存抽出的音軌；YouTube 只保存影片參照，逐字稿與分析結果另存。
- 轉檔工作沿用來源正規化的 lease；語音辨識送到本機 provider，不外送。
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import html
import json
import logging
import os
from pathlib import Path, PurePath
import re
import shutil
import stat
import tempfile
import time
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import select

from document_normalization.converter import (
    AUDIO_MIME,
    IMAGE_SET_MIME,
    MAX_AUDIO_BYTES,
    MAX_FILE_BYTES,
    MAX_MEDIA_SECONDS,
    MAX_VIDEO_BYTES,
    MIME,
    VIDEO_MIME,
    YOUTUBE_MIME,
    NormalizationError,
    convert_images,
    media_step,
    render_transcript,
)
from .errors import SourceError
from .generation_jobs import cancellable_job, check_job
from .provider_http import generation_response, provider_request
from .storage.artifacts import _root, open_verified_artifact
from .storage.database import database_session
from .storage.materials import require_source_material
from .storage.tables import MaterialSource, SourceNormalization

_UPLOAD_STALE_SECONDS = 24 * 60 * 60
_YOUTUBE_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
}
# 上傳者字幕優先序：繁體中文 → 簡體中文 → 影片原語言。自動字幕一律不用。
_TRADITIONAL = ("zh-Hant", "zh-TW", "zh-HK", "zh-MO")
_SIMPLIFIED = ("zh-Hans", "zh-CN", "zh-SG", "zh")


def upload_limit(name, media):
    """依副檔名與宣告型別決定可接收的大小；YouTube 參照不能由上傳建立。"""
    extension = PurePath(name).suffix.lower()
    if MIME.get(extension) == media or (
        extension == ".zip" and media == IMAGE_SET_MIME
    ):
        return MAX_FILE_BYTES
    if AUDIO_MIME.get(extension) == media:
        return MAX_AUDIO_BYTES
    if VIDEO_MIME.get(extension) == media:
        return MAX_VIDEO_BYTES
    raise SourceError("UNSUPPORTED_MEDIA_TYPE")


def work_root():
    """大型暫存放在 artifact 磁碟；容器 /tmp 只有 1 GiB。"""
    path = _root() / ".media-work"
    path.mkdir(mode=0o700, exist_ok=True)
    details = path.stat(follow_symlinks=False)
    if not stat.S_ISDIR(details.st_mode) or stat.S_IMODE(details.st_mode) != 0o700:
        raise SourceError("STORAGE_UNAVAILABLE")
    return path


def _upload_paths(owner, material_id, key):
    digest = hashlib.sha256(f"{owner}:{material_id}:{key}".encode()).hexdigest()
    folder = work_root() / "uploads"
    folder.mkdir(mode=0o700, exist_ok=True)
    return folder / f"{digest}.part", folder / f"{digest}.json"


def _existing_source(owner, material_id, key, *, dsn):
    from .storage.artifacts import _key_digest

    with database_session(dsn) as session:
        return session.scalar(
            select(MaterialSource.source_id).where(
                MaterialSource.learner_id == owner,
                MaterialSource.material_id == material_id,
                MaterialSource.idempotency_key_sha256 == _key_digest(key),
                MaterialSource.removed_at.is_(None),
            )
        )


def receive_chunk(owner, material_id, key, name, media, total, offset, chunk, *, dsn=None):
    """依序接收分段；最後一段寫入後建立來源。回傳已收到的位元組數與來源 ID。

    同一意圖重送最後一段時，若來源已建立便直接回傳，不重複建立。
    """
    from .source_normalization import _name

    name = _name(name)
    limit = upload_limit(name, media)
    if (
        type(total) is not int
        or type(offset) is not int
        or not 0 < total <= limit
        or not 0 <= offset < total
        or not chunk
        or offset + len(chunk) > total
    ):
        raise SourceError("MATERIAL_TOO_LARGE" if total > limit else "REQUEST_INVALID")
    with database_session(dsn) as session:
        require_source_material(session, owner, material_id)
    existing = _existing_source(owner, material_id, key, dsn=dsn)
    if existing is not None:
        return {"received_bytes": total, "source_id": existing}
    part, meta = _upload_paths(owner, material_id, key)
    intent = {"name": name, "media_type": media, "total_bytes": total}
    descriptor = os.open(meta, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        # 同時重送最後一段時，另一個請求可能剛建立完來源。
        existing = _existing_source(owner, material_id, key, dsn=dsn)
        if existing is not None:
            part.unlink(missing_ok=True)
            meta.unlink(missing_ok=True)
            return {"received_bytes": total, "source_id": existing}
        raw = os.read(descriptor, 4096)
        try:
            stored = json.loads(raw) if raw else None
        except ValueError:
            stored = None
        if offset == 0:
            os.ftruncate(descriptor, 0)
            os.lseek(descriptor, 0, os.SEEK_SET)
            os.write(descriptor, json.dumps(intent).encode())
            part.unlink(missing_ok=True)
        elif stored != intent:
            raise SourceError("UPLOAD_OFFSET_MISMATCH")
        received = part.stat().st_size if part.exists() else 0
        if received != offset:
            raise SourceError("UPLOAD_OFFSET_MISMATCH")
        with part.open("ab") as stream:
            stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        received += len(chunk)
        if received < total:
            return {"received_bytes": received, "source_id": None}
        try:
            source_id = store_upload(owner, material_id, part, name, media, key, dsn=dsn)
        finally:
            part.unlink(missing_ok=True)
            meta.unlink(missing_ok=True)
        return {"received_bytes": total, "source_id": source_id}
    finally:
        os.close(descriptor)


def store_upload_bytes(owner, material_id, data, name, media, key, *, dsn=None):
    """單次上傳的音訊／影片同樣先檢查長度或抽音軌。"""
    with tempfile.NamedTemporaryFile(
        dir=work_root(), suffix=PurePath(name).suffix.lower()
    ) as stream:
        stream.write(data)
        stream.flush()
        return store_upload(owner, material_id, Path(stream.name), name, media, key, dsn=dsn)


def store_upload(owner, material_id, path, name, media, key, *, dsn=None):
    from .source_normalization import upload_source

    if path.stat().st_size > upload_limit(name, media):
        raise SourceError("MATERIAL_TOO_LARGE")
    try:
        if media in VIDEO_MIME.values():
            # 原影片不保存；只把音軌交給 artifact store。
            with media_step("extract", path, work_dir=work_root()) as output:
                data = (output / "audio.m4a").read_bytes()
        elif media in AUDIO_MIME.values():
            with media_step("probe", path, work_dir=work_root()):
                pass
            data = path.read_bytes()
        else:
            data = path.read_bytes()
    except NormalizationError as error:
        raise SourceError(str(error)) from None
    if len(data) > MAX_AUDIO_BYTES:
        raise SourceError("MATERIAL_TOO_LARGE")
    return upload_source(owner, material_id, data, name, media, key, dsn=dsn)


def cleanup_stale_uploads(*, now=None):
    """中斷超過一天的分段上傳視為放棄；只刪暫存，不影響已建立來源。"""
    folder = work_root() / "uploads"
    if not folder.is_dir():
        return
    limit = (now or time.time()) - _UPLOAD_STALE_SECONDS
    for path in folder.iterdir():
        try:
            if path.stat(follow_symlinks=False).st_mtime < limit:
                path.unlink()
        except FileNotFoundError:
            continue


# ---------- YouTube ----------


def youtube_video_id(url):
    """只接受單支影片網址；純播放清單拒絕，影片網址附帶的清單參數忽略。"""
    if not isinstance(url, str) or len(url) > 2048 or any(ord(c) < 33 for c in url):
        raise SourceError("YOUTUBE_URL_INVALID")
    parsed = urlsplit(url if "://" in url else "https://" + url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or host not in _YOUTUBE_HOSTS:
        raise SourceError("YOUTUBE_URL_INVALID")
    parts = [part for part in parsed.path.split("/") if part]
    query = parse_qs(parsed.query)
    if host.endswith("youtu.be"):
        candidate = parts[0] if parts else ""
    elif parts[:1] == ["watch"]:
        candidate = (query.get("v") or [""])[0]
    elif len(parts) >= 2 and parts[0] in ("shorts", "embed", "live", "v"):
        candidate = parts[1]
    elif parts[:1] == ["playlist"] or "list" in query:
        raise SourceError("YOUTUBE_PLAYLIST_REJECTED")
    else:
        raise SourceError("YOUTUBE_URL_INVALID")
    if not _YOUTUBE_ID.fullmatch(candidate):
        raise SourceError("YOUTUBE_URL_INVALID")
    return candidate


class _QuietLogger:
    def debug(self, message):
        pass

    def info(self, message):
        pass

    def warning(self, message):
        pass

    def error(self, message):
        pass


def _youtube_options(**extra):
    return {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "cachedir": False,
        "socket_timeout": 30,
        "extractor_retries": 1,
        "logger": _QuietLogger(),
        **extra,
    }


def _youtube_error(message):
    text = message.lower()
    for markers, code in (
        (("private video",), "YOUTUBE_PRIVATE"),
        (("members-only", "members only", "join this channel"), "YOUTUBE_MEMBERS_ONLY"),
        (("confirm your age", "age-restricted", "inappropriate for some users"), "YOUTUBE_AGE_RESTRICTED"),
        (("live event", "premieres in", "is live", "live stream"), "YOUTUBE_LIVE"),
    ):
        if any(marker in text for marker in markers):
            return code
    return "YOUTUBE_UNAVAILABLE"


def youtube_info(video_id):
    import yt_dlp

    try:
        with yt_dlp.YoutubeDL(_youtube_options(skip_download=True)) as client:
            info = client.extract_info(
                f"https://www.youtube.com/watch?v={video_id}", download=False
            )
    except yt_dlp.utils.DownloadError as error:
        raise SourceError(_youtube_error(str(error))) from None
    except Exception:
        raise SourceError("YOUTUBE_UNAVAILABLE") from None
    if not isinstance(info, dict) or info.get("id") != video_id:
        raise SourceError("YOUTUBE_UNAVAILABLE")
    return info


def check_youtube_info(info):
    """直播、會員限定、私人、年齡限制與超過 2 小時的影片拒絕，並保留具體原因。"""
    if info.get("_type") == "playlist":
        raise SourceError("YOUTUBE_PLAYLIST_REJECTED")
    if info.get("live_status") in ("is_live", "is_upcoming", "post_live"):
        raise SourceError("YOUTUBE_LIVE")
    availability = info.get("availability")
    if availability == "private":
        raise SourceError("YOUTUBE_PRIVATE")
    if availability in ("subscriber_only", "premium_only", "needs_auth"):
        raise SourceError("YOUTUBE_MEMBERS_ONLY")
    if (info.get("age_limit") or 0) >= 18:
        raise SourceError("YOUTUBE_AGE_RESTRICTED")
    duration = info.get("duration")
    if not isinstance(duration, (int, float)) or duration <= 0:
        raise SourceError("YOUTUBE_UNAVAILABLE")
    if duration > MAX_MEDIA_SECONDS:
        raise SourceError("MEDIA_TOO_LONG")


def _display_title(info):
    title = " ".join(str(info.get("title") or info["id"]).split())
    title = "".join("／" if char in "/\\" else char for char in title if ord(char) >= 32)
    return title[:180] or info["id"]


def add_youtube_source(owner, material_id, url, key, *, dsn=None):
    from .source_normalization import upload_source

    video_id = youtube_video_id(url)
    with database_session(dsn) as session:
        require_source_material(session, owner, material_id)
    info = youtube_info(video_id)
    check_youtube_info(info)
    reference = {
        "schema": "youtube-source/v1",
        "video_id": video_id,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "title": _display_title(info),
        "channel": str(info.get("channel") or info.get("uploader") or "")[:200],
        "duration_seconds": float(info["duration"]),
    }
    data = json.dumps(reference, ensure_ascii=False, sort_keys=True).encode()
    return upload_source(
        owner, material_id, data, reference["title"] + ".youtube", YOUTUBE_MIME, key, dsn=dsn
    )


def pick_subtitle(info):
    """只看上傳者字幕；回傳語言代碼，沒有符合的字幕時回傳 None。"""
    tracks = {
        language: formats
        for language, formats in (info.get("subtitles") or {}).items()
        if language != "live_chat"
        and any(item.get("ext") == "vtt" and item.get("url") for item in formats or [])
    }

    def first(candidates):
        for candidate in candidates:
            for language in tracks:
                if language == candidate or language.startswith(candidate + "-"):
                    return language
        return None

    original = info.get("language")
    return (
        first(_TRADITIONAL)
        or first(_SIMPLIFIED)
        or (first((original,)) if isinstance(original, str) and original else None)
    )


_CUE_TIME = re.compile(
    r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{3})\s+-->\s+(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{3})"
)


def _seconds(hours, minutes, seconds, millis):
    return int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000


def parse_vtt(text):
    """把 WebVTT 轉成逐字稿片段；移除樣式標籤、合併連續相同字幕。"""
    segments = []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    index = 0
    while index < len(lines):
        match = _CUE_TIME.search(lines[index])
        index += 1
        if not match:
            continue
        body = []
        while index < len(lines) and lines[index].strip():
            body.append(lines[index])
            index += 1
        words = html.unescape(re.sub(r"<[^>]*>", "", " ".join(body)))
        words = " ".join(words.split())
        if not words:
            continue
        start = _seconds(*match.groups()[:4])
        end = _seconds(*match.groups()[4:])
        if segments and segments[-1]["text"] == words:
            segments[-1]["end"] = max(segments[-1]["end"], end)
            continue
        segments.append({"start": start, "end": max(start, end), "text": words})
    return segments


def _subtitle_segments(info, language):
    import yt_dlp

    track = next(
        item for item in info["subtitles"][language] if item.get("ext") == "vtt"
    )
    try:
        with yt_dlp.YoutubeDL(_youtube_options()) as client:
            with client.urlopen(track["url"]) as response:
                data = response.read(16 * 1024 * 1024 + 1)
    except Exception:
        raise SourceError("YOUTUBE_UNAVAILABLE") from None
    if len(data) > 16 * 1024 * 1024:
        raise SourceError("TRANSCRIPT_INVALID")
    segments = parse_vtt(data.decode("utf-8", errors="replace"))
    if not segments:
        raise SourceError("TRANSCRIPT_EMPTY")
    return segments


def _download_audio(video_id, work, check):
    """沒有可用字幕時暫存音軌，只用於本機辨識；呼叫端結束時刪除整個暫存目錄。"""
    import yt_dlp

    def progress(_status):
        check()

    options = _youtube_options(
        format="bestaudio[ext=m4a]/bestaudio[acodec!=none]",
        outtmpl=str(work / "youtube-audio.%(ext)s"),
        max_filesize=MAX_AUDIO_BYTES,
        progress_hooks=[progress],
    )
    try:
        with yt_dlp.YoutubeDL(options) as client:
            client.download([f"https://www.youtube.com/watch?v={video_id}"])
    except yt_dlp.utils.DownloadError as error:
        raise SourceError(_youtube_error(str(error))) from None
    files = sorted(work.glob("youtube-audio.*"))
    if len(files) != 1 or files[0].suffix in (".part", ".ytdl"):
        raise SourceError("YOUTUBE_UNAVAILABLE")
    return files[0]


# ---------- 語音辨識與正規化 ----------


def transcribe_speech(job, speech, check):
    """呼叫本機 provider 的 /media-transcribe；連線後等待辨識完成，取消時中斷連線。"""
    request = provider_request(
        "/media-transcribe",
        {**job, "audio": base64.b64encode(speech).decode("ascii")},
        unavailable=SourceError("STT_PROVIDER_UNAVAILABLE"),
    )
    try:
        with generation_response(request) as response:
            return json.loads(response.read(64 * 1024 * 1024))
    except SourceError:
        raise
    except Exception as error:
        check()
        status = getattr(error, "code", None)
        raise SourceError(
            "STT_FAILED" if isinstance(status, int) else "STT_PROVIDER_UNAVAILABLE"
        ) from None


def _transcribe(path, work, job, check):
    """壓成 16 kHz Opus 後交給本機 provider；回傳逐字稿片段、語言與音訊長度。"""
    try:
        with media_step("stt", path, work_dir=work, cancellation_check=check) as output:
            speech = (output / "speech.ogg").read_bytes()
            duration = json.loads((output / "probe.json").read_text())["duration_seconds"]
    except NormalizationError as error:
        raise SourceError(str(error)) from None
    started = time.monotonic()
    result = transcribe_speech(job, speech, check)
    del speech
    segments = result.get("segments") if isinstance(result, dict) else None
    if not isinstance(segments, list) or not all(
        isinstance(item, dict)
        and isinstance(item.get("text"), str)
        and isinstance(item.get("start"), (int, float))
        and isinstance(item.get("end"), (int, float))
        for item in segments
    ):
        raise SourceError("STT_FAILED")
    logging.getLogger(__name__).info(
        "MEDIA_TRANSCRIBED audio_seconds=%.0f seconds=%.1f device=%s",
        duration,
        time.monotonic() - started,
        result.get("device", "unknown"),
    )
    return (
        [
            {"start": item["start"], "end": item["end"], "text": item["text"]}
            for item in segments
        ],
        str(result.get("language") or "")[:16],
        duration,
        str(result.get("provider") or "")[:120],
    )


def _artifact_copy(owner, artifact_id, target, *, dsn):
    """沙箱需要檔案路徑；複製時同時核對 artifact 的 bytes。"""
    with open_verified_artifact(owner, artifact_id, dsn=dsn) as source:
        with target.open("xb") as stream:
            shutil.copyfileobj(source.file, stream, 1024 * 1024)
        return source.sha256


def normalize_media(identity, token, owner, original_id, name, media, policy, *, dsn=None):
    """圖片組交給 OCR 流程；音訊、影片音軌與 YouTube 先取得逐字稿再排成時間段頁面。"""

    def check():
        check_job(SourceNormalization, identity, token, dsn=dsn)

    if media == IMAGE_SET_MIME:
        with open_verified_artifact(owner, original_id, dsn=dsn) as source:
            data = source.file.read()
        return convert_images(data, policy, cancellation_check=check)
    with cancellable_job(SourceNormalization, identity, token, dsn=dsn) as job, (
        tempfile.TemporaryDirectory(prefix="normalize-", dir=work_root())
    ) as temporary:
        work = Path(temporary)
        if media == YOUTUBE_MIME:
            with open_verified_artifact(owner, original_id, dsn=dsn) as source:
                reference = json.loads(source.file.read(65536))
                original_sha256 = source.sha256
            transcript = _youtube_transcript(reference, work, job, check)
        else:
            path = work / ("source" + PurePath(name).suffix.lower())
            original_sha256 = _artifact_copy(owner, original_id, path, dsn=dsn)
            segments, language, duration, provider = _transcribe(path, work, job, check)
            path.unlink()
            transcript = {
                "format": "video" if media in VIDEO_MIME.values() else "audio",
                "duration_seconds": duration,
                "media": {
                    "transcript_source": "whisper",
                    "language": language,
                    "stt": provider,
                },
                "segments": segments,
            }
        try:
            return render_transcript(
                {
                    "schema": "media-transcript/v1",
                    "original_sha256": original_sha256,
                    **transcript,
                },
                policy,
                work_dir=work,
                cancellation_check=check,
            )
        except NormalizationError as error:
            raise SourceError(str(error)) from None


def _youtube_transcript(reference, work, job, check):
    video_id = reference["video_id"]
    if not _YOUTUBE_ID.fullmatch(str(video_id)):
        raise SourceError("YOUTUBE_URL_INVALID")
    info = youtube_info(video_id)
    check_youtube_info(info)
    language = pick_subtitle(info)
    media = {
        "video_id": video_id,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "title": reference.get("title", ""),
    }
    duration = float(info["duration"])
    if language:
        segments = _subtitle_segments(info, language)
        media.update(transcript_source="uploader_subtitles", language=language)
    else:
        audio = _download_audio(video_id, work, check)
        segments, detected, duration, provider = _transcribe(audio, work, job, check)
        # 只保存逐字稿與分析結果；辨識完成立即刪除音軌。
        audio.unlink()
        media.update(transcript_source="whisper", language=detected, stt=provider)
    check()
    return {
        "format": "youtube",
        "duration_seconds": duration,
        "media": media,
        "segments": segments,
    }
