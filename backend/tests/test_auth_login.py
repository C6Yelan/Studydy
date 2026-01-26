import pytest

from tests.conftest import register_user  # type: ignore

pytestmark = pytest.mark.anyio


async def test_login_success(client_with_overrides):
    client, fake_email_service = client_with_overrides
    await register_user(client, fake_email_service, "login-success@example.com", "correctpassword")

    response = await client.post(
        "/auth/login",
        json={
            "email": "login-success@example.com",
            "password": "correctpassword",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "login-success@example.com"
    assert data["id"] is not None
    assert data["created_at"]

    me_response = await client.get("/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["email"] == "login-success@example.com"


async def test_login_incorrect_credentials(client_with_overrides):
    client, fake_email_service = client_with_overrides
    await register_user(client, fake_email_service, "login-fail@example.com", "correctpassword")

    response = await client.post(
        "/auth/login",
        json={
            "email": "login-fail@example.com",
            "password": "wrongpassword",
        },
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Incorrect email or password"}


async def test_logout_clears_session(client_with_overrides):
    client, fake_email_service = client_with_overrides
    await register_user(client, fake_email_service, "logout@example.com", "correctpassword")

    login_response = await client.post(
        "/auth/login",
        json={
            "email": "logout@example.com",
            "password": "correctpassword",
        },
    )
    assert login_response.status_code == 200

    logout_response = await client.post("/auth/logout")
    assert logout_response.status_code == 200
    assert logout_response.json() == {"detail": "Logged out"}

    me_response = await client.get("/auth/me")
    assert me_response.status_code == 401
    assert me_response.json() == {"detail": "Not authenticated"}
