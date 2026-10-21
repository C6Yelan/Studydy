from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import scrypt, sha256
import re
import secrets
from threading import Lock
from uuid import UUID, uuid4

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from .storage.database import DatabaseConfigurationError
from .storage.tables import Learner, LearnerSession
from .storage.database import database_session

IDLE_LIFETIME = timedelta(days=7)
ABSOLUTE_LIFETIME = timedelta(days=30)
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
_EMAIL_ADDRESS = TypeAdapter(EmailStr)
_CURRENT_SCRYPT = (2**14, 8, 5)
_SCRYPT_MAXMEM = {
    _CURRENT_SCRYPT: 32 * 1024 * 1024,
    (2**17, 8, 1): 256 * 1024 * 1024,
}


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


def _add_session(session: Session, learner_id: UUID) -> CreatedSession:
    """為指定使用者建立登入 session。"""
    token_bytes = secrets.token_bytes(32)
    now = _utc_now()
    session.add(
        LearnerSession(
            session_id=uuid4(),
            learner_id=learner_id,
            token_sha256=sha256(token_bytes).digest(),
            created_at=now,
            idle_expires_at=now + IDLE_LIFETIME,
            absolute_expires_at=now + ABSOLUTE_LIFETIME,
            revoked_at=None,
            updated_at=now,
        )
    )
    return CreatedSession(learner_id=learner_id, raw_token=_encode_token(token_bytes))


def _credentials(email: str, password: str) -> str:
    # EmailStr 使用 email-validator 做格式與 Unicode/domain 正規化，不查 DNS。
    # 登入 identifier 整體不分大小寫；不做 mailbox verification。
    try:
        email = _EMAIL_ADDRESS.validate_python(email).lower()
    except ValidationError:
        raise SessionError("REQUEST_INVALID") from None
    if not isinstance(password, str) or not 15 <= len(password) <= 128:
        raise SessionError("REQUEST_INVALID")
    return email


def _password_digest(
    password: str, salt: bytes, profile: tuple[int, int, int] = _CURRENT_SCRYPT
) -> bytes:
    # 預設雜湊約使用 16 MiB；驗證依雜湊記錄的參數，密碼不截斷。
    n, r, p = profile
    return scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        maxmem=_SCRYPT_MAXMEM[profile],
        dklen=32,
    )


def _password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    n, r, p = _CURRENT_SCRYPT
    return f"scrypt${n}${r}${p}${salt.hex()}${_password_digest(password, salt).hex()}"


def register_account(
    email: str, password: str, *, dsn: str | None = None
) -> CreatedSession:
    """以同一交易建立帳號與登入 session。"""
    email = _credentials(email, password)
    password_hash = _password_hash(password)
    learner_id = uuid4()
    try:
        with database_session(dsn) as session:
            session.add(
                Learner(
                    learner_id=learner_id,
                    created_at=_utc_now(),
                    email=email,
                    password_hash=password_hash,
                )
            )
            session.flush()
            created = _add_session(session, learner_id)
    except IntegrityError as error:
        if getattr(error.orig, "sqlstate", None) == "23505":
            raise SessionError("ACCOUNT_UNAVAILABLE") from None
        raise SessionError("SESSION_CREATE_FAILED") from None
    except (DatabaseConfigurationError, SQLAlchemyError):
        raise SessionError("SESSION_CREATE_FAILED") from None
    return created


def _password_profile(stored: str | None, password: str) -> tuple[int, int, int] | None:
    """常數時間核對密碼，相符時回傳雜湊參數。

    沒有雜湊（如帳號不存在）時仍以預設參數計算一次，避免以回應時間洩露帳號狀態；
    已存雜湊格式不符直接視為不相符。
    """
    parts = stored.split("$") if stored else None
    try:
        profile = tuple(map(int, parts[1:4])) if parts else _CURRENT_SCRYPT
        if parts and (
            len(parts) != 6 or parts[0] != "scrypt" or profile not in _SCRYPT_MAXMEM
        ):
            raise ValueError
        salt = bytes.fromhex(parts[4]) if parts else bytes(16)
    except (TypeError, ValueError):
        return None
    digest = _password_digest(password, salt, profile)
    if stored is None or not secrets.compare_digest(digest.hex(), parts[5]):
        return None
    return profile


def login_account(
    email: str, password: str, *, dsn: str | None = None
) -> CreatedSession:
    """驗證密碼後為原 learner 建立 session；失效 token 不會被復活。"""
    email = _credentials(email, password)
    try:
        with database_session(dsn) as session:
            learner = session.scalar(select(Learner).where(Learner.email == email))
            # 不存在的帳號也執行雜湊，避免以回應時間洩露帳號是否存在。
            profile = _password_profile(
                learner.password_hash if learner is not None else None, password
            )
            if learner is None or profile is None:
                raise SessionError("INVALID_CREDENTIALS")
            if profile != _CURRENT_SCRYPT:
                learner.password_hash = _password_hash(password)
            created = _add_session(session, learner.learner_id)
    except (DatabaseConfigurationError, SQLAlchemyError):
        raise SessionError("SESSION_STORAGE_FAILED") from None
    return created


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


def _active(stored: LearnerSession | None, now: datetime) -> bool:
    return (
        stored is not None
        and stored.revoked_at is None
        and stored.idle_expires_at > now
        and stored.absolute_expires_at > now
    )


def _active_count(session: Session, learner_id: UUID, now: datetime) -> int:
    return session.scalar(
        select(func.count())
        .select_from(LearnerSession)
        .where(
            LearnerSession.learner_id == learner_id,
            LearnerSession.revoked_at.is_(None),
            LearnerSession.idle_expires_at > now,
            LearnerSession.absolute_expires_at > now,
        )
    )


def _lock_current(session: Session, token_digest: bytes) -> tuple[Learner, LearnerSession]:
    """先鎖帳號、再鎖目前 session，固定鎖順序以免兩個裝置同時操作時互鎖。"""
    learner_id = session.scalar(
        select(LearnerSession.learner_id).where(
            LearnerSession.token_sha256 == token_digest
        )
    )
    learner = (
        None
        if learner_id is None
        else session.get(Learner, learner_id, with_for_update=True)
    )
    current = session.scalar(
        select(LearnerSession)
        .where(LearnerSession.token_sha256 == token_digest)
        .with_for_update()
    )
    if learner is None or not _active(current, _utc_now()):
        raise SessionError("SESSION_REQUIRED")
    return learner, current


def _revoke_others(session: Session, current: LearnerSession, now: datetime) -> None:
    session.execute(
        update(LearnerSession)
        .where(
            LearnerSession.learner_id == current.learner_id,
            LearnerSession.session_id != current.session_id,
            LearnerSession.revoked_at.is_(None),
        )
        .values(revoked_at=now, updated_at=now)
    )


def count_active_sessions(learner_id: UUID, *, dsn: str | None = None) -> int:
    """本人目前仍有效（未撤銷、未逾期）的登入數，含目前這個。"""
    try:
        with database_session(dsn) as session:
            return _active_count(session, learner_id, _utc_now())
    except (DatabaseConfigurationError, SQLAlchemyError):
        raise SessionError("SESSION_STORAGE_FAILED") from None


def revoke_other_sessions(raw_token: str | None, *, dsn: str | None = None) -> int:
    """撤銷本人其他所有 session，保留目前這個；回傳之後的有效登入數。"""
    token_digest = _token_digest(raw_token)
    if token_digest is None:
        raise SessionError("SESSION_REQUIRED")
    try:
        with database_session(dsn) as session:
            _, current = _lock_current(session, token_digest)
            now = _utc_now()
            _revoke_others(session, current, now)
            return _active_count(session, current.learner_id, now)
    except (DatabaseConfigurationError, SQLAlchemyError):
        raise SessionError("SESSION_STORAGE_FAILED") from None


def change_password(
    raw_token: str | None,
    current_password: str,
    new_password: str,
    *,
    dsn: str | None = None,
) -> int:
    """核對目前密碼後更新雜湊，並撤銷其他所有 session。

    目前 session 保留原 token：cookie 為 HttpOnly、無法由頁面讀取，重新簽發會讓
    同一瀏覽器其他分頁正在送出的請求收到 401 而被登出。目前密碼錯誤時整筆交易
    不寫入任何資料。回傳之後的有效登入數。
    """
    token_digest = _token_digest(raw_token)
    if token_digest is None:
        raise SessionError("SESSION_REQUIRED")
    if not isinstance(current_password, str) or not isinstance(new_password, str):
        raise SessionError("REQUEST_INVALID")
    if not 15 <= len(new_password) <= 128 or len(current_password) > 128:
        raise SessionError("REQUEST_INVALID")
    try:
        with database_session(dsn) as session:
            learner, current = _lock_current(session, token_digest)
            if _password_profile(learner.password_hash, current_password) is None:
                raise SessionError("CURRENT_PASSWORD_INVALID")
            now = _utc_now()
            learner.password_hash = _password_hash(new_password)
            _revoke_others(session, current, now)
            return _active_count(session, current.learner_id, now)
    except (DatabaseConfigurationError, SQLAlchemyError):
        raise SessionError("SESSION_STORAGE_FAILED") from None


class PasswordAttemptLimiter:
    """同一帳號修改密碼時「目前密碼錯誤」的節流。

    視窗內累積達上限即暫停嘗試，直到最早一次失敗超過視窗；成功後歸零。紀錄只存在
    本 API 程序記憶體（正式環境為單一 Uvicorn 程序），重啟後歸零，不寫入 DB。
    """

    def __init__(
        self,
        limit: int = 5,
        window: timedelta = timedelta(minutes=15),
        clock=_utc_now,
    ) -> None:
        self._limit = limit
        self._window = window
        self._clock = clock
        self._failures: dict[UUID, list[datetime]] = {}
        self._lock = Lock()

    def _recent(self, learner_id: UUID) -> list[datetime]:
        cutoff = self._clock() - self._window
        recent = [at for at in self._failures.get(learner_id, []) if at > cutoff]
        if recent:
            self._failures[learner_id] = recent
        else:
            self._failures.pop(learner_id, None)
        return recent

    def blocked(self, learner_id: UUID) -> bool:
        with self._lock:
            return len(self._recent(learner_id)) >= self._limit

    def record_failure(self, learner_id: UUID) -> None:
        with self._lock:
            self._failures[learner_id] = [*self._recent(learner_id), self._clock()]

    def reset(self, learner_id: UUID) -> None:
        with self._lock:
            self._failures.pop(learner_id, None)
