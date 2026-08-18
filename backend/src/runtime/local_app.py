"""正式本機 runtime 的單一 composition root。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from fastapi import FastAPI

from .api.app import ApiSettings, create_app


def create_local_app(
    *,
    profile: str,
    public_origin: str,
    secure_cookie: bool,
    local_config: dict[str, Any],
    dsn: str,
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
