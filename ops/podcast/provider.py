#!/usr/bin/env python3
"""本機 Podcast provider：CosyVoice 自架語音，不接產品資料庫。

以專用 token 保護，只在本機／Docker gateway 監聽。教材不寫一般 log；
文字模型由後端直接呼叫 Gemma。
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from hmac import compare_digest
import json
import logging
from logging.handlers import RotatingFileHandler
from datetime import UTC, datetime
import time
from uuid import UUID
import os
from pathlib import Path
import sys
import tempfile
from threading import Lock, BoundedSemaphore

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend/src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from runtime import podcast_process


LOCK = Lock()
ASR_LOCK = Lock()
# 與後端影片 worker 數一致；同時渲染的集數各自使用 NVENC。
VIDEO_LOCK = BoundedSemaphore(int(os.environ.get("STUDYDY_VIDEO_WORKERS", "1")))


def failure_code(error):
    allowed = {
        "SCENE_ALIGNMENT_FAILED",
        "VOICE_TRANSCRIPT_INVALID",
        "MEDIA_TRANSCRIPT_FAILED",
        "PODCAST_AUDIO_INVALID",
        "PODCAST_CANCELLED",
        "VIDEO_AUDIO_INVALID",
        "VIDEO_CANCELLED",
        "VIDEO_SOURCE_CHANGED",
        "VIDEO_PROVIDER_UNAVAILABLE",
        "VIDEO_DISK_SPACE_LOW",
        "VIDEO_TRANSCRIPT_INVALID",
        "VIDEO_STORYBOARD_INVALID",
        "VIDEO_LAYOUT_INVALID",
        "VIDEO_STORYBOARD_NEEDS_REVIEW",
        "VIDEO_RENDER_FAILED",
        "VIDEO_TOO_LARGE",
        "VIDEO_ALIGNMENT_INVALID",
    }
    return str(error) if str(error) in allowed else "PODCAST_PROVIDER_FAILED"


def record_job(path, body, started, error):
    if not isinstance(body, dict) or "job_id" not in body:
        return
    try:
        identity = str(UUID(body["job_id"]))
    except (ValueError, TypeError, AttributeError):
        return
    index = body.get("episode_index")
    event = {
        "at": datetime.now(UTC).isoformat(),
        "job_id": identity,
        "operation": path,
        "episode_index": index if type(index) is int else None,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "result": failure_code(error) if error else "succeeded",
        "error_type": type(error).__name__ if error else None,
    }
    logging.getLogger("studydy.podcast.jobs").info(json.dumps(event))


def audio(body, *, with_metadata=False, cancelled=None):
    # Podcast 由使用者取消或程序結果結束，不用固定秒數終止長音訊。
    with tempfile.TemporaryDirectory(prefix="studydy-podcast-audio-") as directory:
        output = Path(directory) / "audio.wav"
        result = podcast_process.run(
            [
                os.environ["STUDYDY_PODCAST_TTS_PYTHON"],
                str(Path(__file__).with_name("synthesize.py")),
                str(output),
            ],
            input=json.dumps(body, ensure_ascii=False),
            timeout=None,
            cancelled=cancelled,
            env={
                k: v
                for k, v in os.environ.items()
                if k != "STUDYDY_PODCAST_PROVIDER_TOKEN"
            },
        )
        if result.returncode != 0 or not output.is_file():
            raise RuntimeError("PODCAST_AUDIO_INVALID")
        data = output.read_bytes()
        if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
            raise RuntimeError("PODCAST_AUDIO_INVALID")
        metadata = None
        if body.get("purpose") == "podcast":
            metadata = json.loads(output.with_suffix(".json").read_text())
        return (data, metadata) if with_metadata else data


def transcribe(body, *, cancelled=None):
    import base64

    data = base64.b64decode(body["audio"], validate=True)
    if not 0 < len(data) <= 12 * 1024 * 1024:
        raise ValueError("REQUEST_INVALID")
    hints = body.get("hints", "")
    if (
        not isinstance(hints, str)
        or len(hints) > 320
        or any(ord(c) < 32 for c in hints)
    ):
        raise ValueError("REQUEST_INVALID")
    with tempfile.TemporaryDirectory(prefix="studydy-stt-") as temporary:
        output = Path(temporary) / "transcript.json"
        result = podcast_process.run(
            [
                os.environ["STUDYDY_STT_PYTHON"],
                str(Path(__file__).with_name("transcribe.py")),
                str(output),
            ],
            input=data,
            timeout=None,
            cancelled=cancelled,
            env={
                **{
                    k: v
                    for k, v in os.environ.items()
                    if k != "STUDYDY_PODCAST_PROVIDER_TOKEN"
                },
                "STUDYDY_STT_HINTS": hints,
            },
        )
        if result.returncode != 0 or not output.is_file():
            raise RuntimeError("VOICE_TRANSCRIPT_INVALID")
        return json.loads(output.read_text())


def media_transcribe(body, *, cancelled=None):
    """教材音訊、影片音軌與無字幕 YouTube 的逐字稿；音訊只在暫存目錄，完成即刪除。"""
    import base64

    data = base64.b64decode(body["audio"], validate=True)
    if not 0 < len(data) <= 48 * 1024 * 1024:
        raise ValueError("REQUEST_INVALID")
    with tempfile.TemporaryDirectory(prefix="studydy-media-stt-") as temporary:
        audio = Path(temporary) / "speech.ogg"
        output = Path(temporary) / "transcript.json"
        audio.write_bytes(data)
        del data
        result = podcast_process.run(
            [
                os.environ["STUDYDY_STT_PYTHON"],
                str(Path(__file__).with_name("transcribe_media.py")),
                str(audio),
                str(output),
            ],
            timeout=None,
            cancelled=cancelled,
            env={
                k: v
                for k, v in os.environ.items()
                if k != "STUDYDY_PODCAST_PROVIDER_TOKEN"
            },
        )
        if result.returncode != 0 or not output.is_file():
            raise RuntimeError("MEDIA_TRANSCRIPT_FAILED")
        return json.loads(output.read_text())


def align_audio(body, *, cancelled=None, max_texts=12):
    import base64

    data = base64.b64decode(body["audio"], validate=True)
    texts = body.get("texts")
    if (
        not 0 < len(data) <= 100 * 1024 * 1024
        or not isinstance(texts, list)
        or not 1 <= len(texts) <= max_texts
    ):
        raise ValueError("REQUEST_INVALID")
    with tempfile.TemporaryDirectory(prefix="studydy-align-") as temporary:
        wav = Path(temporary) / "audio.wav"
        output = Path(temporary) / "alignment.json"
        wav.write_bytes(data)
        result = podcast_process.run(
            [
                os.environ["STUDYDY_STT_PYTHON"],
                str(Path(__file__).with_name("align.py")),
                str(wav),
                str(output),
            ],
            input=json.dumps(
                {
                    "texts": texts,
                    **{
                        k: body[k]
                        for k in ("turn_indices", "caption_turns")
                        if k in body
                    },
                },
                ensure_ascii=False,
            ),
            timeout=None,
            cancelled=cancelled,
            env={
                k: v
                for k, v in os.environ.items()
                if k != "STUDYDY_PODCAST_PROVIDER_TOKEN"
            },
        )
        if result.returncode != 0 or not output.is_file():
            raise RuntimeError("SCENE_ALIGNMENT_FAILED")
        return json.loads(output.read_text())


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        expected = "Bearer " + os.environ["STUDYDY_PODCAST_PROVIDER_TOKEN"]
        if not compare_digest(self.headers.get("Authorization", ""), expected):
            self.send_error(401)
            return
        if self.path not in {
            "/audio",
            "/transcribe",
            "/media-transcribe",
            "/align",
            "/video",
            "/jobs/cancel",
            "/jobs/progress",
        }:
            self.send_error(404)
            return
        started, body, failure = time.monotonic(), None, None
        self.audio_queued_at = self.audio_started_at = None
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if (
                not 0
                < size
                <= (
                    140
                    if self.path in {"/align", "/video"}
                    else 66
                    if self.path == "/media-transcribe"
                    else 18
                )
                * 1024
                * 1024
            ):
                raise ValueError("REQUEST_INVALID")
            body = json.loads(self.rfile.read(size))
            if self.path == "/jobs/progress":
                self.respond(json.dumps(podcast_process.progress(body)).encode(), "application/json")
                return
            if self.path == "/jobs/cancel":
                self.respond(
                    json.dumps(podcast_process.cancel(body)).encode(),
                    "application/json",
                )
                return
            active_lock = VIDEO_LOCK if self.path == "/video" else LOCK if self.path in {"/audio", "/media-transcribe"} else ASR_LOCK
            with podcast_process.job(body, required=self.path == "/video") as cancelled:
                if self.path == "/audio":
                    self.audio_queued_at = time.monotonic()
                with podcast_process.tracked_lock(active_lock, cancelled, body, self.path):
                    if self.path == "/audio":
                        self.audio_started_at = time.monotonic()
                    self.generate(body, cancelled)
        except Exception as error:
            failure = error
            self.failed(error)
        finally:
            record_job(self.path, body, started, failure)

    def generate(self, body, cancelled):
        body = {
            key: value
            for key, value in body.items()
            if key not in {"job_id", "job_token"}
        }
        if self.path == "/video":
            import video_service

            # 分鏡由後端依 runtime lock 生成；provider 只做對齊切頁與渲染。
            result = (
                video_service.prepare(body, align=align_audio, asr_lock=ASR_LOCK, cancelled=cancelled)
                if body.get("stage") == "prepare"
                else video_service.render(body, cancelled=cancelled)
            )
            data, media = (
                json.dumps(result, ensure_ascii=False).encode(),
                "application/json",
            )
        elif self.path == "/align":
            data, media = (
                json.dumps(
                    align_audio(body, cancelled=cancelled), ensure_ascii=False
                ).encode(),
                "application/json",
            )
        elif self.path == "/media-transcribe":
            result = media_transcribe(body, cancelled=cancelled)
            data, media = (
                json.dumps(result, ensure_ascii=False).encode(),
                "application/json",
            )
        elif self.path == "/transcribe":
            result = transcribe(body, cancelled=cancelled)
            data, media = (
                json.dumps(result, ensure_ascii=False).encode(),
                "application/json",
            )
        else:
            data, audio_metadata = audio(body, with_metadata=True, cancelled=cancelled)
            media = "audio/wav"
        podcast_process.check(cancelled)
        self.respond(data, media, audio_metadata if self.path == "/audio" else None)

    def respond(self, data, media, audio_metadata=None):
        self.send_response(200)
        self.send_header("Content-Type", media)
        self.send_header("Content-Length", str(len(data)))
        if self.path == "/audio":
            self.send_header("X-Studydy-Audio-Timings", json.dumps({
                "tts_queue_ms": (self.audio_started_at - self.audio_queued_at) * 1000,
                "tts_synthesis_ms": (time.monotonic() - self.audio_started_at) * 1000,
            }))
            self.send_header(
                "X-Studydy-Audio-Provider",
                "Fun-CosyVoice3-0.5B-2512;rl-reference-vw;spoken-input/v15;dynamic-pauses/v2;podcast-mastering/v1",
            )
            if audio_metadata:
                self.send_header(
                    "X-Studydy-Audio-Mastering",
                    json.dumps(audio_metadata, separators=(",", ":")),
                )
        self.end_headers()
        self.wfile.write(data)

    def failed(self, error):
        code = failure_code(error)
        data = json.dumps({"error_code": code}).encode()
        self.send_response(502)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    # 程式碼可由核准分支部署，持久日誌仍由正式環境指定位置。
    job_log = Path(
        os.environ.get(
            "STUDYDY_PODCAST_JOB_LOG",
            str(Path(__file__).resolve().parents[2] / "data/podcast/job-events.jsonl"),
        )
    )
    job_log.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    logger = logging.getLogger("studydy.podcast.jobs")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(RotatingFileHandler(job_log, maxBytes=1024 * 1024, backupCount=2))
    token = os.environ.get("STUDYDY_PODCAST_PROVIDER_TOKEN", "")
    if len(token) < 32:
        raise SystemExit(
            "STUDYDY_PODCAST_PROVIDER_TOKEN must contain at least 32 characters"
        )
    server = ThreadingHTTPServer(
        (
            os.environ.get("STUDYDY_PODCAST_BIND", "127.0.0.1"),
            int(os.environ.get("STUDYDY_PODCAST_PORT", "18010")),
        ),
        Handler,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
