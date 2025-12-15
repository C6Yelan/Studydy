from datetime import datetime, timezone
from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from app.core.security import create_access_token, hash_secret, verify_secret
from app.db import get_session
from app.models import LearningPreference, User
from app.schemas.auth import (
    LoginRequest,
    PasswordResetConfirmRequest,
    PasswordResetRequest,
    RegisterConfirmRequest,
    RegisterRequestCode,
    RegisterResponse,
    TokenResponse,
)
from app.services import get_email_service
from app.services.email import EmailService
from app.services.verification_codes import (
    create_code,
    delete_codes_for_email,
    is_code_valid,
    latest_unused_code,
    mark_code_used,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _ensure_timezone(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


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

    delete_codes_for_email(session, request.email)
    _, code = create_code(session, request.email)
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

    verification = latest_unused_code(session, request.email)
    now = datetime.now(timezone.utc)
    if (
        not verification
        or not is_code_valid(verification, request.code, now)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification code",
        )

    user = User(
        email=request.email,
        password_hash=hash_secret(request.password),
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
    if not user or not verify_secret(request.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token({"sub": str(user.id)})
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
        delete_codes_for_email(session, request.email)
        verification, code = create_code(session, request.email)
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
    verification = latest_unused_code(session, request.email)
    now = datetime.now(timezone.utc)
    if (
        not user
        or not verification
        or not is_code_valid(verification, request.code, now)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification code",
        )

    user.password_hash = hash_secret(request.new_password)
    mark_code_used(session, verification)
    session.add(user)
    session.commit()
    session.refresh(user)

    return {"detail": "Password has been reset"}
