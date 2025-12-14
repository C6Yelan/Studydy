import pytest

pytestmark = pytest.mark.anyio


async def test_request_code_success(client_with_overrides):
    client, fake_email_service = client_with_overrides
    response = await client.post(
        "/auth/register/request-code",
        json={"email": "user@example.com", "password": "supersecret"},
    )

    assert response.status_code == 200
    assert response.json() == {"detail": "Verification code sent"}
    assert fake_email_service.sent_codes["user@example.com"]


async def test_confirm_registration_success(client_with_overrides):
    client, fake_email_service = client_with_overrides
    await client.post(
        "/auth/register/request-code",
        json={"email": "user2@example.com", "password": "supersecret"},
    )
    code = fake_email_service.sent_codes["user2@example.com"]

    response = await client.post(
        "/auth/register/confirm",
        json={
            "email": "user2@example.com",
            "password": "supersecret",
            "code": code,
            "learning_preference": "visual",
        },
    )

    assert response.status_code == 201
    data = response.json()
    assert data["email"] == "user2@example.com"
    assert data["learning_preference"] == "visual"
    assert "password_hash" not in data
    assert data["id"] is not None
    assert data["created_at"]


async def test_confirm_wrong_code_fails(client_with_overrides):
    client, fake_email_service = client_with_overrides
    await client.post(
        "/auth/register/request-code",
        json={"email": "user3@example.com", "password": "supersecret"},
    )
    actual_code = fake_email_service.sent_codes["user3@example.com"]
    wrong_code = "000000" if actual_code != "000000" else "999999"

    response = await client.post(
        "/auth/register/confirm",
        json={
            "email": "user3@example.com",
            "password": "supersecret",
            "code": wrong_code,
        },
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid or expired verification code"}


async def test_duplicate_email_fails(client_with_overrides):
    client, fake_email_service = client_with_overrides
    await client.post(
        "/auth/register/request-code",
        json={"email": "dup@example.com", "password": "supersecret"},
    )
    code = fake_email_service.sent_codes["dup@example.com"]
    confirm_response = await client.post(
        "/auth/register/confirm",
        json={
            "email": "dup@example.com",
            "password": "supersecret",
            "code": code,
        },
    )
    assert confirm_response.status_code == 201

    duplicate = await client.post(
        "/auth/register/request-code",
        json={"email": "dup@example.com", "password": "supersecret"},
    )

    assert duplicate.status_code == 400
    assert duplicate.json() == {"detail": "Email already registered"}
