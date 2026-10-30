#!/usr/bin/env python3
"""本機 Podcast provider：Luna 文字替代測試與 CosyVoice 自架語音，不接產品資料庫。

以專用 token 保護，只在本機／Docker gateway 監聽。教材不寫一般 log；
Codex 使用 ephemeral、唯讀空目錄及停用工具，憑證只由主機 CLI 繼承。
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
import subprocess
import sys
import tempfile
from threading import Lock, BoundedSemaphore

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend/src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from runtime import podcast_process
import luna_call_log


MODEL = "gpt-5.6-luna"
LOCK = Lock()
TEXT_LOCK = Lock()
# 教材子圖彼此獨立；其他文字功能仍沿用原串行鎖。
MATERIAL_SLOTS = BoundedSemaphore(8)
# 暫時測試模式：分析與審查共用兩槽，另留 TEXT_LOCK 一槽給互動／其他文字功能。
TEST_BACKGROUND_SLOTS = (
    BoundedSemaphore(2) if os.environ.get("STUDYDY_LUNA_PARALLEL_TEST") == "1" else None
)
ASR_LOCK = Lock()
VIDEO_LOCK = Lock()
CALL_LOG_LOCK = Lock()


def failure_code(error):
    allowed = {
        "SCENE_ALIGNMENT_FAILED",
        "LUNA_GENERATION_TIMEOUT",
        "VOICE_TRANSCRIPT_INVALID",
        "MEDIA_TRANSCRIPT_FAILED",
        "PODCAST_AUDIO_INVALID",
        "LUNA_GENERATION_FAILED",
        "LUNA_TAGS_INVALID",
        "LUNA_CALL_LOG_FAILED",
        "LUNA_CALL_LOG_INVALID",
        "LUNA_FAILURE_RECORD_FAILED",
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


# CLI 錯誤行只對照成固定類別；原文可能含教材，不保存也不輸出。
# 不重試類別優先，避免同一診斷的 HTTP 狀態碼遮蔽 timeout／context 原因。
LUNA_FAILURE_CAUSES = (
    ("usage_limit", ("usage limit", "quota", "billing")),
    ("auth", ("401", "403", "unauthorized", "forbidden", "authentication", "unauthenticated",
              "not logged in", "login", "log in", "sign in", "refresh token", "token refresh", "invalid api key")),
    ("context_window", ("context window", "context_length", "maximum context")),
    ("timeout", ("504", "timed out", "timeout", "deadline exceeded")),
    ("rate_limit", ("429", "rate limit", "too many requests")),
    ("server_error", ("500", "502", "503", "internal server error", "overloaded",
                      "bad gateway", "service unavailable")),
    ("connection", ("stream disconnected", "connection", "error sending request",
                    "broken pipe", "network")),
)
# 不自動重試的原因；timeout 與外層程序逾時遵守相同邊界。
LUNA_PERMANENT_CAUSES = {"usage_limit", "auth", "context_window", "timeout"}


class LunaCliFailure(RuntimeError):
    def __init__(self, cause):
        super().__init__("LUNA_GENERATION_FAILED")
        self.cause = cause


def luna_failure_cause(returncode, stderr):
    """只看 CLI 以 error 開頭的行，避免回顯的教材內容影響分類。"""
    lines = [
        line.strip().lower()
        for line in (stderr or "").splitlines()
        if line.strip().lower().startswith("error")
    ]
    matched = next(
        (name for name, needles in LUNA_FAILURE_CAUSES
         if any(needle in line for line in lines for needle in needles)),
        None,
    )
    if matched in LUNA_PERMANENT_CAUSES:
        cause = matched
    elif returncode == 0:
        cause = "output_missing"
    elif returncode < 0:
        cause = "signal"
    elif not lines:
        cause = "no_error_message"
    else:
        cause = matched or "unclassified"
    return {"cause": cause, "exit_code": returncode}


def luna(prompt, schema, *, timeout=None, cancelled=None):
    try:
        return _luna_once(prompt, schema, timeout=timeout, cancelled=cancelled)
    except LunaCliFailure as error:
        if error.cause["cause"] in LUNA_PERMANENT_CAUSES:
            raise
    podcast_process.check(cancelled)
    return _luna_once(prompt, schema, timeout=timeout, cancelled=cancelled)


def _luna_once(prompt, schema, *, timeout, cancelled):
    with tempfile.TemporaryDirectory(prefix="studydy-podcast-") as temporary:
        directory = Path(temporary)
        schema_path, output = directory / "schema.json", directory / "output.json"
        schema_path.write_text(json.dumps(schema), encoding="utf-8")
        args = [
            os.environ.get("STUDYDY_CODEX_BIN", "codex"),
            "exec",
            "--ignore-user-config",
            "-m",
            MODEL,
            "-s",
            "read-only",
            "--ephemeral",
            "--skip-git-repo-check",
            "-C",
            temporary,
            "--output-schema",
            str(schema_path),
            "-o",
            str(output),
            "-c",
            'web_search="disabled"',
            "-c",
            "project_doc_max_bytes=0",
            "-c",
            "tools.view_image=false",
            "-c",
            'history.persistence="none"',
        ]
        for feature in (
            "shell_tool",
            "multi_agent",
            "apps",
            "plugins",
            "code_mode",
            "code_mode_host",
            "view_image",
            "image_generation",
        ):
            args.extend(["--disable", feature])
        # stdin 避免教材出現在程序參數；stderr 只留記憶體做固定分類。
        ticket = luna_call_log.begin_call()
        code = failure = None
        try:
            try:
                result = podcast_process.run(
                    args + ["-"],
                    input=prompt,
                    timeout=timeout,
                    cancelled=cancelled,
                    started=ticket.started if ticket else None,
                    capture_stderr=True,
                    env={
                        k: v
                        for k, v in os.environ.items()
                        if k != "STUDYDY_PODCAST_PROVIDER_TOKEN"
                    },
                )
            except subprocess.TimeoutExpired:
                raise RuntimeError("LUNA_GENERATION_TIMEOUT") from None
            if result.returncode != 0 or not output.is_file():
                raise LunaCliFailure(luna_failure_cause(result.returncode, result.stderr))
            value = json.loads(output.read_text(encoding="utf-8"))
            luna_call_log.capture_candidate(value)
            return value
        except Exception as error:
            code = failure_code(error)
            failure = getattr(error, "cause", None)
            if output.is_file():
                luna_call_log.capture_candidate(output.read_text(encoding="utf-8", errors="replace"), failed=True)
            raise
        finally:
            if ticket is not None:
                ticket.finish(code, failure)


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

    def calls(self):
        with CALL_LOG_LOCK:
            if not hasattr(self.server, "luna_calls"):
                self.server.luna_calls = luna_call_log.CallLog(
                    os.environ.get("STUDYDY_LUNA_CALL_LOG", str(Path(__file__).resolve().parents[2] / "data/podcast/luna-calls.jsonl"))
                )
            return self.server.luna_calls

    def do_POST(self):
        expected = "Bearer " + os.environ["STUDYDY_PODCAST_PROVIDER_TOKEN"]
        if not compare_digest(self.headers.get("Authorization", ""), expected):
            self.send_error(401)
            return
        if self.path not in {
            "/audio",
            "/transcribe",
            "/media-transcribe",
            "/semantics",
            "/luna-health",
            "/align",
            "/video",
            "/jobs/cancel",
            "/jobs/progress",
            "/jobs/luna-result",
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
            tags = luna_call_log.normalize_tags(body.pop("tags", None))
            if self.path == "/jobs/luna-result":
                result = self.calls().finalize(tags, body.get("outcome"), body.get("reason"))
                self.respond(json.dumps(result).encode(), "application/json")
                return
            if self.path == "/jobs/progress":
                self.respond(json.dumps(podcast_process.progress(body)).encode(), "application/json")
                return
            if self.path == "/jobs/cancel":
                self.respond(
                    json.dumps(podcast_process.cancel(body)).encode(),
                    "application/json",
                )
                return
            if self.path == "/luna-health":
                self.generate(body, None)
                return
            active_lock = (
                TEST_BACKGROUND_SLOTS
                if TEST_BACKGROUND_SLOTS is not None
                and self.path == "/semantics"
                and tags is not None
                and tags.get("feature") in {"material_analysis", "material_review", "material_consolidation"}
                else MATERIAL_SLOTS
                if self.path == "/semantics"
                and body.get("task") == "material_semantics"
                else VIDEO_LOCK
                if self.path == "/video"
                else LOCK
                # 教材辨識用顯示卡，與 CosyVoice 共用同一把 GPU 鎖。
                if self.path in {"/audio", "/media-transcribe"}
                else ASR_LOCK
                if self.path in {"/transcribe", "/align"}
                else TEXT_LOCK
            )
            with self.calls().request(tags, self.path), podcast_process.job(body, required=self.path == "/video") as cancelled:
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
        elif self.path == "/luna-health":
            data, media = json.dumps({"model": MODEL}).encode(), "application/json"
        elif self.path == "/semantics":
            result = luna(
                body["prompt"]
                + "\nINPUT:\n"
                + json.dumps(body["request"], ensure_ascii=False),
                body["schema"],
                timeout=None,
                cancelled=cancelled,
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
    server.luna_calls = luna_call_log.CallLog(
        os.environ.get("STUDYDY_LUNA_CALL_LOG", str(Path(__file__).resolve().parents[2] / "data/podcast/luna-calls.jsonl"))
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
