import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from sqlmodel import Session, delete, select

from app.core.config import VERIFICATION_CODE_EXPIRE_MINUTES, VERIFICATION_CODE_LENGTH
from app.core.security import hash_secret, verify_secret
from app.models import EmailVerificationCode


def generate_code() -> str:
    max_number = 10**VERIFICATION_CODE_LENGTH
    return f"{secrets.randbelow(max_number):0{VERIFICATION_CODE_LENGTH}d}"


def delete_codes_for_email(session: Session, email: str) -> None:
    session.exec(delete(EmailVerificationCode).where(EmailVerificationCode.email == email))


def create_code(session: Session, email: str) -> Tuple[EmailVerificationCode, str]:
    code = generate_code()
    now = datetime.now(timezone.utc)
    verification = EmailVerificationCode(
        email=email,
        code_hash=hash_secret(code),
        expires_at=now + timedelta(minutes=VERIFICATION_CODE_EXPIRE_MINUTES),
        created_at=now,
    )
    session.add(verification)
    session.commit()
    session.refresh(verification)
    return verification, code


def latest_unused_code(session: Session, email: str) -> Optional[EmailVerificationCode]:
    return (
        session.exec(
            select(EmailVerificationCode)
            .where(EmailVerificationCode.email == email, EmailVerificationCode.used.is_(False))
            .order_by(EmailVerificationCode.created_at.desc())
        ).first()
    )


def is_code_valid(stored: EmailVerificationCode, submitted: str, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    if stored.used:
        return False
    expires_at = stored.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < now:
        return False
    return verify_secret(submitted, stored.code_hash)


def mark_code_used(session: Session, code_record: EmailVerificationCode) -> None:
    code_record.used = True
    session.add(code_record)
