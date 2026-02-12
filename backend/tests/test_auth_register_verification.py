import pytest

pytestmark = pytest.mark.anyio


async def test_register_success(client_with_overrides):
    client, _ = client_with_overrides
    response = await client.post(
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

    me_response = await client.get("/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["email"] == "user@example.com"


async def test_register_duplicate_email_fails(client_with_overrides):
    client, _ = client_with_overrides
    first = await client.post(
        "/auth/register",
        json={"email": "dup@example.com", "password": "supersecret"},
    )
    assert first.status_code == 201

    duplicate = await client.post(
        "/auth/register",
        json={"email": "dup@example.com", "password": "supersecret"},
    )

    assert duplicate.status_code == 400
    assert duplicate.json() == {"detail": "Email already registered"}
