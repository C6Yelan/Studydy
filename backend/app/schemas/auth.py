from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.core.config import MIN_PASSWORD_LENGTH, VERIFICATION_CODE_LENGTH
from app.models import LearningPreference


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH)
    learning_preference: LearningPreference | None = None


class UserResponse(BaseModel):
    id: int
    email: EmailStr
    learning_preference: LearningPreference | None = None
    created_at: datetime


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirmRequest(BaseModel):
    email: EmailStr
    code: str = Field(min_length=VERIFICATION_CODE_LENGTH, max_length=VERIFICATION_CODE_LENGTH)
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH)
