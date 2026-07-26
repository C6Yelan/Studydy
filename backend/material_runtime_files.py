from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def resolve_runtime_path(repo_root: str | Path, relative_path: str | Path) -> Path:
    """組合 repository root 與既定的 runtime artifact path。"""
    return Path(repo_root).resolve() / Path(relative_path)


def publish_runtime_json(
    value: Any,
    *,
    repo_root: str | Path,
    stable_path: str | Path,
) -> None:
    """在 stable 同目錄完成暫存寫入後原子替換；失敗時保留既有 stable。"""
    payload = canonical_json_bytes(value)
    stable = resolve_runtime_path(repo_root, stable_path)
    stable.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{stable.name}.",
            suffix=".tmp",
            dir=stable.parent,
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as temporary_file:
            temporary_file.write(payload)
        os.replace(temporary_path, stable)
        temporary_path = None
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
