import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlmodel import Session, select, delete

from app.db import get_session
from app.models import EmailVerificationCode, LearningPreference, User
from app.services import get_email_service
from app.services.email import EmailService

router = APIRouter(prefix="/auth", tags=["auth"])

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "dev-change-me")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
MIN_PASSWORD_LENGTH = 8
VERIFICATION_CODE_LENGTH = 6
VERIFICATION_CODE_EXPIRE_MINUTES = 10


class RegisterRequestCode(BaseModel):
    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH)


class RegisterConfirmRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH)
    code: str = Field(min_length=VERIFICATION_CODE_LENGTH, max_length=VERIFICATION_CODE_LENGTH)
    learning_preference: LearningPreference | None = None


class RegisterResponse(BaseModel):
    id: int
    email: EmailStr
    learning_preference: LearningPreference | None = None
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirmRequest(BaseModel):
    email: EmailStr
    code: str = Field(min_length=VERIFICATION_CODE_LENGTH, max_length=VERIFICATION_CODE_LENGTH)
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH)


def _hash_secret(secret: str) -> str:
    salt = os.urandom(16)
    hashed = hashlib.pbkdf2_hmac(
        "sha256",
        secret.encode("utf-8"),
        salt,
        100_000,
    )
    return f"{salt.hex()}:{hashed.hex()}"


def _verify_secret(secret: str, stored_hash: str) -> bool:
    try:
        salt_hex, hash_hex = stored_hash.split(":")
    except ValueError:
        return False
    salt = bytes.fromhex(salt_hex)
    expected_hash = bytes.fromhex(hash_hex)
    computed = hashlib.pbkdf2_hmac(
        "sha256",
        secret.encode("utf-8"),
        salt,
        100_000,
    )
    return hmac.compare_digest(computed, expected_hash)


def _create_access_token(data: Dict[str, Any], expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def _generate_verification_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _ensure_timezone(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _get_latest_code(session: Session, email: str) -> EmailVerificationCode | None:
    return (
        session.exec(
            select(EmailVerificationCode)
            .where(EmailVerificationCode.email == email, EmailVerificationCode.used.is_(False))
            .order_by(EmailVerificationCode.created_at.desc())
        ).first()
    )


@router.post(
    "/register/request-code",
    status_code=status.HTTP_200_OK,
)
async def request_registration_code(
    request: RegisterRequestCode,
    session: Session = Depends(get_session),
    email_service: EmailService = Depends(get_email_service),
) -> Dict[str, str]:
    existing = session.exec(select(User).where(User.email == request.email)).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    session.exec(delete(EmailVerificationCode).where(EmailVerificationCode.email == request.email))

    code = _generate_verification_code()
    now = datetime.now(timezone.utc)
    verification = EmailVerificationCode(
        email=request.email,
        code_hash=_hash_secret(code),
        expires_at=now + timedelta(minutes=VERIFICATION_CODE_EXPIRE_MINUTES),
        created_at=now,
    )
    session.add(verification)
    session.commit()

    email_service.send_verification_code(request.email, code)
    return {"detail": "Verification code sent"}


@router.post(
    "/register/confirm",
    status_code=status.HTTP_201_CREATED,
    response_model=RegisterResponse,
)
async def confirm_registration(
    request: RegisterConfirmRequest,
    session: Session = Depends(get_session),
) -> RegisterResponse:
    existing_user = session.exec(select(User).where(User.email == request.email)).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    verification = _get_latest_code(session, request.email)

    now = datetime.now(timezone.utc)
    expires_at = _ensure_timezone(verification.expires_at) if verification else None
    if (
        not verification
        or expires_at < now
        or not _verify_secret(request.code, verification.code_hash)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification code",
        )

    user = User(
        email=request.email,
        password_hash=_hash_secret(request.password),
        learning_preference=request.learning_preference,
    )
    verification.used = True
    session.add(user)
    session.add(verification)
    session.commit()
    session.refresh(user)

    return RegisterResponse(
        id=user.id,
        email=user.email,
        learning_preference=user.learning_preference,
        created_at=_ensure_timezone(user.created_at),
    )


@router.post(
    "/login",
    response_model=TokenResponse,
)
async def login_user(
    request: LoginRequest,
    session: Session = Depends(get_session),
) -> TokenResponse:
    user = session.exec(select(User).where(User.email == request.email)).first()
    if not user or not _verify_secret(request.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = _create_access_token({"sub": str(user.id)})
    return TokenResponse(access_token=access_token)


@router.get("/learning-preferences", response_model=List[str])
def list_learning_preferences() -> List[str]:
    return [pref.value for pref in LearningPreference]


@router.post(
    "/password-reset/request-code",
    status_code=status.HTTP_200_OK,
)
async def request_password_reset_code(
    request: PasswordResetRequest,
    session: Session = Depends(get_session),
    email_service: EmailService = Depends(get_email_service),
) -> Dict[str, str]:
    user = session.exec(select(User).where(User.email == request.email)).first()
    if user:
        session.exec(delete(EmailVerificationCode).where(EmailVerificationCode.email == request.email))
        code = _generate_verification_code()
        now = datetime.now(timezone.utc)
        verification = EmailVerificationCode(
            email=request.email,
            code_hash=_hash_secret(code),
            expires_at=now + timedelta(minutes=VERIFICATION_CODE_EXPIRE_MINUTES),
            created_at=now,
        )
        session.add(verification)
        session.commit()
        email_service.send_verification_code(request.email, code)

    return {"detail": "If the email exists, a verification code has been sent"}


@router.post(
    "/password-reset/confirm",
    status_code=status.HTTP_200_OK,
)
async def confirm_password_reset(
    request: PasswordResetConfirmRequest,
    session: Session = Depends(get_session),
) -> Dict[str, str]:
    user = session.exec(select(User).where(User.email == request.email)).first()
    verification = _get_latest_code(session, request.email)
    now = datetime.now(timezone.utc)
    expires_at = _ensure_timezone(verification.expires_at) if verification else None
    if (
        not user
        or not verification
        or expires_at < now
        or not _verify_secret(request.code, verification.code_hash)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification code",
        )

    user.password_hash = _hash_secret(request.new_password)
    verification.used = True
    session.add(user)
    session.add(verification)
    session.commit()
    session.refresh(user)

    return {"detail": "Password has been reset"}
