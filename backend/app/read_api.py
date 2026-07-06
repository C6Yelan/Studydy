from collections.abc import Generator
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Concept, ConceptRelation, Evidence, Material, MaterialBlock

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
            "decision": "accepted",
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
    relation_type = (
        relation.relation_type.value
        if hasattr(relation.relation_type, "value")
        else relation.relation_type
    )
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


def _get_material_or_404(db: Session, material_id: int) -> Material:
    material = db.get(Material, material_id)
    if material is None:
        raise HTTPException(status_code=404, detail="Material not found")
    return material


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
