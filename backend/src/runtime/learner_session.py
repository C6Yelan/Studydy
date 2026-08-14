from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import re
import secrets
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from .storage.database import DatabaseConfigurationError
from .storage.tables import Learner, LearnerSession, database_session

IDLE_LIFETIME = timedelta(days=7)
ABSOLUTE_LIFETIME = timedelta(days=30)
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")


class SessionError(RuntimeError):
    """Session 儲存失敗；訊息不含 token、DSN 或 SQL。"""


@dataclass(frozen=True)
class TrustedLearner:
    learner_id: UUID


@dataclass(frozen=True)
class CreatedSession:
    learner_id: UUID
    raw_token: str = field(repr=False)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _encode_token(token_bytes: bytes) -> str:
    return base64.urlsafe_b64encode(token_bytes).rstrip(b"=").decode("ascii")


def _token_digest(raw_token: str | None) -> bytes | None:
    if not isinstance(raw_token, str) or _TOKEN_PATTERN.fullmatch(raw_token) is None:
        return None
    try:
        token_bytes = base64.urlsafe_b64decode(raw_token + "=")
    except (ValueError, UnicodeError):
        return None
    if len(token_bytes) != 32 or _encode_token(token_bytes) != raw_token:
        return None
    return sha256(token_bytes).digest()


def create_session(*, dsn: str | None = None) -> CreatedSession:
    """原子建立 learner/session，raw token 只回傳這一次。"""

    learner_id = uuid4()
    session_id = uuid4()
    token_bytes = secrets.token_bytes(32)
    raw_token = _encode_token(token_bytes)
    token_digest = sha256(token_bytes).digest()
    created_at = _utc_now()
    idle_expires_at = created_at + IDLE_LIFETIME
    absolute_expires_at = created_at + ABSOLUTE_LIFETIME

    try:
        with database_session(dsn) as session:
            session.add(Learner(learner_id=learner_id, created_at=created_at))
            session.add(
                LearnerSession(
                    session_id=session_id,
                    learner_id=learner_id,
                    token_sha256=token_digest,
                    created_at=created_at,
                    idle_expires_at=idle_expires_at,
                    absolute_expires_at=absolute_expires_at,
                    revoked_at=None,
                    updated_at=created_at,
                )
            )
    except (DatabaseConfigurationError, SQLAlchemyError):
        raise SessionError("SESSION_CREATE_FAILED") from None
    return CreatedSession(learner_id=learner_id, raw_token=raw_token)


def resolve_session(
    raw_token: str | None,
    *,
    dsn: str | None = None,
) -> TrustedLearner | None:
    """解析仍有效的 exact token；此讀取不延長 session。"""

    token_digest = _token_digest(raw_token)
    if token_digest is None:
        return None
    now = _utc_now()
    try:
        with database_session(dsn) as session:
            learner_id = session.scalar(
                select(LearnerSession.learner_id)
                .join(Learner, Learner.learner_id == LearnerSession.learner_id)
                .where(
                    LearnerSession.token_sha256 == token_digest,
                    LearnerSession.revoked_at.is_(None),
                    LearnerSession.idle_expires_at > now,
                    LearnerSession.absolute_expires_at > now,
                )
            )
    except (DatabaseConfigurationError, SQLAlchemyError):
        raise SessionError("SESSION_STORAGE_FAILED") from None
    if learner_id is None:
        return None
    return TrustedLearner(learner_id=learner_id)


def refresh_session(
    raw_token: str | None,
    *,
    dsn: str | None = None,
) -> TrustedLearner | None:
    """鎖定並重驗 session 後延長 idle deadline。"""

    token_digest = _token_digest(raw_token)
    if token_digest is None:
        return None
    try:
        with database_session(dsn) as session:
            stored = session.scalar(
                select(LearnerSession)
                .where(LearnerSession.token_sha256 == token_digest)
                .with_for_update()
            )
            if stored is None:
                return None
            now = _utc_now()
            if stored.revoked_at is not None:
                return None
            if stored.idle_expires_at <= now or stored.absolute_expires_at <= now:
                return None
            stored.idle_expires_at = min(
                max(stored.idle_expires_at, now + IDLE_LIFETIME),
                stored.absolute_expires_at,
            )
            stored.updated_at = now
            learner_id = stored.learner_id
    except (DatabaseConfigurationError, SQLAlchemyError):
        raise SessionError("SESSION_STORAGE_FAILED") from None
    return TrustedLearner(learner_id=learner_id)


def revoke_session(
    raw_token: str | None,
    *,
    dsn: str | None = None,
) -> bool:
    """冪等撤銷 exact token；撤銷後不會恢復有效。"""

    token_digest = _token_digest(raw_token)
    if token_digest is None:
        return False
    try:
        with database_session(dsn) as session:
            stored = session.scalar(
                select(LearnerSession)
                .where(LearnerSession.token_sha256 == token_digest)
                .with_for_update()
            )
            if stored is None:
                return False
            if stored.revoked_at is None:
                now = _utc_now()
                stored.revoked_at = now
                stored.updated_at = now
    except (DatabaseConfigurationError, SQLAlchemyError):
        raise SessionError("SESSION_STORAGE_FAILED") from None
    return True
