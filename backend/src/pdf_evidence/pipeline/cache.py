from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any, Callable

from .transport import (
    MAX_RESPONSE_BYTES,
    _canonical_bytes,
    _canonical_sha256,
)


CACHE_SCHEMA = "structured-generation-cache/v1"
MAX_CACHE_BYTES = MAX_RESPONSE_BYTES + 1024 * 1024


def _cache_path(local_config: dict[str, Any], operation: str, cache_key: str) -> Path:
    """以 operation 隔離 success-only cache。"""
    root = Path(os.path.abspath(local_config["cache_dir"]))
    return root / operation / f"{cache_key}.json"


def _path_has_symlink(path: Path) -> bool:
    """檢查既有路徑元件，避免 cache 經 symlink 寫到指定 root 之外。"""
    try:
        absolute = Path(os.path.abspath(path))
        current = Path(absolute.anchor)
        for part in absolute.parts[1:]:
            current /= part
            if os.path.lexists(current) and current.is_symlink():
                return True
        return False
    except (OSError, ValueError):
        return True


def _read_cache(
    path: Path,
    *,
    cache_key: str,
    operation: str,
    input_binding: dict[str, Any],
    runtime_binding: dict[str, Any],
    consume: Callable[[Any], tuple[dict[str, Any] | None, str | None]],
) -> tuple[dict[str, Any] | None, str | None, bool]:
    """重驗 cache identity、內容 hash 與目前 consumer。"""
    if _path_has_symlink(path.parent) or (
        path.parent.exists() and not path.parent.is_dir()
    ):
        return None, "GENERATION_CACHE_INVALID", True
    if not os.path.lexists(path):
        return None, None, False
    if path.is_symlink() or not path.is_file():
        return None, "GENERATION_CACHE_INVALID", True
    try:
        with path.open("rb") as cache_file:
            encoded = cache_file.read(MAX_CACHE_BYTES + 1)
        if len(encoded) > MAX_CACHE_BYTES:
            return None, "GENERATION_CACHE_INVALID", True
        record = json.loads(encoded.decode("utf-8"))
    except (OSError, RecursionError, UnicodeDecodeError, ValueError):
        return None, "GENERATION_CACHE_INVALID", True
    fields = {
        "schema",
        "cache_key",
        "operation",
        "input_binding",
        "runtime_binding",
        "output_sha256",
        "output",
    }
    if (
        not isinstance(record, dict)
        or set(record) != fields
        or record["schema"] != CACHE_SCHEMA
        or record["cache_key"] != cache_key
        or record["operation"] != operation
        or record["input_binding"] != input_binding
        or record["runtime_binding"] != runtime_binding
        or record["output_sha256"] != _canonical_sha256(record["output"])
    ):
        return None, "GENERATION_CACHE_INVALID", True
    try:
        artifact, reason = consume(record["output"])
    except (
        AttributeError,
        IndexError,
        KeyError,
        OverflowError,
        RecursionError,
        TypeError,
        ValueError,
    ):
        return None, "GENERATION_CACHE_INVALID", True
    if artifact is None or reason is not None:
        return None, "GENERATION_CACHE_INVALID", True
    return artifact, None, True


def _write_cache(path: Path, record: dict[str, Any]) -> bool:
    """以同目錄暫存檔發布 bounded cache；既有內容必須完全相同。"""
    temporary_path: Path | None = None
    descriptor: int | None = None
    try:
        encoded = _canonical_bytes(record)
        if encoded is None or len(encoded) > MAX_CACHE_BYTES:
            return False
        if _path_has_symlink(path.parent) or (
            path.parent.exists() and not path.parent.is_dir()
        ):
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix="cache-", dir=path.parent)
        temporary_path = Path(name)
        with os.fdopen(descriptor, "wb") as cache_file:
            descriptor = None
            cache_file.write(encoded)
        if os.path.lexists(path):
            if path.is_symlink() or not path.is_file():
                return False
            with path.open("rb") as existing_file:
                existing = existing_file.read(len(encoded) + 1)
            return existing == encoded
        os.replace(temporary_path, path)
        temporary_path = None
        return True
    except OSError:
        return False
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
