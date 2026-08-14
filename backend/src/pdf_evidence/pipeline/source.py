"""複製並驗證單次 PDF 執行使用的來源快照。"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import tempfile
from typing import Any

import pymupdf


def _source_page_count(
    pdf_path: Path,
    expected_source_sha256: str,
    page_limit: int,
) -> tuple[Path | None, int | None, str | None]:
    """在任何 generation 前驗證 PDF、hash、加密與頁數上限。"""
    try:
        with pdf_path.open("rb") as source:
            if source.read(5) != b"%PDF-":
                return None, None, "MATERIAL_NOT_PDF"
            source.seek(0)
            source_sha256 = hashlib.file_digest(source, "sha256").hexdigest()
    except OSError:
        return None, None, "MATERIAL_READ_FAILED"
    if source_sha256 != expected_source_sha256:
        return None, None, "SOURCE_HASH_MISMATCH"
    try:
        document = pymupdf.open(pdf_path)
    except Exception:
        return None, None, "PDF_CORRUPT"
    try:
        if document.needs_pass:
            return None, None, "PDF_ENCRYPTED"
        page_count = document.page_count
        if page_count < 1:
            return None, None, "PDF_EMPTY"
        if page_count > page_limit:
            return None, page_count, "PAGE_LIMIT_EXCEEDED"
    finally:
        document.close()
    return pdf_path, page_count, None


def _copy_source_snapshot(
    pdf_path: Any,
    snapshot_path: Path,
) -> str | None:
    """從同一個已開啟 FD 複製 immutable source snapshot。"""
    try:
        source_path = Path(pdf_path)
    except TypeError:
        return "MATERIAL_INPUT_INVALID"
    try:
        if source_path.is_symlink() or not source_path.exists():
            return "MATERIAL_MISSING"
        source_descriptor = os.open(
            source_path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
    except FileNotFoundError:
        return "MATERIAL_MISSING"
    except (OSError, ValueError):
        return "MATERIAL_READ_FAILED"
    snapshot_descriptor: int | None = None
    try:
        source_status = os.fstat(source_descriptor)
        if not stat.S_ISREG(source_status.st_mode):
            return "MATERIAL_MISSING"
        snapshot_descriptor = os.open(
            snapshot_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(source_descriptor, "rb") as source:
            source_descriptor = -1
            with os.fdopen(snapshot_descriptor, "wb") as snapshot:
                snapshot_descriptor = None
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    snapshot.write(chunk)
        return None
    except (OSError, ValueError):
        return "MATERIAL_READ_FAILED"
    finally:
        if source_descriptor >= 0:
            os.close(source_descriptor)
        if snapshot_descriptor is not None:
            os.close(snapshot_descriptor)


def _cleanup_source_snapshot(
    snapshot_path: Path | None,
    temporary_directory: tempfile.TemporaryDirectory | None,
) -> bool:
    """盡力刪除私人 source snapshot，回報是否完整清理。"""
    cleanup_failed = False
    if snapshot_path is not None:
        try:
            snapshot_path.unlink(missing_ok=True)
        except OSError:
            cleanup_failed = True
    if temporary_directory is not None:
        try:
            temporary_directory.cleanup()
        except OSError:
            cleanup_failed = True
    return not cleanup_failed
