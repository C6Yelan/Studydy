from collections.abc import Generator
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Concept, ConceptRelation, Evidence, LearningPath, LearningPathNode, Material, MaterialBlock

router = APIRouter(prefix="/api")


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return value


def _enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _decision_from_review(needs_review: bool) -> str:
    return "needs_review" if needs_review else "accepted"


# Fields absent from the Phase 1 schema use the approved v1 read-contract placeholders.
def _material_summary(material: Material) -> dict[str, Any]:
    return {
        "id": material.id,
        "title": material.title,
        "subject": material.subject,
        "chapter_range": material.chapter_range,
        "file_name": None,
        "upload_status": "validated",
        "processing_status": "completed",
        "created_at": None,
        "updated_at": None,
    }


def _concept_summary(concept: Concept) -> dict[str, Any]:
    return {
        "id": concept.id,
        "name": concept.name,
        "summary": concept.description,
        "keywords": [],
        "difficulty_level": None,
        "importance_level": None,
        "status": "accepted",
        "score": {
            "score_value": _optional_float(concept.score_value),
            "score_level": concept.score_level,
            "decision": _decision_from_review(concept.needs_review),
            "score_detail": concept.score_detail,
            "score_reason": concept.score_reason,
        },
        "needs_review": concept.needs_review,
        "review_reason": None,
        "scope_note": None,
    }


def _material_concepts(db: Session, material_id: int) -> list[Concept]:
    # Use the first evidence location as the material-local concept order.
    return db.execute(
        select(Concept)
        .join(Evidence, Evidence.concept_id == Concept.id)
        .outerjoin(MaterialBlock, Evidence.block_id == MaterialBlock.id)
        .where(Evidence.material_id == material_id)
        .group_by(Concept.id)
        .order_by(
            func.min(MaterialBlock.block_index).nullslast(),
            func.min(Evidence.id),
            Concept.id,
        )
    ).scalars().all()


def _knowledge_map_node(concept: Concept, index: int) -> dict[str, Any]:
    return {
        "id": str(concept.id),
        "type": "concept",
        "position": {
            "x": (index % 4) * 240,
            "y": (index // 4) * 160,
        },
        "data": {
            "label": concept.name,
            "summary": concept.description,
            "difficulty_level": None,
            "importance_level": None,
            "needs_review": concept.needs_review,
            "score_value": _optional_float(concept.score_value),
        },
    }


def _knowledge_map_edge(relation: ConceptRelation) -> dict[str, Any]:
    relation_type = _enum_value(relation.relation_type)
    return {
        "id": str(relation.id),
        "source": str(relation.source_concept_id),
        "target": str(relation.target_concept_id),
        "type": "concept_relation",
        "label": relation_type,
        "data": {
            "relation_type": relation_type,
            "reason": relation.description,
            "score_value": _optional_float(relation.score_value),
            "needs_review": relation.needs_review,
        },
    }


def _evidence_summary(evidence: Evidence) -> dict[str, Any]:
    return {
        "id": evidence.id,
        "material_id": evidence.material_id,
        "block_id": evidence.block_id,
        "page_number": evidence.page_number,
        "quote_text": evidence.quote_text,
        "evidence_type": _enum_value(evidence.evidence_type),
        "metadata": evidence.metadata_,
    }


def _relation_summary(relation: ConceptRelation) -> dict[str, Any]:
    return {
        "id": relation.id,
        "source_concept_id": relation.source_concept_id,
        "target_concept_id": relation.target_concept_id,
        "relation_type": _enum_value(relation.relation_type),
        "reason": relation.description,
        "score": {
            "score_value": _optional_float(relation.score_value),
            "score_level": relation.score_level,
            "decision": _decision_from_review(relation.needs_review),
            "score_detail": relation.score_detail,
            "score_reason": relation.score_reason,
        },
        "needs_review": relation.needs_review,
    }


def _learning_path_response(path_id: int | None, nodes: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": path_id,
        "path_type": "initial",
        "status": "accepted",
        "nodes": nodes,
        "needs_review": False,
        "review_reason": None,
    }


def _learning_path_node_summary(order_index: int, concept_id: int, concept_name: str) -> dict[str, Any]:
    return {
        "order_index": order_index,
        "concept_id": concept_id,
        "concept_name": concept_name,
        "reason": None,
        "is_required": True,
    }


def _get_material_or_404(db: Session, material_id: int) -> Material:
    material = db.get(Material, material_id)
    if material is None:
        raise HTTPException(status_code=404, detail="Material not found")
    return material


def _get_concept_or_404(db: Session, concept_id: int) -> Concept:
    concept = db.get(Concept, concept_id)
    if concept is None:
        raise HTTPException(status_code=404, detail="Concept not found")
    return concept


@router.get("/materials/{material_id}")
def get_material(material_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    material = _get_material_or_404(db, material_id)
    return _material_summary(material)


@router.get("/materials/{material_id}/concepts")
def list_material_concepts(material_id: int, db: Session = Depends(get_db)) -> dict[str, list[dict[str, Any]]]:
    _get_material_or_404(db, material_id)

    concepts = _material_concepts(db, material_id)
    return {"items": [_concept_summary(concept) for concept in concepts]}


@router.get("/materials/{material_id}/knowledge-map")
def get_knowledge_map(material_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    _get_material_or_404(db, material_id)

    concepts = _material_concepts(db, material_id)
    nodes = [_knowledge_map_node(concept, index) for index, concept in enumerate(concepts)]
    node_ids = {concept.id for concept in concepts}

    relations = db.execute(
        select(ConceptRelation)
        .where(
            ConceptRelation.source_concept_id.in_(node_ids),
            ConceptRelation.target_concept_id.in_(node_ids),
        )
        .order_by(ConceptRelation.id)
    ).scalars().all()

    return {
        "nodes": nodes,
        "edges": [_knowledge_map_edge(relation) for relation in relations],
        "warnings": [],
    }


@router.get("/concepts/{concept_id}")
def get_concept_detail(concept_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    concept = _get_concept_or_404(db, concept_id)

    evidence_list = db.execute(
        select(Evidence)
        .where(Evidence.concept_id == concept_id)
        .order_by(Evidence.material_id, Evidence.block_id.nullslast(), Evidence.id)
    ).scalars().all()
    incoming_relations = db.execute(
        select(ConceptRelation)
        .where(ConceptRelation.target_concept_id == concept_id)
        .order_by(ConceptRelation.id)
    ).scalars().all()
    outgoing_relations = db.execute(
        select(ConceptRelation)
        .where(ConceptRelation.source_concept_id == concept_id)
        .order_by(ConceptRelation.id)
    ).scalars().all()
    learning_path_position = db.execute(
        select(LearningPathNode.position)
        .where(LearningPathNode.concept_id == concept_id)
        .order_by(LearningPathNode.position)
    ).scalars().first()

    return {
        "concept": _concept_summary(concept),
        "evidence_list": [_evidence_summary(evidence) for evidence in evidence_list],
        "resource_list": [],
        "incoming_relations": [_relation_summary(relation) for relation in incoming_relations],
        "outgoing_relations": [_relation_summary(relation) for relation in outgoing_relations],
        "learning_path_position": learning_path_position,
        "mastery_status": "not_started",
        "warnings": [],
    }


@router.get("/materials/{material_id}/learning-path")
def get_learning_path(material_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    _get_material_or_404(db, material_id)

    learning_path = db.execute(
        select(LearningPath)
        .where(LearningPath.material_id == material_id)
        .order_by(LearningPath.id)
    ).scalars().first()
    if learning_path is None:
        concepts = _material_concepts(db, material_id)
        return _learning_path_response(
            None,
            [
                _learning_path_node_summary(index, concept.id, concept.name)
                for index, concept in enumerate(concepts, start=1)
            ],
        )

    rows = db.execute(
        select(LearningPathNode.position, Concept.id, Concept.name)
        .join(Concept, Concept.id == LearningPathNode.concept_id)
        .where(LearningPathNode.learning_path_id == learning_path.id)
        .order_by(LearningPathNode.position)
    ).all()

    return _learning_path_response(
        learning_path.id,
        [
            _learning_path_node_summary(position, concept_id, concept_name)
            for position, concept_id, concept_name in rows
        ],
    )
