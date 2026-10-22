from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
from typing import Any


_LOCAL_RUNTIME_ROOT_ENVIRONMENT_KEY = "STUDYDY_LOCAL_RUNTIME_ROOT"


def _runtime_lock() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[3] / "local_ai" / "runtime-lock.json"
    try:
        runtime_lock = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError("LOCAL_APP_SETTINGS_INVALID") from None
    if not isinstance(runtime_lock, dict):
        raise ValueError("LOCAL_APP_SETTINGS_INVALID")
    return runtime_lock


def read_local_ai_config_from_environment(
    environment: Mapping[str, str],
) -> dict[str, Any]:
    """從單一 root 組出 OCR sidecar；resident semantic service 只由 lock 定義。"""

    root_value = environment.get(_LOCAL_RUNTIME_ROOT_ENVIRONMENT_KEY)
    if root_value is None:
        root = Path.home() / ".local" / "share" / "studydy"
    elif (
        not isinstance(root_value, str)
        or not root_value
        or "\x00" in root_value
        or not Path(root_value).is_absolute()
    ):
        raise ValueError("LOCAL_APP_SETTINGS_INVALID")
    else:
        root = Path(root_value)
    runtime_lock = _runtime_lock()
    if environment.get("STUDYDY_TEXT_TEST_PROVIDER") == "codex-exec-luna":
        from .luna_test import test_lock

        runtime_lock = test_lock(runtime_lock)
    values: dict[str, Any] = {
        "private_runtime_root": str(root / "runtime"),
        "runtime_lock": runtime_lock,
        "python_executable": str(root / "ocr" / "runtime" / "bin" / "python3.12"),
        "site_packages": str(
            root / "ocr" / "runtime" / "lib" / "python3.12" / "site-packages"
        ),
        "ocr_model_root": str(root / "models" / "unlimited-ocr"),
    }
    return values


def configured_lock():
    return read_local_ai_config_from_environment(os.environ)["runtime_lock"]
