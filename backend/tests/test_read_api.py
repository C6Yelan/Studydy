import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import Concept, Evidence, Material, MaterialBlock
from scripts.seed_demo_data import DEMO_CONCEPTS, DEMO_LEARNING_PATH, DEMO_RELATIONS, seed_demo_data


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


@pytest.fixture()
def demo_stack_concept_id(engine) -> int:
    seed_demo_data()
    with engine.connect() as connection:
        return connection.execute(select(Concept.id).where(Concept.name == "Stack")).scalar_one()


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


@pytest.mark.parametrize(
    "path",
    [
        "/api/materials/999999",
        "/api/materials/999999/concepts",
        "/api/materials/999999/knowledge-map",
        "/api/materials/999999/learning-path",
    ],
)
def test_material_scoped_endpoints_return_404_for_missing_material(client, path):
    response = client.get(path)

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


def test_get_knowledge_map_returns_demo_nodes_edges(client, demo_material_id):
    response = client.get(f"/api/materials/{demo_material_id}/knowledge-map")
    body = response.json()

    assert response.status_code == 200
    assert body.keys() == {"nodes", "edges", "warnings"}
    assert body["warnings"] == []
    assert len(body["nodes"]) == 7
    assert len(body["edges"]) == 8

    node_ids = {node["id"] for node in body["nodes"]}
    assert all(edge["source"] in node_ids and edge["target"] in node_ids for edge in body["edges"])

    node_id_by_label = {node["data"]["label"]: node["id"] for node in body["nodes"]}
    expected_edges = {
        (node_id_by_label[source], node_id_by_label[target], relation_type)
        for source, target, relation_type in DEMO_RELATIONS
    }
    actual_edges = {
        (edge["source"], edge["target"], edge["data"]["relation_type"])
        for edge in body["edges"]
    }
    assert actual_edges == expected_edges

    first_node = body["nodes"][0]
    assert {"id", "type", "position", "data"} <= first_node.keys()
    assert {"x", "y"} <= first_node["position"].keys()
    assert {
        "label",
        "summary",
        "difficulty_level",
        "importance_level",
        "needs_review",
        "score_value",
    } <= first_node["data"].keys()

    first_edge = body["edges"][0]
    assert {"id", "source", "target", "type", "label", "data"} <= first_edge.keys()
    assert {"relation_type", "reason", "score_value", "needs_review"} <= first_edge["data"].keys()


def test_get_concept_detail_returns_demo_contract(client, demo_stack_concept_id):
    response = client.get(f"/api/concepts/{demo_stack_concept_id}")
    body = response.json()

    assert response.status_code == 200
    assert body.keys() == {
        "concept",
        "evidence_list",
        "resource_list",
        "incoming_relations",
        "outgoing_relations",
        "learning_path_position",
        "mastery_status",
        "warnings",
    }
    assert body["concept"]["id"] == demo_stack_concept_id
    assert body["concept"]["name"] == "Stack"
    assert body["resource_list"] == []
    assert body["mastery_status"] == "not_started"
    assert body["learning_path_position"] == DEMO_LEARNING_PATH.index("Stack") + 1
    assert body["warnings"] == []

    assert len(body["evidence_list"]) >= 1
    first_evidence = body["evidence_list"][0]
    assert {
        "id",
        "material_id",
        "block_id",
        "page_number",
        "quote_text",
        "evidence_type",
        "metadata",
    } <= first_evidence.keys()
    assert first_evidence["evidence_type"] == "summary"

    assert len(body["incoming_relations"]) >= 1
    assert len(body["outgoing_relations"]) >= 1
    first_relation = body["incoming_relations"][0]
    assert {
        "id",
        "source_concept_id",
        "target_concept_id",
        "relation_type",
        "reason",
        "score",
        "needs_review",
    } <= first_relation.keys()
    assert first_relation["target_concept_id"] == demo_stack_concept_id


def test_get_concept_detail_derives_score_decision_from_review_state(client, engine, demo_stack_concept_id):
    with engine.connect() as connection:
        original_needs_review = connection.execute(
            select(Concept.needs_review).where(Concept.id == demo_stack_concept_id)
        ).scalar_one()

    with engine.begin() as connection:
        connection.execute(
            Concept.__table__
            .update()
            .where(Concept.id == demo_stack_concept_id)
            .values(needs_review=True)
        )

    try:
        response = client.get(f"/api/concepts/{demo_stack_concept_id}")
        body = response.json()

        assert response.status_code == 200
        assert body["concept"]["needs_review"] is True
        assert body["concept"]["score"]["decision"] == "needs_review"
    finally:
        with engine.begin() as connection:
            connection.execute(
                Concept.__table__
                .update()
                .where(Concept.id == demo_stack_concept_id)
                .values(needs_review=original_needs_review)
            )


def test_get_concept_detail_returns_404_for_missing_concept(client):
    response = client.get("/api/concepts/999999")

    assert response.status_code == 404
    assert response.json()["detail"] == "Concept not found"


def test_get_learning_path_returns_demo_order(client, demo_material_id):
    response = client.get(f"/api/materials/{demo_material_id}/learning-path")
    body = response.json()

    assert response.status_code == 200
    assert body.keys() == {"id", "path_type", "status", "nodes", "needs_review", "review_reason"}
    assert body["id"] is not None
    assert body["path_type"] == "initial"
    assert body["status"] == "accepted"
    assert body["needs_review"] is False
    assert body["review_reason"] is None
    assert [node["concept_name"] for node in body["nodes"]] == DEMO_LEARNING_PATH

    first_node = body["nodes"][0]
    assert {"order_index", "concept_id", "concept_name", "reason", "is_required"} <= first_node.keys()
    assert first_node["order_index"] == 1
    assert first_node["reason"] is None
    assert first_node["is_required"] is True


def test_get_learning_path_falls_back_to_material_concept_order(client, engine):
    seed_demo_data()
    with engine.begin() as connection:
        concept_ids = {
            name: connection.execute(select(Concept.id).where(Concept.name == name)).scalar_one()
            for name in ("Big-O", "ADT")
        }
        material_id = connection.execute(
            Material.__table__
            .insert()
            .values(
                title="Fallback Learning Path Material",
                subject="data_structure",
                chapter_range="Fallback",
            )
            .returning(Material.id)
        ).scalar_one()
        block_ids = {}
        for block_index, concept_name in enumerate(("Big-O", "ADT")):
            block_ids[concept_name] = connection.execute(
                MaterialBlock.__table__
                .insert()
                .values(
                    material_id=material_id,
                    block_index=block_index,
                    page_number=block_index + 1,
                    block_type="summary",
                    content=f"Fallback summary for {concept_name}.",
                )
                .returning(MaterialBlock.id)
            ).scalar_one()
            connection.execute(
                Evidence.__table__.insert().values(
                    material_id=material_id,
                    block_id=block_ids[concept_name],
                    concept_id=concept_ids[concept_name],
                    quote_text=f"Fallback summary evidence for {concept_name}.",
                    evidence_type="summary",
                    metadata={"test": "learning_path_fallback"},
                )
            )

    try:
        response = client.get(f"/api/materials/{material_id}/learning-path")
        body = response.json()

        assert response.status_code == 200
        assert body["id"] is None
        assert [node["concept_name"] for node in body["nodes"]] == ["Big-O", "ADT"]
        assert [node["order_index"] for node in body["nodes"]] == [1, 2]
    finally:
        with engine.begin() as connection:
            connection.execute(Material.__table__.delete().where(Material.id == material_id))
