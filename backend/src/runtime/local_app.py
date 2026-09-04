from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI
import uvicorn

from .api.app import ApiSettings, create_app


_APP_ENVIRONMENT_KEYS = {
    "profile": "STUDYDY_PROFILE",
    "public_origin": "STUDYDY_PUBLIC_ORIGIN",
    "secure_cookie": "STUDYDY_SECURE_COOKIE",
}
_LOCAL_RUNTIME_ROOT_ENVIRONMENT_KEY = "STUDYDY_LOCAL_RUNTIME_ROOT"


def _required_environment_value(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name)
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("LOCAL_APP_SETTINGS_INVALID")
    return value


def _runtime_lock() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[3] / "local_ai" / "runtime-lock.json"
    try:
        runtime_lock = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError("LOCAL_APP_SETTINGS_INVALID") from None
    if not isinstance(runtime_lock, dict):
        raise ValueError("LOCAL_APP_SETTINGS_INVALID")
    return runtime_lock


def _app_arguments_from_environment(environment: Mapping[str, str]) -> dict[str, Any]:
    """只讀取明列的非 secret 本機設定；DB 仍由既有 storage 邊界解析。"""

    values = {
        name: _required_environment_value(environment, environment_name)
        for name, environment_name in _APP_ENVIRONMENT_KEYS.items()
    }
    local_config = read_local_ai_config_from_environment(environment)
    profile = values["profile"]
    if profile not in {"local", "test"}:
        raise ValueError("LOCAL_APP_SETTINGS_INVALID")
    if values["secure_cookie"] not in {"true", "false"}:
        raise ValueError("LOCAL_APP_SETTINGS_INVALID")
    return {
        "profile": profile,
        "public_origin": values["public_origin"],
        "secure_cookie": values["secure_cookie"] == "true",
        "local_config": local_config,
        "dsn": None,
    }


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


def create_local_app(
    *,
    profile: str,
    public_origin: str,
    secure_cookie: bool,
    local_config: dict[str, Any],
    dsn: str | None,
) -> FastAPI:
    """先完成唯一 runtime preflight，再建立產品 API。"""

    settings = ApiSettings(
        profile=profile,
        public_origin=public_origin,
        secure_cookie=secure_cookie,
        local_config=deepcopy(local_config),
        dsn=dsn,
    )
    return create_app(settings)


def run_local_app(
    *,
    environment: Mapping[str, str] | None = None,
    host: str = "127.0.0.1",
    port: int = 8001,
) -> None:
    """以同一組 API/worker 組裝結果啟動正式 Uvicorn server。"""

    app_arguments = _app_arguments_from_environment(
        os.environ if environment is None else environment
    )
    uvicorn.run(
        create_local_app(**app_arguments),
        host=host,
        port=port,
        log_config=None,
        access_log=False,
    )


def main() -> None:
    run_local_app()


if __name__ == "__main__":
    main()
