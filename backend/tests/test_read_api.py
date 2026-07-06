import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy import select

from app.main import app
from app.models import Material
from scripts.seed_demo_data import DEMO_CONCEPTS, seed_demo_data


# Minimal ASGI caller keeps these smoke tests dependency-free.
async def _asgi_get(path: str) -> tuple[int, dict[str, str], Any]:
    body_parts: list[bytes] = []
    status_code: int | None = None
    response_headers: dict[str, str] = {}

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }

    request_sent = False

    async def receive() -> dict[str, Any]:
        nonlocal request_sent
        if request_sent:
            return {"type": "http.disconnect"}
        request_sent = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        nonlocal status_code, response_headers
        if message["type"] == "http.response.start":
            status_code = message["status"]
            response_headers = {
                key.decode().lower(): value.decode()
                for key, value in message.get("headers", [])
            }
        elif message["type"] == "http.response.body":
            body_parts.append(message.get("body", b""))

    app_call: Callable[[dict[str, Any], Callable[[], Awaitable[dict[str, Any]]], Callable[[dict[str, Any]], Awaitable[None]]], Awaitable[None]] = app  # type: ignore[assignment]
    await app_call(scope, receive, send)

    assert status_code is not None
    body = b"".join(body_parts)
    return status_code, response_headers, json.loads(body or b"null")


@pytest.fixture()
def api_get() -> Callable[[str], tuple[int, dict[str, str], Any]]:
    return lambda path: asyncio.run(_asgi_get(path))


@pytest.fixture()
def demo_material_id(engine) -> int:
    seed_demo_data()
    with engine.connect() as connection:
        return connection.execute(
            select(Material.id).where(
                Material.title == "Linear Structures and ADT",
                Material.subject == "data_structure",
                Material.chapter_range == "Linear Structures",
            )
        ).scalar_one()


def test_get_material_returns_demo_summary(api_get, demo_material_id):
    status_code, _, body = api_get(f"/api/materials/{demo_material_id}")

    assert status_code == 200
    assert body == {
        "id": demo_material_id,
        "title": "Linear Structures and ADT",
        "subject": "data_structure",
        "chapter_range": "Linear Structures",
        "file_name": None,
        "upload_status": "validated",
        "processing_status": "completed",
        "created_at": None,
        "updated_at": None,
    }


def test_get_material_returns_404_for_missing_material(api_get):
    status_code, _, body = api_get("/api/materials/999999")

    assert status_code == 404
    assert body["detail"] == "Material not found"


def test_list_material_concepts_returns_demo_concepts(api_get, demo_material_id):
    status_code, _, body = api_get(f"/api/materials/{demo_material_id}/concepts")

    assert status_code == 200
    assert [item["name"] for item in body["items"]] == DEMO_CONCEPTS
    assert len(body["items"]) == 7

    first = body["items"][0]
    assert {
        "id",
        "name",
        "summary",
        "keywords",
        "difficulty_level",
        "importance_level",
        "status",
        "score",
        "needs_review",
        "review_reason",
        "scope_note",
    } <= first.keys()
    assert first["keywords"] == []
    assert first["status"] == "accepted"
    assert first["score"]["decision"] == "accepted"


def test_list_material_concepts_returns_404_for_missing_material(api_get):
    status_code, _, body = api_get("/api/materials/999999/concepts")

    assert status_code == 404
    assert body["detail"] == "Material not found"
