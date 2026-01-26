import pytest

from tests.conftest import register_user  # type: ignore

pytestmark = pytest.mark.anyio


async def test_password_reset_success(client_with_overrides):
    client, email_service = client_with_overrides
    await register_user(client, email_service, "reset-success@example.com", "oldpassword")

    request_resp = await client.post(
        "/auth/password-reset/request-code",
        json={"email": "reset-success@example.com"},
    )
    assert request_resp.status_code == 200
    reset_code = email_service.sent_codes["reset-success@example.com"]

    confirm_resp = await client.post(
        "/auth/password-reset/confirm",
        json={
            "email": "reset-success@example.com",
            "code": reset_code,
            "new_password": "newpassword123",
        },
    )
    assert confirm_resp.status_code == 200

    login_resp = await client.post(
        "/auth/login",
        json={"email": "reset-success@example.com", "password": "newpassword123"},
    )
    assert login_resp.status_code == 200
    assert login_resp.json()["email"] == "reset-success@example.com"


async def test_password_reset_invalid_code_does_not_change_password(client_with_overrides):
    client, email_service = client_with_overrides
    await register_user(client, email_service, "reset-fail@example.com", "oldpassword")

    request_resp = await client.post(
        "/auth/password-reset/request-code",
        json={"email": "reset-fail@example.com"},
    )
    assert request_resp.status_code == 200
    actual_code = email_service.sent_codes["reset-fail@example.com"]
    wrong_code = "000000" if actual_code != "000000" else "999999"

    confirm_resp = await client.post(
        "/auth/password-reset/confirm",
        json={
            "email": "reset-fail@example.com",
            "code": wrong_code,
            "new_password": "newpassword123",
        },
    )
    assert confirm_resp.status_code == 400

    login_old = await client.post(
        "/auth/login",
        json={"email": "reset-fail@example.com", "password": "oldpassword"},
    )
    assert login_old.status_code == 200
    assert login_old.json()["email"] == "reset-fail@example.com"

    login_new = await client.post(
        "/auth/login",
        json={"email": "reset-fail@example.com", "password": "newpassword123"},
    )
    assert login_new.status_code == 401
