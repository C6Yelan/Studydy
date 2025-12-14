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
    original_engine = db.engine
    db.engine = engine
    app.dependency_overrides[get_session] = override_get_session(engine)
    return original_engine


def teardown_test_app(original_engine):
    app.dependency_overrides.clear()
    db.engine = original_engine


def test_login_success():
    original_engine = setup_test_app()
    with TestClient(app) as client:
        register_response = client.post(
            "/auth/register",
            json={
                "email": "login-success@example.com",
                "password": "correctpassword",
            },
        )
        assert register_response.status_code == 201

        response = client.post(
            "/auth/login",
            json={
                "email": "login-success@example.com",
                "password": "correctpassword",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["token_type"] == "bearer"
        assert data["access_token"]
    teardown_test_app(original_engine)


def test_login_incorrect_credentials():
    original_engine = setup_test_app()
    with TestClient(app) as client:
        client.post(
            "/auth/register",
            json={
                "email": "login-fail@example.com",
                "password": "correctpassword",
            },
        )

        response = client.post(
            "/auth/login",
            json={
                "email": "login-fail@example.com",
                "password": "wrongpassword",
            },
        )

        assert response.status_code == 401
        assert response.json() == {"detail": "Incorrect email or password"}
    teardown_test_app(original_engine)
