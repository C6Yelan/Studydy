from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
import os
import re
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from knowledge_map.artifacts import (
    build_knowledge_map_view,
    validate_knowledge_map,
)
from knowledge_map.local_generation import generate_knowledge_map
from pdf_evidence.artifact_reason_codes import formal_reason_codes, reason_codes_are_valid
from pdf_evidence.text_first_bundle import (
    remove_producer_bundle,
    validate_bundle_documents,
)
from pdf_evidence.ocr_page_evidence import canonical_bytes, canonical_sha256
from pdf_evidence.study_material_output import (
    build_study_material_output,
    validate_study_material_output,
)

from .artifacts import open_verified_source_pdf
from .tables import (
    KnowledgeMap,
    MaterialProcessingRun,
    StudyMaterialOutput,
    database_session,
)


class MaterialRunOutputError(RuntimeError):
    """Run output 無法安全保存或讀取。"""


@dataclass(frozen=True)
class MaterialRunOutputs:
    study_material_output_revision: str
    knowledge_map_revision: str
    study_material_output: dict[str, Any] = field(repr=False)
    knowledge_map: dict[str, Any] = field(repr=False)
    knowledge_map_view: dict[str, Any] = field(repr=False)


def _now() -> datetime:
    return datetime.now(UTC)


def _write_stage_failure(
    runtime_root: Path,
    run_id: UUID,
    reason_code: str,
) -> None:
    """只保存固定 stage/reason，不保存教材或模型 request/response。"""

    failures = runtime_root / "stage-failures"
    if runtime_root.is_symlink() or failures.is_symlink():
        raise OSError("STAGE_ARTIFACT_WRITE_FAILED")
    try:
        failures.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = failures / f"{run_id}.json"
        encoded = canonical_bytes({
            "schema": "material-stage-failure/v1",
            "run_id": str(run_id),
            "stage": "formal_knowledge",
            "reason_code": reason_code,
            "produced_at": _now().isoformat(),
        })
        with path.open("xb") as destination:
            os.chmod(path, 0o600)
            destination.write(encoded)
            destination.flush()
            os.fsync(destination.fileno())
    except OSError as error:
        raise OSError("STAGE_ARTIFACT_WRITE_FAILED") from error


def _binding_is_valid(binding: Any) -> bool:
    fields = {
        "schema", "producer_bundle_id", "producer_run_id", "concept_evidence_output_id",
        "study_material_output_revision", "knowledge_map_revision",
        "runtime_binding_sha256", "page_count", "processing", "quality", "decision",
        "reason_codes", "ocr_calls", "concept_calls",
    }
    if not isinstance(binding, dict) or set(binding) != fields:
        return False
    return (
        binding["schema"] == "material-run-output-binding/v3"
        and type(binding["page_count"]) is int
        and binding["page_count"] >= 1
        and binding["processing"] in {"succeeded", "partial"}
        and binding["quality"] == "needs_review"
        and binding["decision"] == "review"
        and reason_codes_are_valid(binding["reason_codes"], formal=True)
        and binding["reason_codes"] == sorted(set(binding["reason_codes"]))
        and type(binding["ocr_calls"]) is int
        and 0 <= binding["ocr_calls"] <= binding["page_count"]
        and type(binding["concept_calls"]) is int
        and binding["concept_calls"] >= 0
        and all(
            isinstance(binding[field], str) and binding[field]
            for field in fields
            - {"page_count", "reason_codes", "ocr_calls", "concept_calls"}
        )
        and re.fullmatch(r"text-first-producer-bundle:sha256:[0-9a-f]{64}", binding["producer_bundle_id"]) is not None
        and re.fullmatch(r"text-first-run:[0-9a-fA-F-]{36}", binding["producer_run_id"]) is not None
        and re.fullmatch(r"concept-evidence-output:sha256:[0-9a-f]{64}", binding["concept_evidence_output_id"]) is not None
        and re.fullmatch(r"study-material-output:sha256:[0-9a-f]{64}", binding["study_material_output_revision"]) is not None
        and re.fullmatch(r"knowledge-map:sha256:[0-9a-f]{64}", binding["knowledge_map_revision"]) is not None
        and re.fullmatch(r"[0-9a-f]{64}", binding["runtime_binding_sha256"]) is not None
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


def _validated_producer(producer_bundle: Any, run_id: UUID) -> tuple[dict, dict]:
    if not isinstance(producer_bundle, dict) or set(producer_bundle) != {
        "bundle",
        "output",
    }:
        raise MaterialRunOutputError("MATERIAL_OUTPUT_INVALID")
    bundle = producer_bundle["bundle"]
    output = producer_bundle["output"]
    expected_run_id = f"text-first-run:{run_id}"
    if (
        not validate_bundle_documents(bundle, output, expected_run_id)
        or output is None
        or bundle.get("processing") not in {"succeeded", "partial"}
    ):
        raise MaterialRunOutputError("MATERIAL_OUTPUT_INVALID")
    return bundle, output


def publish_material_outputs(
    learner_id: UUID,
    material_id: UUID,
    run_id: UUID,
    source_sha256: str,
    runtime_binding_sha256: str,
    producer_bundle: dict[str, Any],
    *,
    local_config: dict[str, Any],
    runtime_root: Path,
    dsn: str | None = None,
) -> MaterialRunOutputs:
    """在單一 transaction 內保存 Output、Map，清理 handoff 後才發布 terminal。"""

    bundle, producer_output = _validated_producer(producer_bundle, run_id)
    if (
        producer_output.get("material_id") != f"material:sha256:{source_sha256}"
        or producer_output.get("source_binding", {}).get("source_sha256") != source_sha256
        or bundle.get("runtime_binding_sha256") != runtime_binding_sha256
    ):
        raise MaterialRunOutputError("MATERIAL_OUTPUT_INVALID")
    try:
        study_material_output = build_study_material_output(producer_output)
    except (KeyError, TypeError, ValueError):
        raise MaterialRunOutputError("MATERIAL_OUTPUT_INVALID") from None
    try:
        knowledge_map = generate_knowledge_map(
            study_material_output,
            local_config,
            runtime_binding_sha256,
        )
        knowledge_map_view = build_knowledge_map_view(knowledge_map)
    except (KeyError, TypeError, ValueError):
        try:
            _write_stage_failure(runtime_root, run_id, "KNOWLEDGE_GENERATION_FAILED")
        except OSError:
            raise MaterialRunOutputError("STAGE_ARTIFACT_WRITE_FAILED") from None
        raise MaterialRunOutputError("KNOWLEDGE_GENERATION_FAILED") from None

    final_processing = (
        "partial"
        if bundle["processing"] == "partial"
        or knowledge_map["processing"] == "partial"
        else "succeeded"
    )
    binding = {
        "schema": "material-run-output-binding/v3",
        "producer_bundle_id": bundle["bundle_id"],
        "producer_run_id": bundle["run_id"],
        "concept_evidence_output_id": producer_output["output_id"],
        "study_material_output_revision": study_material_output["output_id"],
        "knowledge_map_revision": knowledge_map["revision"],
        "runtime_binding_sha256": runtime_binding_sha256,
        "page_count": bundle["page_count"],
        "processing": final_processing,
        "quality": bundle["quality"],
        "decision": bundle["decision"],
        "reason_codes": formal_reason_codes(
            bundle["reason_codes"]
            + (["NO_FORMAL_CONCEPT"] if knowledge_map["decision"] == "reject" else [])
        ),
        "ocr_calls": bundle["ocr_calls"],
        "concept_calls": bundle["concept_calls"],
    }
    if not _binding_is_valid(binding):
        raise MaterialRunOutputError("MATERIAL_OUTPUT_INVALID")
    try:
        with database_session(dsn) as session:
            run_row = session.execute(
                select(
                    MaterialProcessingRun.source_artifact_id,
                    MaterialProcessingRun.runtime_binding,
                ).where(
                    MaterialProcessingRun.learner_id == learner_id,
                    MaterialProcessingRun.material_id == material_id,
                    MaterialProcessingRun.run_id == run_id,
                    MaterialProcessingRun.status == "running",
                )
            ).one_or_none()
            if run_row is None or not isinstance(run_row[1], dict):
                raise MaterialRunOutputError("MATERIAL_RUN_UNAVAILABLE")
            run_runtime = run_row[1]
            if (
                run_runtime.get("runtime_binding_sha256") != runtime_binding_sha256
                or run_runtime.get("runtime_lock_sha256")
                != canonical_sha256(producer_output["runtime_binding"])
            ):
                raise MaterialRunOutputError("MATERIAL_OUTPUT_INVALID")
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
                    StudyMaterialOutput.output_revision == study_material_output["output_id"],
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
            remove_producer_bundle(runtime_root, bundle["run_id"])
            status = final_processing
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
    except (MaterialRunOutputError, OSError):
        raise
    except Exception:
        raise MaterialRunOutputError("MATERIAL_OUTPUT_STORE_FAILED") from None
    return MaterialRunOutputs(
        study_material_output["output_id"],
        knowledge_map["revision"],
        deepcopy(study_material_output),
        deepcopy(knowledge_map),
        knowledge_map_view,
    )


def read_material_run_outputs(
    learner_id: UUID,
    material_id: UUID,
    run_id: UUID,
    *,
    dsn: str | None = None,
) -> MaterialRunOutputs:
    """依 owner/run binding 重驗 Study Material 與 Knowledge Map。"""

    if not all(isinstance(value, UUID) for value in (learner_id, material_id, run_id)):
        raise MaterialRunOutputError("MATERIAL_OUTPUT_UNAVAILABLE")
    try:
        with database_session(dsn) as session:
            run = session.execute(
                select(
                    MaterialProcessingRun.source_artifact_id,
                    MaterialProcessingRun.runtime_binding,
                    MaterialProcessingRun.status,
                    MaterialProcessingRun.output_binding,
                ).where(
                    MaterialProcessingRun.learner_id == learner_id,
                    MaterialProcessingRun.material_id == material_id,
                    MaterialProcessingRun.run_id == run_id,
                    MaterialProcessingRun.status.in_(("succeeded", "partial")),
                )
            ).one_or_none()
            if run is None or not isinstance(run[1], dict) or not isinstance(run[3], dict):
                raise MaterialRunOutputError("MATERIAL_OUTPUT_UNAVAILABLE")
            source_artifact_id, run_runtime, run_status, binding = run
            if not _binding_is_valid(binding):
                raise MaterialRunOutputError("MATERIAL_OUTPUT_UNAVAILABLE")
            output_row = session.execute(
                select(StudyMaterialOutput.document).where(
                    StudyMaterialOutput.learner_id == learner_id,
                    StudyMaterialOutput.material_id == material_id,
                    StudyMaterialOutput.output_revision
                    == binding.get("study_material_output_revision"),
                )
            ).one_or_none()
            map_row = session.execute(
                select(KnowledgeMap.source_output_revision, KnowledgeMap.document).where(
                    KnowledgeMap.learner_id == learner_id,
                    KnowledgeMap.material_id == material_id,
                    KnowledgeMap.map_revision == binding.get("knowledge_map_revision"),
                )
            ).one_or_none()
        if output_row is None or map_row is None:
            raise MaterialRunOutputError("MATERIAL_OUTPUT_UNAVAILABLE")
        study_material_output = output_row[0]
        knowledge_map = map_row[1]
        source_concepts = {
            concept["concept_id"]: concept
            for concept in study_material_output.get("concepts", [])
            if isinstance(concept, dict) and isinstance(concept.get("concept_id"), str)
        }
        formal_claims_match_source = all(
            any(
                claim == source_concept["definition"]
                or claim in source_concept["key_points"]
                for source_id in formal_concept.get("source_concept_ids", [])
                if (source_concept := source_concepts.get(source_id)) is not None
            )
            for formal_concept in knowledge_map.get("formal_concepts", [])
            for claim in formal_concept.get("claims", [])
        )
        if (
            map_row[0] != study_material_output.get("output_id")
            or binding["study_material_output_revision"] != study_material_output.get("output_id")
            or binding["knowledge_map_revision"] != knowledge_map.get("revision")
            or knowledge_map.get("source_binding", {}).get("study_material_output_id")
            != study_material_output.get("output_id")
            or knowledge_map.get("source_binding", {}).get("producer_output_id")
            != study_material_output.get("source_binding", {}).get("producer_output_id")
            or knowledge_map.get("source_binding", {}).get("producer_runtime_lock_sha256")
            != study_material_output.get("source_binding", {}).get("runtime_binding_sha256")
            or knowledge_map.get("source_binding", {}).get("material_runtime_binding_sha256")
            != binding["runtime_binding_sha256"]
            or knowledge_map.get("material_ref") != study_material_output.get("material_ref")
            or knowledge_map.get("evidence_index")
            != study_material_output.get("evidence_index")
            or knowledge_map.get("excluded_pages")
            != study_material_output.get("excluded_pages")
            or any(
                source_id not in source_concepts
                for formal_concept in knowledge_map.get("formal_concepts", [])
                for source_id in formal_concept.get("source_concept_ids", [])
            )
            or not formal_claims_match_source
            or binding["concept_evidence_output_id"]
            != study_material_output.get("source_binding", {}).get("producer_output_id")
            or binding["runtime_binding_sha256"]
            != run_runtime.get("runtime_binding_sha256")
            or run_runtime.get("runtime_lock_sha256")
            != study_material_output.get("source_binding", {}).get("runtime_binding_sha256")
            or binding["page_count"]
            != study_material_output.get("source_binding", {}).get("page_count")
            or binding["processing"] != run_status
            or binding["processing"]
            != (
                "partial"
                if study_material_output.get("processing") == "partial"
                or knowledge_map.get("processing") == "partial"
                else "succeeded"
            )
            or study_material_output.get("run_id") != binding["producer_run_id"]
            or validate_study_material_output(study_material_output) is not None
            or validate_knowledge_map(knowledge_map) is not None
        ):
            raise MaterialRunOutputError("MATERIAL_OUTPUT_UNAVAILABLE")
        with open_verified_source_pdf(learner_id, source_artifact_id, dsn=dsn) as source:
            if (
                source.material_id != material_id
                or source.sha256
                != study_material_output.get("source_binding", {}).get("source_sha256")
                or study_material_output.get("material_ref")
                != f"material:sha256:{source.sha256}"
            ):
                raise MaterialRunOutputError("MATERIAL_OUTPUT_UNAVAILABLE")
        return MaterialRunOutputs(
            study_material_output["output_id"],
            knowledge_map["revision"],
            deepcopy(study_material_output),
            deepcopy(knowledge_map),
            build_knowledge_map_view(knowledge_map),
        )
    except MaterialRunOutputError:
        raise
    except Exception:
        raise MaterialRunOutputError("MATERIAL_OUTPUT_UNAVAILABLE") from None
