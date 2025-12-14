import pytest

pytestmark = pytest.mark.anyio


async def _register_user(client, email_service, email: str, password: str) -> None:
    request_response = await client.post(
        "/auth/register/request-code",
        json={"email": email, "password": password},
    )
    assert request_response.status_code == 200
    code = email_service.sent_codes[email]

    confirm_response = await client.post(
        "/auth/register/confirm",
        json={"email": email, "password": password, "code": code},
    )
    assert confirm_response.status_code == 201


async def test_login_success(client_with_overrides):
    client, fake_email_service = client_with_overrides
    await _register_user(client, fake_email_service, "login-success@example.com", "correctpassword")

    response = await client.post(
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


async def test_login_incorrect_credentials(client_with_overrides):
    client, fake_email_service = client_with_overrides
    await _register_user(client, fake_email_service, "login-fail@example.com", "correctpassword")

    response = await client.post(
        "/auth/login",
        json={
            "email": "login-fail@example.com",
            "password": "wrongpassword",
        },
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Incorrect email or password"}
