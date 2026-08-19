"""建立不可變來源 PDF 快照，並確認正式流程需要處理的完整頁數。"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat
from typing import Any

import pymupdf


_SHA256 = re.compile(r"[0-9a-f]{64}")


def copy_source_snapshot(pdf_path: Any, snapshot_path: Path) -> str | None:
    """從同一個已開啟 FD 複製不可變的來源快照，並拒絕 symlink。"""

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
                while chunk := source.read(1024 * 1024):
                    snapshot.write(chunk)
        return None
    except (OSError, ValueError):
        return "MATERIAL_READ_FAILED"
    finally:
        if source_descriptor >= 0:
            os.close(source_descriptor)
        if snapshot_descriptor is not None:
            os.close(snapshot_descriptor)


def build_whole_document_request(request: Any) -> dict[str, Any]:
    """確認來源與頁數，並建立涵蓋完整 PDF 的正式處理要求。"""

    if not isinstance(request, dict) or set(request) != {
        "media_type",
        "source_path",
        "expected_source_sha256",
    }:
        raise ValueError("SOURCE_READ_FAILED")
    if request["media_type"] != "application/pdf":
        raise ValueError("MEDIA_TYPE_INVALID")
    source_path = Path(request["source_path"])
    if source_path.is_symlink():
        raise ValueError("SOURCE_READ_FAILED")
    source_sha256 = request["expected_source_sha256"]
    if not isinstance(source_sha256, str) or _SHA256.fullmatch(source_sha256) is None:
        raise ValueError("SOURCE_HASH_MISMATCH")
    try:
        with source_path.open("rb") as source:
            if source.read(5) != b"%PDF-":
                raise ValueError("PDF_INVALID")
            source.seek(0)
            actual_sha256 = hashlib.file_digest(source, "sha256").hexdigest()
        if actual_sha256 != source_sha256:
            raise ValueError("SOURCE_HASH_MISMATCH")
        document = pymupdf.open(source_path)
    except ValueError:
        raise
    except (OSError, TypeError) as error:
        raise ValueError("SOURCE_READ_FAILED") from error
    except Exception as error:
        raise ValueError("PDF_INVALID") from error
    try:
        if document.needs_pass:
            raise ValueError("PDF_ENCRYPTED")
        page_count = document.page_count
    finally:
        document.close()
    if page_count < 1:
        raise ValueError("PDF_INVALID")
    return {
        **request,
        "page_numbers": list(range(1, page_count + 1)),
    }
