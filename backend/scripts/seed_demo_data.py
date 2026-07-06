from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.engine import Connection

from app.db import engine
from app.models import (
    Concept,
    ConceptRelation,
    Evidence,
    LearningPath,
    LearningPathNode,
    Material,
    MaterialBlock,
)

DEMO_MATERIAL = {
    "title": "Linear Structures and ADT",
    "subject": "data_structure",
    "chapter_range": "Linear Structures",
}

DEMO_CONCEPTS = [
    "Big-O",
    "ADT",
    "Array",
    "Linked List",
    "Stack",
    "Queue",
    "Implementation",
]

DEMO_RELATIONS = [
    ("Big-O", "Implementation", "prerequisite"),
    ("ADT", "Stack", "contains"),
    ("ADT", "Queue", "contains"),
    ("Array", "Stack", "application"),
    ("Linked List", "Stack", "application"),
    ("Array", "Queue", "application"),
    ("Linked List", "Queue", "application"),
    ("Stack", "Queue", "confusing"),
]

DEMO_LEARNING_PATH = [
    "Big-O",
    "ADT",
    "Array",
    "Linked List",
    "Stack",
    "Queue",
    "Implementation",
]

# Demo evidence is seeded as summaries; these are not manually verified exact quotes.
CONCEPT_SUMMARIES = {
    "Big-O": "Summary evidence: Big-O describes asymptotic growth used to reason about implementation tradeoffs.",
    "ADT": "Summary evidence: ADT separates behavior from concrete representation in linear structures.",
    "Array": "Summary evidence: Array-based storage supports indexed linear structure implementations.",
    "Linked List": "Summary evidence: Linked lists represent linear structures through linked nodes.",
    "Stack": "Summary evidence: Stack is a linear ADT with last-in-first-out access.",
    "Queue": "Summary evidence: Queue is a linear ADT with first-in-first-out access.",
    "Implementation": "Summary evidence: Implementation here means array-backed and linked-list-backed realizations.",
}


def _scalar_or_none(connection: Connection, statement: Select[Any]) -> Any:
    return connection.execute(statement).scalar_one_or_none()


def _get_or_create_material(connection: Connection) -> int:
    material_id = _scalar_or_none(
        connection,
        select(Material.id).where(
            Material.title == DEMO_MATERIAL["title"],
            Material.subject == DEMO_MATERIAL["subject"],
            Material.chapter_range == DEMO_MATERIAL["chapter_range"],
        ),
    )
    if material_id is not None:
        return material_id

    return connection.execute(Material.__table__.insert().values(**DEMO_MATERIAL).returning(Material.id)).scalar_one()


def _get_or_create_block(connection: Connection, material_id: int, block_index: int, concept_name: str) -> int:
    block_id = _scalar_or_none(
        connection,
        select(MaterialBlock.id).where(
            MaterialBlock.material_id == material_id,
            MaterialBlock.block_index == block_index,
        ),
    )
    if block_id is not None:
        return block_id

    return connection.execute(
        MaterialBlock.__table__
        .insert()
        .values(
            material_id=material_id,
            block_index=block_index,
            page_number=block_index + 1,
            block_type="summary",
            content=CONCEPT_SUMMARIES[concept_name],
        )
        .returning(MaterialBlock.id)
    ).scalar_one()


def _get_or_create_concept(connection: Connection, name: str) -> int:
    concept_id = _scalar_or_none(connection, select(Concept.id).where(Concept.name == name))
    if concept_id is not None:
        return concept_id

    return connection.execute(
        Concept.__table__
        .insert()
        .values(
            name=name,
            description=f"Demo concept for {name}.",
            score_detail={"seed": "demo", "concept": name},
        )
        .returning(Concept.id)
    ).scalar_one()


def _get_or_create_relation(
    connection: Connection,
    concept_ids: Mapping[str, int],
    source_name: str,
    target_name: str,
    relation_type: str,
) -> int:
    source_id = concept_ids[source_name]
    target_id = concept_ids[target_name]
    relation_id = _scalar_or_none(
        connection,
        select(ConceptRelation.id).where(
            ConceptRelation.source_concept_id == source_id,
            ConceptRelation.target_concept_id == target_id,
            ConceptRelation.relation_type == relation_type,
        ),
    )
    if relation_id is not None:
        return relation_id

    return connection.execute(
        ConceptRelation.__table__
        .insert()
        .values(
            source_concept_id=source_id,
            target_concept_id=target_id,
            relation_type=relation_type,
            description=f"Demo {relation_type} relation from {source_name} to {target_name}.",
            score_detail={"seed": "demo", "source": source_name, "target": target_name},
        )
        .returning(ConceptRelation.id)
    ).scalar_one()


def _ensure_concept_evidence(
    connection: Connection,
    material_id: int,
    block_id: int,
    concept_id: int,
    concept_name: str,
) -> None:
    quote_text = CONCEPT_SUMMARIES[concept_name]
    existing = _scalar_or_none(
        connection,
        select(Evidence.id).where(
            Evidence.material_id == material_id,
            Evidence.block_id == block_id,
            Evidence.concept_id == concept_id,
            Evidence.relation_id.is_(None),
            Evidence.evidence_type == "summary",
            Evidence.quote_text == quote_text,
        ),
    )
    if existing is not None:
        return

    connection.execute(
        Evidence.__table__.insert().values(
            material_id=material_id,
            block_id=block_id,
            concept_id=concept_id,
            page_number=None,
            quote_text=quote_text,
            evidence_type="summary",
            metadata={"seed": "demo", "source": "course_slide_summary"},
        )
    )


def _ensure_relation_evidence(
    connection: Connection,
    material_id: int,
    relation_id: int,
    source_name: str,
    target_name: str,
    relation_type: str,
) -> None:
    quote_text = f"Summary evidence: {source_name} has a {relation_type} relationship to {target_name}."
    existing = _scalar_or_none(
        connection,
        select(Evidence.id).where(
            Evidence.material_id == material_id,
            Evidence.block_id.is_(None),
            Evidence.concept_id.is_(None),
            Evidence.relation_id == relation_id,
            Evidence.evidence_type == "summary",
            Evidence.quote_text == quote_text,
        ),
    )
    if existing is not None:
        return

    connection.execute(
        Evidence.__table__.insert().values(
            material_id=material_id,
            relation_id=relation_id,
            quote_text=quote_text,
            evidence_type="summary",
            metadata={"seed": "demo", "source": "course_slide_summary"},
        )
    )


def _get_or_create_learning_path(connection: Connection, material_id: int) -> int:
    title = "Linear Structures and ADT Learning Path"
    learning_path_id = _scalar_or_none(
        connection,
        select(LearningPath.id).where(LearningPath.material_id == material_id, LearningPath.title == title),
    )
    if learning_path_id is not None:
        return learning_path_id

    return connection.execute(
        LearningPath.__table__.insert().values(material_id=material_id, title=title).returning(LearningPath.id)
    ).scalar_one()


def _ensure_learning_path_node(
    connection: Connection,
    learning_path_id: int,
    concept_id: int,
    position: int,
) -> None:
    existing = _scalar_or_none(
        connection,
        select(LearningPathNode.id).where(
            LearningPathNode.learning_path_id == learning_path_id,
            LearningPathNode.concept_id == concept_id,
        ),
    )
    if existing is not None:
        return

    connection.execute(
        LearningPathNode.__table__.insert().values(
            learning_path_id=learning_path_id,
            concept_id=concept_id,
            position=position,
        )
    )


def seed_demo_data() -> None:
    with engine.begin() as connection:
        material_id = _get_or_create_material(connection)
        concept_ids = {name: _get_or_create_concept(connection, name) for name in DEMO_CONCEPTS}
        block_ids = {
            name: _get_or_create_block(connection, material_id, index, name)
            for index, name in enumerate(DEMO_CONCEPTS)
        }

        for name in DEMO_CONCEPTS:
            _ensure_concept_evidence(connection, material_id, block_ids[name], concept_ids[name], name)

        for source_name, target_name, relation_type in DEMO_RELATIONS:
            relation_id = _get_or_create_relation(connection, concept_ids, source_name, target_name, relation_type)
            _ensure_relation_evidence(connection, material_id, relation_id, source_name, target_name, relation_type)

        learning_path_id = _get_or_create_learning_path(connection, material_id)
        for position, concept_name in enumerate(DEMO_LEARNING_PATH, start=1):
            _ensure_learning_path_node(connection, learning_path_id, concept_ids[concept_name], position)


if __name__ == "__main__":
    seed_demo_data()
