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
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from .storage.database import DatabaseConfigurationError
from .storage.tables import Learner, LearnerSession
from .storage.database import database_session

IDLE_LIFETIME = timedelta(days=7)
ABSOLUTE_LIFETIME = timedelta(days=30)
# 每次請求解析 session 時，距上次記錄超過這段時間才更新最後使用時間，避免每個請求都寫入。
LAST_USED_INTERVAL = timedelta(minutes=5)
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


@dataclass(frozen=True)
class ActiveSession:
    """本人一個仍有效的登入；只含可公開的 session_id，不含 token 雜湊。"""

    session_id: UUID
    device_label: str | None
    created_at: datetime
    last_used_at: datetime
    current: bool


# 依出現順序比對：Edge／Opera／Samsung 的 User-Agent 也含 Chrome，iPhone 的含 Mac OS X，
# Android 的含 Linux，所以較特定的寫在前面。
_BROWSERS = (
    ("Edge", ("Edg/", "EdgA/", "EdgiOS/")),
    ("Opera", ("OPR/", "OPT/")),
    ("Samsung Internet", ("SamsungBrowser/",)),
    ("Firefox", ("Firefox/", "FxiOS/")),
    ("Chrome", ("CriOS/", "Chrome/")),
    ("Safari", ("Version/",)),
)
_SYSTEMS = (
    ("iPhone", ("iPhone",)),
    ("iPad", ("iPad",)),
    ("Android", ("Android",)),
    ("Windows", ("Windows",)),
    ("ChromeOS", ("CrOS",)),
    ("macOS", ("Macintosh", "Mac OS X")),
    ("Linux", ("Linux",)),
)


def device_label(user_agent: str | None) -> str | None:
    """把 User-Agent 簡化成「瀏覽器 · 系統」；兩者都認不出時回傳 None。

    只保存這個標籤，不保存原始 User-Agent。Safari 只認含 Version/ 的 Safari，
    App 內建瀏覽器等其他情況只顯示系統。
    """
    if not isinstance(user_agent, str):
        return None
    agent = user_agent[:512]

    def first(rules: tuple[tuple[str, tuple[str, ...]], ...]) -> str | None:
        return next((name for name, marks in rules if any(m in agent for m in marks)), None)

    browser = first(_BROWSERS)
    if browser == "Safari" and "Safari/" not in agent:
        browser = None
    parts = [part for part in (browser, first(_SYSTEMS)) if part]
    return " · ".join(parts) or None


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


def _add_session(
    session: Session, learner_id: UUID, label: str | None = None
) -> CreatedSession:
    """為指定使用者建立登入 session；label 為 device_label() 的結果。"""
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
            device_label=label,
            last_used_at=now,
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
    email: str,
    password: str,
    *,
    dsn: str | None = None,
    label: str | None = None,
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
            created = _add_session(session, learner_id, label)
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
    email: str,
    password: str,
    *,
    dsn: str | None = None,
    label: str | None = None,
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
            created = _add_session(session, learner.learner_id, label)
    except (DatabaseConfigurationError, SQLAlchemyError):
        raise SessionError("SESSION_STORAGE_FAILED") from None
    return created


def resolve_session(
    raw_token: str | None,
    *,
    dsn: str | None = None,
) -> TrustedLearner | None:
    """解析仍有效的 exact token；不延長 session，只記錄最後使用時間。

    最後使用時間距今超過 LAST_USED_INTERVAL 才寫入，大多數請求只有一次讀取。
    """

    token_digest = _token_digest(raw_token)
    if token_digest is None:
        return None
    now = _utc_now()
    stale = or_(
        LearnerSession.last_used_at.is_(None),
        LearnerSession.last_used_at < now - LAST_USED_INTERVAL,
    )
    try:
        with database_session(dsn) as session:
            found = session.execute(
                select(LearnerSession.learner_id, stale)
                .join(Learner, Learner.learner_id == LearnerSession.learner_id)
                .where(
                    LearnerSession.token_sha256 == token_digest,
                    LearnerSession.revoked_at.is_(None),
                    LearnerSession.idle_expires_at > now,
                    LearnerSession.absolute_expires_at > now,
                )
            ).first()
            if found is not None and found[1]:
                session.execute(
                    update(LearnerSession)
                    .where(LearnerSession.token_sha256 == token_digest, stale)
                    .values(last_used_at=now)
                )
    except (DatabaseConfigurationError, SQLAlchemyError):
        raise SessionError("SESSION_STORAGE_FAILED") from None
    if found is None:
        return None
    return TrustedLearner(learner_id=found[0])


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
            stored.last_used_at = now
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


def _active_sessions(
    session: Session, current: LearnerSession, now: datetime
) -> list[ActiveSession]:
    """本人仍有效的登入：目前這個排第一，其餘依最後使用時間由新到舊。

    0013 之前建立的 session 沒有最後使用時間，以 updated_at（建立或上次 refresh）代替。
    """
    last_used = func.coalesce(LearnerSession.last_used_at, LearnerSession.updated_at)
    rows = session.execute(
        select(
            LearnerSession.session_id,
            LearnerSession.device_label,
            LearnerSession.created_at,
            last_used,
        )
        .where(
            LearnerSession.learner_id == current.learner_id,
            LearnerSession.revoked_at.is_(None),
            LearnerSession.idle_expires_at > now,
            LearnerSession.absolute_expires_at > now,
        )
        .order_by(
            (LearnerSession.session_id == current.session_id).desc(),
            last_used.desc(),
            LearnerSession.created_at.desc(),
        )
    ).all()
    return [
        ActiveSession(
            session_id=row[0],
            device_label=row[1],
            created_at=row[2],
            last_used_at=row[3],
            current=row[0] == current.session_id,
        )
        for row in rows
    ]


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


def list_active_sessions(
    raw_token: str | None, *, dsn: str | None = None
) -> list[ActiveSession]:
    """列出目前 token 所屬帳號仍有效（未撤銷、未逾期）的登入，含目前這個。"""
    token_digest = _token_digest(raw_token)
    if token_digest is None:
        raise SessionError("SESSION_REQUIRED")
    try:
        with database_session(dsn) as session:
            current = session.scalar(
                select(LearnerSession).where(LearnerSession.token_sha256 == token_digest)
            )
            now = _utc_now()
            if not _active(current, now):
                raise SessionError("SESSION_REQUIRED")
            return _active_sessions(session, current, now)
    except (DatabaseConfigurationError, SQLAlchemyError):
        raise SessionError("SESSION_STORAGE_FAILED") from None


def revoke_other_sessions(
    raw_token: str | None, *, dsn: str | None = None
) -> list[ActiveSession]:
    """撤銷本人其他所有 session，保留目前這個；回傳之後的有效登入。"""
    token_digest = _token_digest(raw_token)
    if token_digest is None:
        raise SessionError("SESSION_REQUIRED")
    try:
        with database_session(dsn) as session:
            _, current = _lock_current(session, token_digest)
            now = _utc_now()
            _revoke_others(session, current, now)
            return _active_sessions(session, current, now)
    except (DatabaseConfigurationError, SQLAlchemyError):
        raise SessionError("SESSION_STORAGE_FAILED") from None


def revoke_own_session(
    raw_token: str | None, session_id: UUID, *, dsn: str | None = None
) -> list[ActiveSession]:
    """撤銷本人另一個登入；回傳之後的有效登入。

    目前這個登入不能用這裡撤銷（改用登出）；不屬於本人或不存在的 session_id 一律
    RESOURCE_NOT_FOUND，不透露其他帳號是否有這個登入。本人已失效的登入視為已完成。
    """
    token_digest = _token_digest(raw_token)
    if token_digest is None:
        raise SessionError("SESSION_REQUIRED")
    try:
        with database_session(dsn) as session:
            _, current = _lock_current(session, token_digest)
            if session_id == current.session_id:
                raise SessionError("REQUEST_INVALID")
            target = session.scalar(
                select(LearnerSession)
                .where(
                    LearnerSession.session_id == session_id,
                    LearnerSession.learner_id == current.learner_id,
                )
                .with_for_update()
            )
            if target is None:
                raise SessionError("RESOURCE_NOT_FOUND")
            now = _utc_now()
            if target.revoked_at is None:
                target.revoked_at = now
                target.updated_at = now
            return _active_sessions(session, current, now)
    except (DatabaseConfigurationError, SQLAlchemyError):
        raise SessionError("SESSION_STORAGE_FAILED") from None


def change_password(
    raw_token: str | None,
    current_password: str,
    new_password: str,
    *,
    dsn: str | None = None,
) -> list[ActiveSession]:
    """核對目前密碼後更新雜湊，並撤銷其他所有 session。

    目前 session 保留原 token：cookie 為 HttpOnly、無法由頁面讀取，重新簽發會讓
    同一瀏覽器其他分頁正在送出的請求收到 401 而被登出。目前密碼錯誤時整筆交易
    不寫入任何資料。回傳之後的有效登入。
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
            return _active_sessions(session, current, now)
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
