"""在受限子程序中將來源轉為 PDF，並核對輸出與來源對照。"""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
import signal
import shutil
import subprocess
import tempfile
import sys
import sysconfig


MIME = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".ppt": "application/vnd.ms-powerpoint",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".txt": "text/plain",
    ".md": "text/markdown",
}
MAX_FILE_BYTES = 100 * 1024 * 1024
# 圖片組在瀏覽器依順序打包成 0001.png… 的 ZIP，原檔即此壓縮檔。
IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
IMAGE_SET_MIME = "application/zip"
MAX_IMAGES = 50
AUDIO_MIME = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".wav": "audio/wav"}
VIDEO_MIME = {".mp4": "video/mp4"}
# 影片只保存抽出的音軌，原影片不進 artifact store。
VIDEO_AUDIO_MIME = "audio/mp4"
YOUTUBE_MIME = "application/vnd.studydy.youtube+json"
MAX_AUDIO_BYTES = 300 * 1024 * 1024
MAX_VIDEO_BYTES = 2 * 1024 * 1024 * 1024
MAX_MEDIA_SECONDS = 2 * 60 * 60
SEGMENT_SECONDS = 120
MEDIA_POLICY = {
    "schema": "normalization-policy/v1",
    "kind": "media",
    "version": 1,
    "av": "19.0.1",
    "pymupdf": "1.28.0",
    "max_images": MAX_IMAGES,
    "image_page_points": 842,
    "max_media_seconds": MAX_MEDIA_SECONDS,
    "segment_seconds": SEGMENT_SECONDS,
    "stt": "faster-whisper:large-v3-turbo;media-transcript/v1",
    "youtube_subtitles": "uploader-only;zh-Hant>zh-Hans>original",
}
POLICY = {
    "schema": "normalization-policy/v1",
    "version": 4,
    "olefile": "0.47",
    "max_file_bytes": MAX_FILE_BYTES,
    "libreoffice": "26.2.5.2",
    "pymupdf": "1.28.0",
    "markdown_it": "3.0.0",
    "visible_slides_only": True,
    "notes": False,
    "remote_resources": "public-images-best-effort",
    "text_columns": 72,
}
_SAFE_ERRORS = {
    "UNSUPPORTED_MEDIA_TYPE",
    "MATERIAL_TOO_LARGE",
    "PDF_UNUSABLE",
    "PDF_DAMAGED",
    "ZIP_PATH",
    "OFFICE_ACTIVE_OR_ENCRYPTED",
    "OFFICE_TYPE_MISMATCH",
    "OFFICE_INVALID_PACKAGE",
    "UTF8_REQUIRED",
    "SLIDE_PAGE_COUNT_MISMATCH",
    "UNSAFE_RENDER_HTML",
    "NORMALIZER_VERSION_MISMATCH",
}


class NormalizationError(RuntimeError):
    pass


def normalizer_available():
    """核對轉檔工具與字型版本，建立來源 mapping 使用的政策身分。"""
    return (
        all(shutil.which(tool) for tool in ("prlimit", "bwrap", "fc-list"))
        and Path("/usr/lib/libreoffice/program/soffice").is_file()
    )


def media_available():
    """媒體與圖片只需沙箱與 PyAV，不需要 LibreOffice。"""
    try:
        import av  # noqa: F401
    except ImportError:
        return False
    return all(shutil.which(tool) for tool in ("prlimit", "bwrap"))


def is_media(media_type):
    return media_type in (
        IMAGE_SET_MIME,
        YOUTUBE_MIME,
        *AUDIO_MIME.values(),
        *VIDEO_MIME.values(),
    )


def media_policy():
    """圖片組、音訊、影片與 YouTube 的轉換政策；文件仍用 conversion_policy。"""
    if not media_available():
        raise NormalizationError("NORMALIZER_UNAVAILABLE")
    import yt_dlp

    return {**MEDIA_POLICY, "yt_dlp": yt_dlp.version.__version__}


def conversion_policy():
    if not normalizer_available():
        raise NormalizationError("NORMALIZER_UNAVAILABLE")
    fonts = subprocess.check_output(
        ["fc-list", ":lang=zh", "file", "family"], text=True
    )
    font_lines = "\n".join(sorted(fonts.splitlines()))
    return {**POLICY, "fonts_sha256": hashlib.sha256(font_lines.encode()).hexdigest()}


def convert(
    data: bytes,
    extension: str,
    media_type: str,
    policy: dict,
    *,
    cancellation_check=None,
) -> tuple[bytes, dict]:
    """只接受當前政策；子程序產物須對回原檔與輸出 PDF 的雜湊。"""
    if policy != conversion_policy():
        raise NormalizationError("NORMALIZER_VERSION_MISMATCH")
    if extension not in MIME or MIME[extension] != media_type:
        raise NormalizationError("UNSUPPORTED_MEDIA_TYPE")

    if cancellation_check:
        cancellation_check()

    with tempfile.TemporaryDirectory(prefix="studydy-normalize-") as temporary:
        root = Path(temporary)
        source = root / "input"
        output = root / "output"
        source.mkdir()
        output.mkdir()
        (source / ("source" + extension)).write_bytes(data)
        command = _sandbox_command(
            [("--ro-bind", str(source), "/input")], output, MAX_FILE_BYTES
        )
        renderer = [
            "/runtime/bin/python3.12",
            "/renderer.py",
            "/input/source" + extension,
            "/output",
            media_type,
        ]
        # 只讓受控圖片下載階段連網；實際 Office 及 PDF 解析始終在斷網沙箱。
        for prepare in (True, False) if extension in (".docx", ".pptx") else (False,):
            network = (
                [
                    "--share-net",
                    "--ro-bind", "/etc/resolv.conf", "/etc/resolv.conf",
                    "--ro-bind", "/etc/ssl", "/etc/ssl",
                ]
                if prepare else []
            )
            _run_sandbox(
                command + network + renderer + (["--prepare-office"] if prepare else []),
                cancellation_check,
                _SAFE_ERRORS,
                "NORMALIZATION_FAILED",
            )

        try:
            pdf = (output / "normalized.pdf").read_bytes()
            mapping = json.loads((output / "mapping.json").read_text())
            if (
                not pdf
                or len(pdf) > MAX_FILE_BYTES
                or mapping["original_sha256"] != hashlib.sha256(data).hexdigest()
                or mapping["normalized_sha256"] != hashlib.sha256(pdf).hexdigest()
            ):
                raise ValueError
        except (OSError, KeyError, ValueError):
            raise NormalizationError("NORMALIZATION_FAILED") from None
        return pdf, mapping


def _sandbox_command(binds, output, fsize, script="renderer.py"):
    """子程序只讀所需 runtime／系統資源，僅 output 可寫；不繼承外部環境。"""
    # venv 的 Python 為 symlink，須掛載基底直譯器及套件目錄。
    base = Path(sys.base_prefix)
    site = Path(sysconfig.get_path("purelib"))
    if not site.is_dir():
        raise NormalizationError("NORMALIZER_UNAVAILABLE")
    return [
        # 由已 exec 的工具設限，避免在背景 Thread 的 fork 子程序執行 Python。
        "prlimit",
        f"--as={2 * 1024**3}",
        f"--fsize={fsize}",
        "--nofile=128",
        "--nproc=1024",
        "--",
        "bwrap",
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--cap-drop",
        "ALL",
        "--ro-bind",
        "/usr",
        "/usr",
        "--symlink",
        "usr/bin",
        "/bin",
        "--symlink",
        "usr/lib",
        "/lib",
        "--symlink",
        "usr/lib64",
        "/lib64",
        "--ro-bind",
        "/etc/fonts",
        "/etc/fonts",
        "--ro-bind-try",
        "/etc/libreoffice",
        "/etc/libreoffice",
        "--ro-bind",
        "/etc/ld.so.cache",
        "/etc/ld.so.cache",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--ro-bind",
        str(base),
        "/runtime",
        "--ro-bind",
        str(site),
        "/runtime/lib/python3.12/site-packages",
        "--ro-bind",
        str(Path(__file__).with_name(script)),
        "/" + script,
        *[item for bind in binds for item in bind],
        "--bind",
        str(output),
        "/output",
        "--clearenv",
        "--setenv",
        "HOME",
        "/tmp",
        "--setenv",
        "PATH",
        "/usr/bin",
        "--setenv",
        "LANG",
        "C.UTF-8",
        "--setenv",
        "SAL_USE_VCLPLUGIN",
        "svp",
        # 避免 LibreOffice 依主機 CPU 數開大量工作執行緒，耗盡 2 GiB 位址空間。
        "--setenv",
        "MAX_CONCURRENCY",
        "2",
        "--setenv",
        "XDG_CACHE_HOME",
        "/tmp/cache",
    ]


def _run_sandbox(command, cancellation_check, safe_errors, fallback):
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        while True:
            if cancellation_check:
                cancellation_check()
            try:
                stdout, _ = process.communicate(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                continue
    except BaseException:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        raise
    if process.returncode:
        reason = stdout.decode(errors="replace").strip()
        raise NormalizationError(reason if reason in safe_errors else fallback)


_MEDIA_ERRORS = {
    "MEDIA_UNREADABLE",
    "MEDIA_NO_AUDIO",
    "MEDIA_TOO_LONG",
    "MATERIAL_TOO_LARGE",
    "IMAGE_SET_INVALID",
    "IMAGE_UNREADABLE",
    "IMAGE_TOO_LARGE",
    "TRANSCRIPT_EMPTY",
    "TRANSCRIPT_INVALID",
    "NORMALIZER_VERSION_MISMATCH",
}


@contextmanager
def media_step(command, source_path, *, work_dir=None, cancellation_check=None):
    """在斷網沙箱執行 media.py；回傳只含輸出檔的暫存目錄，離開時刪除。

    影片可達 2 GiB，因此輸入以唯讀 bind 掛入，不複製；大型暫存放在呼叫端指定的磁碟。
    """
    if not media_available():
        raise NormalizationError("NORMALIZER_UNAVAILABLE")
    source_path = Path(source_path)
    with tempfile.TemporaryDirectory(
        prefix="studydy-media-", dir=work_dir
    ) as temporary:
        output = Path(temporary) / "output"
        output.mkdir()
        target = "/input/source" + source_path.suffix.lower()
        command_line = _sandbox_command(
            [("--ro-bind", str(source_path), target)],
            output,
            MAX_AUDIO_BYTES,
            "media.py",
        ) + ["/runtime/bin/python3.12", "/media.py", command, target, "/output"]
        _run_sandbox(
            command_line, cancellation_check, _MEDIA_ERRORS, "MEDIA_PROCESSING_FAILED"
        )
        yield output


def _normalized_output(output, original_sha256):
    try:
        pdf = (output / "normalized.pdf").read_bytes()
        mapping = json.loads((output / "mapping.json").read_text())
        if (
            not pdf
            or len(pdf) > MAX_FILE_BYTES
            or mapping["original_sha256"] != original_sha256
            or mapping["normalized_sha256"] != hashlib.sha256(pdf).hexdigest()
            or mapping["page_count"] != len(mapping["records"])
        ):
            raise ValueError
    except (OSError, KeyError, ValueError, TypeError):
        raise NormalizationError("NORMALIZATION_FAILED") from None
    return pdf, mapping


def _without_ytdlp(policy):
    return {key: value for key, value in policy.items() if key != "yt_dlp"}


def convert_images(data, policy, *, cancellation_check=None):
    """圖片組轉成一張一頁的 PDF；文字由既有 OCR 流程辨識。"""
    # yt-dlp 版本只記錄 YouTube 讀取來源，不影響圖片轉換；升級後排隊中的圖片組仍可轉換。
    if _without_ytdlp(policy) != MEDIA_POLICY:
        raise NormalizationError("NORMALIZER_VERSION_MISMATCH")
    with tempfile.TemporaryDirectory(prefix="studydy-images-") as temporary:
        source = Path(temporary) / "images.zip"
        source.write_bytes(data)
        with media_step(
            "images", source, cancellation_check=cancellation_check
        ) as output:
            return _normalized_output(output, hashlib.sha256(data).hexdigest())


def render_transcript(transcript, policy, *, work_dir=None, cancellation_check=None):
    """逐字稿約每 2 分鐘排成一頁；mapping 記錄每頁的時間範圍。"""
    if _without_ytdlp(policy) != MEDIA_POLICY:
        raise NormalizationError("NORMALIZER_VERSION_MISMATCH")
    with tempfile.TemporaryDirectory(
        prefix="studydy-transcript-", dir=work_dir
    ) as temporary:
        source = Path(temporary) / "transcript.json"
        source.write_text(json.dumps(transcript, ensure_ascii=False))
        with media_step(
            "transcript", source, work_dir=work_dir, cancellation_check=cancellation_check
        ) as output:
            return _normalized_output(output, transcript["original_sha256"])
