from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat
from typing import Any

import pymupdf


_SHA256 = re.compile(r"[0-9a-f]{64}")


def snapshot_whole_document_request(
    request: Any, snapshot_path: Path
) -> dict[str, Any]:
    """從單一來源 FD 驗證 hash 並複製快照，再確認完整 PDF 頁數。"""

    if not isinstance(request, dict) or set(request) != {
        "media_type", "source_path", "expected_source_sha256"
    }:
        raise ValueError("SOURCE_READ_FAILED")
    if request["media_type"] != "application/pdf":
        raise ValueError("MEDIA_TYPE_INVALID")
    source_sha256 = request["expected_source_sha256"]
    if not isinstance(source_sha256, str) or _SHA256.fullmatch(source_sha256) is None:
        raise ValueError("SOURCE_HASH_MISMATCH")
    try:
        source_path = Path(request["source_path"])
    except TypeError:
        raise ValueError("SOURCE_READ_FAILED") from None
    try:
        if source_path.is_symlink() or not source_path.exists():
            raise ValueError("SOURCE_READ_FAILED")
        source_descriptor = os.open(
            source_path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
    except FileNotFoundError:
        raise ValueError("SOURCE_READ_FAILED") from None
    except (OSError, ValueError):
        raise ValueError("SOURCE_READ_FAILED") from None
    snapshot_descriptor: int | None = None
    try:
        source_status = os.fstat(source_descriptor)
        if not stat.S_ISREG(source_status.st_mode):
            raise ValueError("SOURCE_READ_FAILED")
        snapshot_descriptor = os.open(
            snapshot_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        digest = hashlib.sha256()
        prefix = b""
        with os.fdopen(source_descriptor, "rb") as source:
            source_descriptor = -1
            with os.fdopen(snapshot_descriptor, "wb") as snapshot:
                snapshot_descriptor = None
                while chunk := source.read(1024 * 1024):
                    if len(prefix) < 5:
                        prefix += chunk[: 5 - len(prefix)]
                    digest.update(chunk)
                    snapshot.write(chunk)
        if prefix != b"%PDF-":
            raise ValueError("PDF_INVALID")
        if digest.hexdigest() != source_sha256:
            raise ValueError("SOURCE_HASH_MISMATCH")
    except ValueError:
        raise
    except OSError:
        raise ValueError("SOURCE_READ_FAILED") from None
    finally:
        if source_descriptor >= 0:
            os.close(source_descriptor)
        if snapshot_descriptor is not None:
            os.close(snapshot_descriptor)
    try:
        document = pymupdf.open(snapshot_path)
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
        "source_path": str(snapshot_path),
        "page_numbers": list(range(1, page_count + 1)),
    }
