from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
import os
from pathlib import Path
from typing import Any, Sequence
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from knowledge_map.artifacts import build_artifacts, build_knowledge_map_view
from learning_resources.catalog import build_controlled_resource_catalog
from learning_resources.matching import build_learning_resource_result, validate_learning_resource_result
from learning_state.assessment import build_evidence_grounded_assessment, canonical_sha256
from learning_state.learning_state import validate_initial_learning_path

from .artifacts import ARTIFACT_ROOT_ENV, open_verified_resource_pdf, open_verified_source_pdf
from .tables import (
    Assessment,
    KnowledgeMap,
    LearningPath,
    LearningResourceResult,
    MaterialProcessingRun,
    ResourceCatalog,
    StudyMaterialOutput,
    database_session,
)


class MaterialRunOutputError(RuntimeError):
    """Run output 無法安全保存或讀取。"""


@dataclass(frozen=True)
class MaterialRunOutputs:
    study_material_output_revision: str
    catalog_revision: str
    learning_resource_result_revision: str
    knowledge_map_revision: str
    learning_path_revision: str
    assessment_revision: str
    study_material_output: dict[str, Any] = field(repr=False)
    resource_catalog: dict[str, Any] = field(repr=False)
    learning_resource_result: dict[str, Any] = field(repr=False)
    knowledge_map: dict[str, Any] = field(repr=False)
    learning_path: dict[str, Any] = field(repr=False)
    knowledge_map_view: dict[str, Any] = field(repr=False)
    assessment_view: dict[str, Any] = field(repr=False)


_SAFE_STATUS_KEYS = {"processing", "quality", "decision", "reason_code", "provider_call_counts"}
_SAFE_OUTCOMES = {
    ("succeeded", "accepted", "retain", "DEVELOPMENT_OUTPUT_ACCEPTED"),
    ("succeeded", "needs_review", "review", "DEVELOPMENT_OUTPUT_NEEDS_REVIEW"),
    ("partial", "needs_review", "review", "DEVELOPMENT_FULL_DOCUMENT_PARTIAL"),
}
_COUNT_KEYS = {"page_structure", "visual_alignment_adjudication", "concept_candidate", "concept_content", "total"}


def _now() -> datetime:
    return datetime.now(UTC)


def _valid_status(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != _SAFE_STATUS_KEYS:
        return False
    if not all(isinstance(value[key], str) for key in ("processing", "quality", "decision", "reason_code")):
        return False
    counts = value["provider_call_counts"]
    return (
        tuple(value[key] for key in ("processing", "quality", "decision", "reason_code")) in _SAFE_OUTCOMES
        and isinstance(counts, dict)
        and set(counts) == _COUNT_KEYS
        and all(type(item) is int and item >= 0 for item in counts.values())
        and counts["total"] == sum(counts[key] for key in _COUNT_KEYS - {"total"})
    )


def _artifact_root() -> Path:
    raw = os.environ.get(ARTIFACT_ROOT_ENV)
    if not raw:
        raise MaterialRunOutputError("MATERIAL_OUTPUT_INVALID")
    return Path(raw)


def _assessment_documents(assessment: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    questions = []
    keys = []
    for question in assessment["questions"]:
        questions.append({key: deepcopy(question[key]) for key in (
            "question_id", "concept_id", "question_type", "prompt", "options", "source_evidence_ids"
        )})
        keys.append({"question_id": question["question_id"], "answer_key_option_id": question["answer_key_option_id"]})
    content = {
        "schema": "assessment-view/v1",
        "version": assessment["version"],
        "knowledge_map_revision": assessment["knowledge_map_revision"],
        "learning_path_revision": assessment["learning_path_revision"],
        "scoring_rule_version": assessment["scoring_rule_version"],
        "questions": questions,
        "practice_sets": deepcopy(assessment["practice_sets"]),
        "processing": assessment["processing"],
        "quality": assessment["quality"],
        "decision": assessment["decision"],
        "reason_code": assessment["reason_code"],
    }
    digest = canonical_sha256(content)
    if digest is None:
        raise MaterialRunOutputError("MATERIAL_OUTPUT_INVALID")
    return (
        {"assessment_view_id": f"assessment-view:sha256:{digest}", **content},
        {
            "schema": "assessment-answer-key/v1",
            "assessment_id": assessment["assessment_id"],
            "answer_keys": sorted(keys, key=lambda item: item["question_id"]),
        },
    )


def _insert_immutable(
    session: Session,
    model: type[Any],
    values: dict[str, Any],
    read_statement: object,
    expected: tuple[Any, ...],
) -> None:
    session.execute(pg_insert(model).values(**values).on_conflict_do_nothing())
    stored = session.execute(read_statement).one_or_none()
    if stored is None or tuple(stored) != expected:
        raise MaterialRunOutputError("MATERIAL_OUTPUT_FAILED")


def store_resource_catalog(
    learner_id: UUID,
    material_id: UUID,
    run_id: UUID,
    subject: str,
    candidates: Sequence[dict[str, Any]],
    *,
    dsn: str | None = None,
) -> dict[str, Any]:
    """保存已發布 resources 的 exact catalog，並讓 run 可被 worker claim。"""
    catalog = build_controlled_resource_catalog(list(candidates), _artifact_root())
    if catalog.get("processing") == "failed":
        raise MaterialRunOutputError("CONTROLLED_RESOURCE_INVALID")
    try:
        with database_session(dsn) as session:
            session.execute(
                pg_insert(ResourceCatalog)
                .values(
                    learner_id=learner_id,
                    material_id=material_id,
                    catalog_revision=catalog["catalog_revision"],
                    subject=subject,
                    document=catalog,
                    created_at=func.clock_timestamp(),
                )
                .on_conflict_do_nothing()
            )
            stored = session.execute(
                select(ResourceCatalog.subject, ResourceCatalog.document).where(
                    ResourceCatalog.learner_id == learner_id,
                    ResourceCatalog.material_id == material_id,
                    ResourceCatalog.catalog_revision == catalog["catalog_revision"],
                )
            ).one_or_none()
            if stored is None or tuple(stored) != (subject, catalog):
                raise MaterialRunOutputError("MATERIAL_OUTPUT_CONFLICT")
            updated = session.execute(
                update(MaterialProcessingRun)
                .where(
                    MaterialProcessingRun.learner_id == learner_id,
                    MaterialProcessingRun.material_id == material_id,
                    MaterialProcessingRun.run_id == run_id,
                    MaterialProcessingRun.status == "running",
                )
                .values(
                    catalog_revision=catalog["catalog_revision"],
                    status="pending",
                    updated_at=func.clock_timestamp(),
                )
                .returning(MaterialProcessingRun.run_id)
            ).scalar_one_or_none()
            if updated is None:
                raise MaterialRunOutputError("MATERIAL_RUN_UNAVAILABLE")
    except MaterialRunOutputError:
        raise
    except Exception:
        raise MaterialRunOutputError("MATERIAL_OUTPUT_STORE_FAILED") from None
    return catalog


def publish_terminal_outputs(
    learner_id: UUID,
    material_id: UUID,
    run_id: UUID,
    subject: str,
    catalog: dict[str, Any],
    study_material_output: dict[str, Any],
    safe_status: dict[str, Any],
    *,
    dsn: str | None = None,
) -> MaterialRunOutputs:
    """建立所有 domain documents，並以單一 transaction 發布 terminal binding。"""
    root = _artifact_root()
    if not _valid_status(safe_status):
        raise MaterialRunOutputError("MATERIAL_OUTPUT_INVALID")
    resource_result = build_learning_resource_result(
        study_material_output,
        catalog,
        root,
        subject,
        None,
        produced_at=_now().isoformat(),
        run_id=str(run_id),
    )
    if resource_result.get("processing") != "succeeded":
        raise MaterialRunOutputError("MATERIAL_OUTPUT_INVALID")
    knowledge_map, learning_path, view = build_artifacts(study_material_output)
    assessment = build_evidence_grounded_assessment(knowledge_map, learning_path["revision"])
    if validate_initial_learning_path(learning_path, knowledge_map) is not None:
        raise MaterialRunOutputError("MATERIAL_OUTPUT_INVALID")
    public_assessment, answer_key = _assessment_documents(assessment)
    binding = {
        "schema": "material-run-output-binding/v1",
        "study_material_output_revision": study_material_output["output_id"],
        "catalog_revision": catalog["catalog_revision"],
        "learning_resource_result_revision": resource_result["result_revision"],
        "knowledge_map_revision": knowledge_map["revision"],
        "learning_path_revision": learning_path["revision"],
        "assessment_revision": assessment["revision"],
        **deepcopy(safe_status),
        "development_only": True,
    }
    try:
        with database_session(dsn) as session:
            created = _now()
            _insert_immutable(
                session,
                StudyMaterialOutput,
                {
                    "learner_id": learner_id,
                    "material_id": material_id,
                    "output_revision": study_material_output["output_id"],
                    "document": study_material_output,
                    "created_at": created,
                },
                select(StudyMaterialOutput.document).where(
                    StudyMaterialOutput.learner_id == learner_id,
                    StudyMaterialOutput.material_id == material_id,
                    StudyMaterialOutput.output_revision
                    == study_material_output["output_id"],
                ),
                (study_material_output,),
            )
            _insert_immutable(
                session,
                KnowledgeMap,
                {
                    "learner_id": learner_id,
                    "material_id": material_id,
                    "map_revision": knowledge_map["revision"],
                    "source_output_revision": study_material_output["output_id"],
                    "document": knowledge_map,
                    "created_at": created,
                },
                select(
                    KnowledgeMap.source_output_revision, KnowledgeMap.document
                ).where(
                    KnowledgeMap.learner_id == learner_id,
                    KnowledgeMap.material_id == material_id,
                    KnowledgeMap.map_revision == knowledge_map["revision"],
                ),
                (study_material_output["output_id"], knowledge_map),
            )
            _insert_immutable(
                session,
                LearningPath,
                {
                    "learner_id": learner_id,
                    "material_id": material_id,
                    "path_revision": learning_path["revision"],
                    "map_revision": knowledge_map["revision"],
                    "document": learning_path,
                    "created_at": created,
                },
                select(LearningPath.map_revision, LearningPath.document).where(
                    LearningPath.learner_id == learner_id,
                    LearningPath.material_id == material_id,
                    LearningPath.path_revision == learning_path["revision"],
                ),
                (knowledge_map["revision"], learning_path),
            )
            _insert_immutable(
                session,
                LearningResourceResult,
                {
                    "learner_id": learner_id,
                    "material_id": material_id,
                    "result_revision": resource_result["result_revision"],
                    "source_output_revision": study_material_output["output_id"],
                    "catalog_revision": catalog["catalog_revision"],
                    "document": resource_result,
                    "created_at": created,
                },
                select(
                    LearningResourceResult.source_output_revision,
                    LearningResourceResult.catalog_revision,
                    LearningResourceResult.document,
                ).where(
                    LearningResourceResult.learner_id == learner_id,
                    LearningResourceResult.material_id == material_id,
                    LearningResourceResult.result_revision
                    == resource_result["result_revision"],
                ),
                (
                    study_material_output["output_id"],
                    catalog["catalog_revision"],
                    resource_result,
                ),
            )
            _insert_immutable(
                session,
                Assessment,
                {
                    "learner_id": learner_id,
                    "material_id": material_id,
                    "assessment_revision": assessment["revision"],
                    "output_revision": study_material_output["output_id"],
                    "map_revision": knowledge_map["revision"],
                    "path_revision": learning_path["revision"],
                    "public_document": public_assessment,
                    "answer_key_document": answer_key,
                    "created_at": created,
                },
                select(
                    Assessment.output_revision,
                    Assessment.map_revision,
                    Assessment.path_revision,
                    Assessment.public_document,
                    Assessment.answer_key_document,
                ).where(
                    Assessment.learner_id == learner_id,
                    Assessment.material_id == material_id,
                    Assessment.assessment_revision == assessment["revision"],
                ),
                (
                    study_material_output["output_id"],
                    knowledge_map["revision"],
                    learning_path["revision"],
                    public_assessment,
                    answer_key,
                ),
            )
            status = "partial" if safe_status["processing"] == "partial" else "succeeded"
            updated = session.execute(
                update(MaterialProcessingRun)
                .where(
                    MaterialProcessingRun.learner_id == learner_id,
                    MaterialProcessingRun.material_id == material_id,
                    MaterialProcessingRun.run_id == run_id,
                    MaterialProcessingRun.status == "running",
                )
                .values(
                    status=status,
                    output_binding=binding,
                    completed_at=func.clock_timestamp(),
                    updated_at=func.clock_timestamp(),
                )
                .returning(MaterialProcessingRun.run_id)
            ).scalar_one_or_none()
            if updated is None:
                raise MaterialRunOutputError("MATERIAL_RUN_UNAVAILABLE")
    except MaterialRunOutputError:
        raise
    except Exception:
        raise MaterialRunOutputError("MATERIAL_OUTPUT_STORE_FAILED") from None
    return _outputs(binding, study_material_output, catalog, resource_result, knowledge_map, learning_path, view, public_assessment)


def _outputs(
    binding: dict[str, Any],
    output: dict[str, Any],
    catalog: dict[str, Any],
    resource_result: dict[str, Any],
    knowledge_map: dict[str, Any],
    learning_path: dict[str, Any],
    view: dict[str, Any],
    assessment_view: dict[str, Any],
) -> MaterialRunOutputs:
    return MaterialRunOutputs(
        binding["study_material_output_revision"],
        binding["catalog_revision"],
        binding["learning_resource_result_revision"],
        binding["knowledge_map_revision"],
        binding["learning_path_revision"],
        binding["assessment_revision"],
        deepcopy(output),
        deepcopy(catalog),
        deepcopy(resource_result),
        deepcopy(knowledge_map),
        deepcopy(learning_path),
        deepcopy(view),
        deepcopy(assessment_view),
    )


def read_material_run_outputs(
    learner_id: UUID,
    material_id: UUID,
    run_id: UUID,
    *,
    dsn: str | None = None,
) -> MaterialRunOutputs:
    """以 owner/material/run binding 讀取並重驗所有 immutable outputs。"""
    if not all(isinstance(value, UUID) for value in (learner_id, material_id, run_id)):
        raise MaterialRunOutputError("MATERIAL_OUTPUT_UNAVAILABLE")
    try:
        with database_session(dsn) as session:
            run = session.execute(
                select(
                    MaterialProcessingRun.source_artifact_id,
                    MaterialProcessingRun.subject,
                    MaterialProcessingRun.catalog_revision,
                    MaterialProcessingRun.output_binding,
                ).where(
                    MaterialProcessingRun.learner_id == learner_id,
                    MaterialProcessingRun.material_id == material_id,
                    MaterialProcessingRun.run_id == run_id,
                    MaterialProcessingRun.status.in_(("succeeded", "partial")),
                )
            ).one_or_none()
            if run is None or not isinstance(run[3], dict):
                raise MaterialRunOutputError("MATERIAL_OUTPUT_UNAVAILABLE")
            source_artifact_id, subject, catalog_revision, binding = run
            output_row = session.execute(
                select(StudyMaterialOutput.document).where(
                    StudyMaterialOutput.learner_id == learner_id,
                    StudyMaterialOutput.material_id == material_id,
                    StudyMaterialOutput.output_revision
                    == binding.get("study_material_output_revision"),
                )
            ).one_or_none()
            catalog_row = session.execute(
                select(ResourceCatalog.subject, ResourceCatalog.document).where(
                    ResourceCatalog.learner_id == learner_id,
                    ResourceCatalog.material_id == material_id,
                    ResourceCatalog.catalog_revision == catalog_revision,
                )
            ).one_or_none()
            resource_row = session.execute(
                select(LearningResourceResult.document).where(
                    LearningResourceResult.learner_id == learner_id,
                    LearningResourceResult.material_id == material_id,
                    LearningResourceResult.result_revision
                    == binding.get("learning_resource_result_revision"),
                )
            ).one_or_none()
            map_row = session.execute(
                select(KnowledgeMap.document).where(
                    KnowledgeMap.learner_id == learner_id,
                    KnowledgeMap.material_id == material_id,
                    KnowledgeMap.map_revision
                    == binding.get("knowledge_map_revision"),
                )
            ).one_or_none()
            path_row = session.execute(
                select(LearningPath.document).where(
                    LearningPath.learner_id == learner_id,
                    LearningPath.material_id == material_id,
                    LearningPath.path_revision
                    == binding.get("learning_path_revision"),
                )
            ).one_or_none()
            assessment_row = session.execute(
                select(Assessment.public_document).where(
                    Assessment.learner_id == learner_id,
                    Assessment.material_id == material_id,
                    Assessment.assessment_revision
                    == binding.get("assessment_revision"),
                )
            ).one_or_none()
        if None in (output_row, catalog_row, resource_row, map_row, path_row, assessment_row):
            raise MaterialRunOutputError("MATERIAL_OUTPUT_UNAVAILABLE")
        output, catalog, resource_result = output_row[0], catalog_row[1], resource_row[0]
        knowledge_map, learning_path, assessment_view = map_row[0], path_row[0], assessment_row[0]
        view = build_knowledge_map_view(knowledge_map, learning_path)
        if (
            catalog_row[0] != subject
            or binding.get("catalog_revision") != catalog_revision
            or validate_learning_resource_result(resource_result, output, catalog, _artifact_root(), None) is not None
            or validate_initial_learning_path(learning_path, knowledge_map) is not None
        ):
            raise MaterialRunOutputError("MATERIAL_OUTPUT_UNAVAILABLE")
        with open_verified_source_pdf(learner_id, source_artifact_id, dsn=dsn):
            pass
        for resource in catalog["resources"]:
            artifact_id = UUID(resource["artifact_ref"].rsplit("/", 1)[-1])
            with open_verified_resource_pdf(learner_id, material_id, artifact_id, dsn=dsn):
                pass
        return _outputs(binding, output, catalog, resource_result, knowledge_map, learning_path, view, assessment_view)
    except MaterialRunOutputError:
        raise
    except Exception:
        raise MaterialRunOutputError("MATERIAL_OUTPUT_UNAVAILABLE") from None
