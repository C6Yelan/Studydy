from typing import AsyncGenerator, Tuple

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app import db
from app.db import get_session
from app.main import app
from app.services.email import EmailService, get_email_service


class FakeEmailService(EmailService):
    def __init__(self) -> None:
        self.last_code: str | None = None
        self.sent_codes: dict[str, str] = {}

    def send_verification_code(self, email: str, code: str) -> None:
        self.last_code = code
        self.sent_codes[email] = code


@pytest.fixture()
async def client_with_overrides() -> AsyncGenerator[Tuple[httpx.AsyncClient, FakeEmailService], None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    original_engine = db.engine
    db.engine = engine

    async def override_get_session():
        with Session(engine) as session:
            yield session

    fake_email_service = FakeEmailService()

    app.dependency_overrides[get_session] = override_get_session

    async def override_email_service():
        return fake_email_service

    app.dependency_overrides[get_email_service] = override_email_service

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client, fake_email_service

    app.dependency_overrides.clear()
    db.engine = original_engine


@pytest.fixture()
def anyio_backend():
    return "asyncio"


async def register_user(client: httpx.AsyncClient, email_service: FakeEmailService, email: str, password: str, learning_preference: str | None = None) -> None:
    response = await client.post(
        "/auth/register",
        json={
            "email": email,
            "password": password,
            "learning_preference": learning_preference,
        },
    )
    assert response.status_code == 201
