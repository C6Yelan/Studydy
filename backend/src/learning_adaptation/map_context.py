from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from knowledge_map.artifacts import validate_knowledge_map
from runtime.storage.database import DatabaseConfigurationError
from runtime.storage.tables import (
    KnowledgeMap,
    MaterialProcessingRun,
    StudyMaterialOutput,
    database_session,
)


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
class PrerequisiteConstraintContext:
    prerequisite_constraint_id: str
    source_formal_concept_id: str
    target_formal_concept_id: str


@dataclass(frozen=True)
class MapContext:
    learner_id: UUID
    material_id: UUID
    knowledge_map_revision: str
    formal_concepts: tuple[FormalConceptContext, ...]
    prerequisite_constraints: tuple[PrerequisiteConstraintContext, ...]
    initial_learning_path: tuple[str, ...]


def _unavailable() -> MapContextError:
    return MapContextError("KNOWLEDGE_MAP_UNAVAILABLE")


def _build_context(
    learner_id: UUID,
    material_id: UUID,
    knowledge_map_revision: str,
    knowledge_map: object,
    study_material_output: object,
    material_runtime_binding_sha256: object,
) -> MapContext:
    if (
        validate_knowledge_map(knowledge_map, study_material_output) is not None
    ):
        raise _unavailable()
    assert isinstance(knowledge_map, dict)
    assert isinstance(study_material_output, dict)
    map_source = knowledge_map["source_binding"]
    material_source = study_material_output["source_binding"]
    if (
        knowledge_map["revision"] != knowledge_map_revision
        or knowledge_map["decision"] == "reject"
        or not knowledge_map["formal_concepts"]
        or knowledge_map["source_output_id"] != study_material_output["output_id"]
        or map_source["producer_output_id"]
        != material_source["producer_output_id"]
        or map_source["producer_runtime_lock_sha256"]
        != material_source["runtime_binding_sha256"]
        or map_source["material_runtime_binding_sha256"]
        != material_runtime_binding_sha256
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
    return MapContext(
        learner_id=learner_id,
        material_id=material_id,
        knowledge_map_revision=knowledge_map_revision,
        formal_concepts=formal_concepts,
        prerequisite_constraints=tuple(
            PrerequisiteConstraintContext(
                prerequisite_constraint_id=constraint[
                    "prerequisite_constraint_id"
                ],
                source_formal_concept_id=constraint[
                    "source_formal_concept_id"
                ],
                target_formal_concept_id=constraint[
                    "target_formal_concept_id"
                ],
            )
            for constraint in knowledge_map["prerequisite_constraints"]
        ),
        initial_learning_path=tuple(
            step["formal_concept_id"]
            for step in knowledge_map["initial_learning_path"]
        ),
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
    study_material_output = row[1]
    if not isinstance(study_material_output, dict):
        raise _unavailable()
    producer_run_id = study_material_output.get("run_id")
    if not isinstance(producer_run_id, str) or not producer_run_id.startswith(
        "text-first-run:"
    ):
        raise _unavailable()
    try:
        run_id = UUID(producer_run_id.removeprefix("text-first-run:"))
    except ValueError:
        raise _unavailable() from None
    run = session.execute(
        select(
            MaterialProcessingRun.runtime_binding,
            MaterialProcessingRun.output_binding,
        ).where(
            MaterialProcessingRun.learner_id == learner_id,
            MaterialProcessingRun.material_id == material_id,
            MaterialProcessingRun.run_id == run_id,
            MaterialProcessingRun.status.in_(("succeeded", "partial")),
        )
    ).one_or_none()
    if run is None or not isinstance(run[0], dict) or not isinstance(run[1], dict):
        raise _unavailable()
    material_runtime = run[0].get("runtime_binding_sha256")
    if (
        run[1].get("study_material_output_revision")
        != study_material_output.get("output_id")
        or run[1].get("knowledge_map_revision") != knowledge_map_revision
        or run[1].get("concept_evidence_output_id")
        != study_material_output.get("source_binding", {}).get(
            "producer_output_id"
        )
        or run[1].get("runtime_binding_sha256") != material_runtime
        or run[0].get("runtime_lock_sha256")
        != study_material_output.get("source_binding", {}).get(
            "runtime_binding_sha256"
        )
    ):
        raise _unavailable()
    return _build_context(
        learner_id,
        material_id,
        knowledge_map_revision,
        row[0],
        study_material_output,
        material_runtime,
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
