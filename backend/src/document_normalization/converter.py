"""在受限子程序中將來源轉為 PDF，並核對輸出與來源對照。"""

from __future__ import annotations

import hashlib
import json
import os
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
POLICY = {
    "schema": "normalization-policy/v1",
    "version": 3,
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

    # venv 的 Python 為 symlink，須掛載基底直譯器及套件目錄。
    base = Path(sys.base_prefix)
    site = Path(sysconfig.get_path("purelib"))
    if not site.is_dir():
        raise NormalizationError("NORMALIZER_UNAVAILABLE")

    with tempfile.TemporaryDirectory(prefix="studydy-normalize-") as temporary:
        root = Path(temporary)
        source = root / "input"
        output = root / "output"
        source.mkdir()
        output.mkdir()
        (source / ("source" + extension)).write_bytes(data)

        # 子程序只讀所需 runtime／系統資源，僅 output 可寫；不繼承外部環境。
        command = [
            # 由已 exec 的工具設限，避免在背景 Thread 的 fork 子程序執行 Python。
            "prlimit",
            f"--as={2 * 1024**3}",
            f"--fsize={MAX_FILE_BYTES}",
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
            "--ro-bind",
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
            str(Path(__file__).with_name("renderer.py")),
            "/renderer.py",
            "--ro-bind",
            str(source),
            "/input",
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
            process = subprocess.Popen(
                command + network + renderer + (["--prepare-office"] if prepare else []),
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
                raise NormalizationError(
                    reason if reason in _SAFE_ERRORS else "NORMALIZATION_FAILED"
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
