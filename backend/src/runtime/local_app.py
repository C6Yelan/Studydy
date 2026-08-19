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


_ENVIRONMENT_KEYS = {
    "profile": "STUDYDY_PROFILE",
    "public_origin": "STUDYDY_PUBLIC_ORIGIN",
    "secure_cookie": "STUDYDY_SECURE_COOKIE",
    "private_runtime_root": "STUDYDY_PRIVATE_RUNTIME_ROOT",
    "python_executable": "STUDYDY_LOCAL_AI_PYTHON",
    "site_packages": "STUDYDY_LOCAL_AI_SITE_PACKAGES",
    "concept_site_packages": "STUDYDY_CONCEPT_SITE_PACKAGES",
    "ocr_model_root": "STUDYDY_OCR_MODEL_ROOT",
    "concept_api_base_url": "STUDYDY_CONCEPT_API_BASE_URL",
    "concept_model": "STUDYDY_CONCEPT_MODEL",
    "concept_server_executable": "STUDYDY_CONCEPT_SERVER_EXECUTABLE",
    "concept_model_root": "STUDYDY_CONCEPT_MODEL_ROOT",
}
_OPTIONAL_INTEGER_SETTINGS = {
    "concept_kv_cache_bytes": ("STUDYDY_CONCEPT_KV_CACHE_BYTES", 2_147_483_648),
    "concept_max_concurrency": ("STUDYDY_CONCEPT_MAX_CONCURRENCY", 2),
    "concept_max_model_len": ("STUDYDY_CONCEPT_MAX_MODEL_LEN", 5_632),
}


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
        for name, environment_name in _ENVIRONMENT_KEYS.items()
    }
    for name, (environment_name, default) in _OPTIONAL_INTEGER_SETTINGS.items():
        raw_value = environment.get(environment_name, str(default))
        if not isinstance(raw_value, str) or not raw_value.isdecimal():
            raise ValueError("LOCAL_APP_SETTINGS_INVALID")
        values[name] = int(raw_value)
    profile = values.pop("profile")
    if profile not in {"local", "test"}:
        raise ValueError("LOCAL_APP_SETTINGS_INVALID")
    secure_cookie_text = values.pop("secure_cookie")
    if secure_cookie_text not in {"true", "false"}:
        raise ValueError("LOCAL_APP_SETTINGS_INVALID")
    public_origin = values.pop("public_origin")
    values["runtime_lock"] = _runtime_lock()
    return {
        "profile": profile,
        "public_origin": public_origin,
        "secure_cookie": secure_cookie_text == "true",
        "local_config": values,
        "dsn": None,
    }


def create_local_app(
    *,
    profile: str,
    public_origin: str,
    secure_cookie: bool,
    local_config: dict[str, Any],
    dsn: str | None,
) -> FastAPI:
    """先完成 formal preflight，再建立唯一 material review API。"""

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
    port: int = 8000,
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
