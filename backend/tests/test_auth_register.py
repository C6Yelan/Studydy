from typing import Generator

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app import db
from app.db import get_session
from app.main import app


def create_test_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def override_get_session(engine) -> Generator[Session, None, None]:
    def _get_session():
        with Session(engine) as session:
            yield session

    return _get_session


def setup_test_app():
    engine = create_test_engine()
    SQLModel.metadata.create_all(engine)
    db.engine = engine
    app.dependency_overrides[get_session] = override_get_session(engine)
    return engine


def teardown_test_app():
    app.dependency_overrides.clear()


def test_register_success():
    setup_test_app()
    with TestClient(app) as client:
        response = client.post(
            "/auth/register",
            json={
                "email": "user@example.com",
                "password": "supersecret",
                "learning_preference": "visual",
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["email"] == "user@example.com"
        assert data["learning_preference"] == "visual"
        assert "password_hash" not in data
        assert data["id"] is not None
        assert data["created_at"]
    teardown_test_app()


def test_register_duplicate_email():
    setup_test_app()
    with TestClient(app) as client:
        first = client.post(
            "/auth/register",
            json={"email": "dup@example.com", "password": "pw123456"},
        )
        assert first.status_code == 201

        second = client.post(
            "/auth/register",
            json={"email": "dup@example.com", "password": "pw123456"},
        )

        assert second.status_code == 400
        assert second.json() == {"detail": "Email already registered"}
    teardown_test_app()
