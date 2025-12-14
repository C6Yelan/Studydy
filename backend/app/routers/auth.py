import hashlib
import os
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlmodel import Session, select

from app.db import get_session
from app.models import User

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    learning_preference: str | None = None


class RegisterResponse(BaseModel):
    id: int
    email: EmailStr
    learning_preference: str | None = None
    created_at: datetime


def _hash_password(password: str) -> str:
    salt = os.urandom(16)
    hashed = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        100_000,
    )
    return f"{salt.hex()}:{hashed.hex()}"


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=RegisterResponse,
)
def register_user(
    request: RegisterRequest,
    session: Session = Depends(get_session),
) -> RegisterResponse:
    existing = session.exec(select(User).where(User.email == request.email)).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    user = User(
        email=request.email,
        password_hash=_hash_password(request.password),
        learning_preference=request.learning_preference,
    )
    session.add(user)
    session.commit()
    session.refresh(user)

    return RegisterResponse(
        id=user.id,
        email=user.email,
        learning_preference=user.learning_preference,
        created_at=user.created_at,
    )


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


@router.post("/login", status_code=status.HTTP_501_NOT_IMPLEMENTED)
def login_user(request: LoginRequest) -> None:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Login not implemented yet",
    )
