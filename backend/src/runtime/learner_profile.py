"""個人顯示名稱：只存學生自己設定的名稱，未設定時維持 NULL。"""

from __future__ import annotations

import unicodedata
from uuid import UUID

from .learner_session import SessionError
from .storage.database import database_session
from .storage.tables import Learner

DISPLAY_NAME_MAX = 20


def normalize_display_name(value: str | None) -> str | None:
    """NFC 正規化並去除前後空白；空字串視為清除名稱。"""
    if value is None:
        return None
    name = unicodedata.normalize("NFC", value).strip()
    if not name:
        return None
    if len(name) > DISPLAY_NAME_MAX or any(
        unicodedata.category(char) in {"Cc", "Zl", "Zp"} for char in name
    ):
        raise SessionError("REQUEST_INVALID")
    return name


def _view(learner: Learner) -> dict:
    return {"schema": "learner-profile/v1", "display_name": learner.display_name}


def read_profile(owner: UUID, *, dsn=None) -> dict:
    with database_session(dsn) as db:
        learner = db.get(Learner, owner)
        if learner is None:
            raise SessionError("SESSION_REQUIRED")
        return _view(learner)


def update_profile(owner: UUID, display_name: str | None, *, dsn=None) -> dict:
    name = normalize_display_name(display_name)
    with database_session(dsn) as db:
        learner = db.get(Learner, owner, with_for_update=True)
        if learner is None:
            raise SessionError("SESSION_REQUIRED")
        learner.display_name = name
        db.flush()
        return _view(learner)
