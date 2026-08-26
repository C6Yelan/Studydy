from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from knowledge_map.artifacts import validate_knowledge_map
from pdf_evidence.study_material_output import validate_study_material_output
from runtime.storage.database import DatabaseConfigurationError
from runtime.storage.tables import KnowledgeMap, StudyMaterialOutput, database_session


class MapContextError(RuntimeError):
    """Knowledge Map 不存在、已失效或無法安全讀取。"""


@dataclass(frozen=True)
class EvidenceLocator:
    evidence_id: str
    page_ref: str
    page_number: int
    coordinate_space: str
    bbox: tuple[int | float, int | float, int | float, int | float]
    text: str


@dataclass(frozen=True)
class ClaimContext:
    claim_id: str
    text: str
    evidence: tuple[EvidenceLocator, ...]


@dataclass(frozen=True)
class SupplementaryResourceContext:
    promotion_id: str
    resource_concept_id: str
    resource_id: str
    label: str
    title: str
    authors: tuple[str, ...]
    source_url: str
    citation: str
    license: str
    license_url: str
    use_boundary: str
    page_numbers: tuple[int, ...]
    resource_evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class FormalConceptContext:
    formal_concept_id: str
    label: str
    source_page_numbers: tuple[int, ...]
    claims: tuple[ClaimContext, ...]
    supplementary_resources: tuple[SupplementaryResourceContext, ...]


@dataclass(frozen=True)
class RelationEvidenceContext:
    owner_formal_concept_id: str
    claim_id: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class PublishedRelationContext:
    relation_id: str
    relation_type: Literal["prerequisite", "contains", "related"]
    source_formal_concept_id: str
    target_formal_concept_id: str
    is_in_prerequisite_cycle: bool
    relation_evidence: tuple[RelationEvidenceContext, ...]


@dataclass(frozen=True)
class MapContext:
    learner_id: UUID
    material_id: UUID
    knowledge_map_revision: str
    formal_concepts: tuple[FormalConceptContext, ...]
    relations: tuple[PublishedRelationContext, ...]
    initial_learning_path: tuple[str, ...]


def _unavailable() -> MapContextError:
    return MapContextError("KNOWLEDGE_MAP_UNAVAILABLE")


def _build_context(
    learner_id: UUID,
    material_id: UUID,
    knowledge_map_revision: str,
    knowledge_map: object,
    study_material_output: object,
) -> MapContext:
    if (
        validate_knowledge_map(knowledge_map) is not None
        or validate_study_material_output(study_material_output) is not None
    ):
        raise _unavailable()
    assert isinstance(knowledge_map, dict)
    assert isinstance(study_material_output, dict)
    if (
        knowledge_map["revision"] != knowledge_map_revision
        or knowledge_map["decision"] == "reject"
        or not knowledge_map["formal_concepts"]
        or knowledge_map["source_output_id"] != study_material_output["output_id"]
        or knowledge_map["material_ref"] != study_material_output["material_ref"]
        or knowledge_map["evidence_index"] != study_material_output["evidence_index"]
    ):
        raise _unavailable()

    evidence_texts = {
        evidence["evidence_id"]: evidence["text"]
        for evidence in study_material_output["evidence_text_index"]
    }
    if set(evidence_texts) != {
        evidence["evidence_id"] for evidence in knowledge_map["evidence_index"]
    }:
        raise _unavailable()

    evidence_by_id = {
        evidence["evidence_id"]: EvidenceLocator(
            evidence_id=evidence["evidence_id"],
            page_ref=evidence["page_ref"],
            page_number=evidence["page_number"],
            coordinate_space=evidence["region"]["coordinate_space"],
            bbox=tuple(evidence["region"]["bbox"]),
            text=evidence_texts[evidence["evidence_id"]],
        )
        for evidence in knowledge_map["evidence_index"]
    }
    formal_concepts = tuple(
        FormalConceptContext(
            formal_concept_id=concept["formal_concept_id"],
            label=concept["label"],
            source_page_numbers=tuple(concept["source_page_numbers"]),
            claims=tuple(
                ClaimContext(
                    claim_id=claim["claim_id"],
                    text=claim["text"],
                    evidence=tuple(
                        evidence_by_id[evidence_id]
                        for evidence_id in claim["evidence_ids"]
                    ),
                )
                for claim in concept["claims"]
            ),
            supplementary_resources=tuple(
                SupplementaryResourceContext(
                    promotion_id=resource["promotion_id"],
                    resource_concept_id=resource["resource_concept_id"],
                    resource_id=resource["resource_id"],
                    label=resource["label"],
                    title=resource["title"],
                    authors=tuple(resource["authors"]),
                    source_url=resource["source_url"],
                    citation=resource["citation"],
                    license=resource["license"],
                    license_url=resource["license_url"],
                    use_boundary=resource["use_boundary"],
                    page_numbers=tuple(resource["page_numbers"]),
                    resource_evidence_ids=tuple(resource["resource_evidence_ids"]),
                )
                for resource in concept["supplementary_resources"]
            ),
        )
        for concept in knowledge_map["formal_concepts"]
    )
    relations = tuple(
        PublishedRelationContext(
            relation_id=relation["relation_id"],
            relation_type=relation["type"],
            source_formal_concept_id=relation["source_formal_concept_id"],
            target_formal_concept_id=relation["target_formal_concept_id"],
            is_in_prerequisite_cycle=relation["is_in_prerequisite_cycle"],
            relation_evidence=tuple(
                RelationEvidenceContext(
                    owner_formal_concept_id=item["owner_formal_concept_id"],
                    claim_id=item["claim_id"],
                    evidence_ids=tuple(item["evidence_ids"]),
                )
                for item in relation["relation_evidence"]
            ),
        )
        for relation in knowledge_map["relations"]
    )
    return MapContext(
        learner_id=learner_id,
        material_id=material_id,
        knowledge_map_revision=knowledge_map_revision,
        formal_concepts=formal_concepts,
        relations=relations,
        initial_learning_path=tuple(knowledge_map["initial_learning_path"]),
    )


def _read_map_context(
    session: Session,
    learner_id: UUID,
    material_id: UUID,
    knowledge_map_revision: str,
) -> MapContext:
    if (
        not isinstance(learner_id, UUID)
        or not isinstance(material_id, UUID)
        or not isinstance(knowledge_map_revision, str)
    ):
        raise _unavailable()
    row = session.execute(
        select(KnowledgeMap.document, StudyMaterialOutput.document)
        .join(
            StudyMaterialOutput,
            (StudyMaterialOutput.learner_id == KnowledgeMap.learner_id)
            & (StudyMaterialOutput.material_id == KnowledgeMap.material_id)
            & (
                StudyMaterialOutput.output_revision
                == KnowledgeMap.source_output_revision
            ),
        )
        .where(
            KnowledgeMap.learner_id == learner_id,
            KnowledgeMap.material_id == material_id,
            KnowledgeMap.map_revision == knowledge_map_revision,
        )
    ).one_or_none()
    if row is None:
        raise _unavailable()
    return _build_context(
        learner_id,
        material_id,
        knowledge_map_revision,
        row[0],
        row[1],
    )


def read_map_context(
    learner_id: UUID,
    material_id: UUID,
    knowledge_map_revision: str,
    *,
    dsn: str | None = None,
) -> MapContext:
    """依 owner、教材與 exact revision 讀取可用的窄版 Knowledge Map。"""

    try:
        with database_session(dsn) as session:
            return _read_map_context(
                session, learner_id, material_id, knowledge_map_revision
            )
    except MapContextError:
        raise
    except (DatabaseConfigurationError, SQLAlchemyError, KeyError, TypeError):
        raise _unavailable() from None
