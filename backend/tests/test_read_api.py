import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import Material
from scripts.seed_demo_data import DEMO_CONCEPTS, seed_demo_data


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


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


def test_get_material_returns_demo_summary(client, demo_material_id):
    response = client.get(f"/api/materials/{demo_material_id}")

    assert response.status_code == 200
    assert response.json() == {
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


def test_get_material_returns_404_for_missing_material(client):
    response = client.get("/api/materials/999999")

    assert response.status_code == 404
    assert response.json()["detail"] == "Material not found"


def test_list_material_concepts_returns_demo_concepts(client, demo_material_id):
    response = client.get(f"/api/materials/{demo_material_id}/concepts")
    body = response.json()

    assert response.status_code == 200
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


def test_list_material_concepts_returns_404_for_missing_material(client):
    response = client.get("/api/materials/999999/concepts")

    assert response.status_code == 404
    assert response.json()["detail"] == "Material not found"
