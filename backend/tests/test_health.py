import pytest

pytestmark = pytest.mark.anyio


async def test_health_endpoint(client_with_overrides) -> None:
    client, _ = client_with_overrides
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
