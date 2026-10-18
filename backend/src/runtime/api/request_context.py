"""各功能路由共用的請求、session 與冪等鍵邊界。"""

from __future__ import annotations

from typing import TYPE_CHECKING
from fastapi import Request
from ..learner_session import SessionError, TrustedLearner, resolve_session

if TYPE_CHECKING:
    from .app import ApiSettings

SESSION_COOKIE = "studydy_session"


class ApiFailure(Exception):
    def __init__(self, reason_code: str) -> None:
        super().__init__("API_REQUEST_FAILED")
        self.reason_code = reason_code


def require_query(request: Request, allowed: set[str]) -> None:
    names = [key for key, _ in request.query_params.multi_items()]
    if set(names) - allowed or len(names) != len(set(names)):
        raise ApiFailure("REQUEST_INVALID")


def idempotency_key(request: Request) -> str:
    values = request.headers.getlist("idempotency-key")
    if len(values) != 1:
        raise ApiFailure("REQUEST_INVALID")
    value = values[0]
    try:
        encoded = value.encode("utf-8")
    except UnicodeError:
        raise ApiFailure("REQUEST_INVALID") from None
    if not 1 <= len(encoded) <= 256 or any(
        ord(character) < 32 or ord(character) == 127 for character in value
    ):
        raise ApiFailure("REQUEST_INVALID")
    return value


def trusted_learner(request: Request, settings: ApiSettings) -> TrustedLearner:
    try:
        learner = resolve_session(request.cookies.get(SESSION_COOKIE), dsn=settings.dsn)
    except SessionError:
        raise ApiFailure("STORAGE_UNAVAILABLE") from None
    if learner is None:
        raise ApiFailure("SESSION_REQUIRED")
    return learner


async def require_empty_body(request: Request) -> None:
    if await request.body() != b"":
        raise ApiFailure("REQUEST_INVALID")
