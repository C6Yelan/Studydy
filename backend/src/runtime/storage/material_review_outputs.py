"""原子保存文字優先 runtime 的複核 Output、Map 與 run binding。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
import re
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from knowledge_map.artifacts import (
    build_knowledge_map_view,
    build_review_knowledge_map,
    validate_knowledge_map,
)
from pdf_evidence.concept_evidence_output import BUNDLE_SCHEMA, OUTPUT_SCHEMA, TERMINAL_SCHEMA
from pdf_evidence.text_first_bundle import validate_bundle_documents
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
        binding["schema"] == "material-run-output-binding/v2"
        and type(binding["page_count"]) is int
        and 1 <= binding["page_count"] <= 32
        and binding["processing"] in {"succeeded", "partial"}
        and binding["quality"] == "needs_review"
        and binding["decision"] == "review"
        and isinstance(binding["reason_codes"], list)
        and all(isinstance(reason, str) and reason for reason in binding["reason_codes"])
        and binding["reason_codes"] == sorted(set(binding["reason_codes"]))
        and type(binding["ocr_calls"]) is int
        and 0 <= binding["ocr_calls"] <= binding["page_count"]
        and type(binding["concept_calls"]) is int
        and 0 <= binding["concept_calls"] <= 2 * binding["page_count"]
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


def _validated_producer(producer_bundle: Any, run_id: UUID) -> tuple[dict, dict, dict]:
    if not isinstance(producer_bundle, dict) or set(producer_bundle) != {
        "bundle",
        "terminal",
        "output",
    }:
        raise MaterialRunOutputError("MATERIAL_OUTPUT_INVALID")
    bundle = producer_bundle["bundle"]
    terminal = producer_bundle["terminal"]
    output = producer_bundle["output"]
    expected_run_id = f"text-first-run:{run_id}"
    if not validate_bundle_documents(bundle, terminal, output, expected_run_id):
        raise MaterialRunOutputError("MATERIAL_OUTPUT_INVALID")
    terminal_fields = {
        "schema", "aggregation_policy", "run_id", "produced_at", "output_id",
        "runtime_binding_sha256", "page_count", "included_page_count",
        "excluded_page_count", "processing", "quality", "decision", "reason_codes",
        "duration_ms", "ocr_calls", "concept_calls", "ocr_loads", "concept_loads",
    }
    output_fields = {
        "schema", "aggregation_policy", "run_id", "produced_at", "material_id",
        "material_revision", "source_binding", "pages", "excluded_pages", "concepts",
        "rejected_candidates", "runtime_binding", "processing", "quality", "decision",
        "reason_codes", "output_id",
    }
    if (
        not isinstance(bundle, dict)
        or bundle.get("schema") != BUNDLE_SCHEMA
        or bundle.get("run_id") != expected_run_id
        or not isinstance(terminal, dict)
        or set(terminal) != terminal_fields
        or terminal.get("schema") != TERMINAL_SCHEMA
        or terminal.get("run_id") != expected_run_id
        or terminal.get("processing") not in {"succeeded", "partial"}
        or terminal.get("quality") != "needs_review"
        or terminal.get("decision") != "review"
        or type(terminal.get("page_count")) is not int
        or not 1 <= terminal["page_count"] <= 32
        or type(terminal.get("included_page_count")) is not int
        or type(terminal.get("excluded_page_count")) is not int
        or terminal["included_page_count"] + terminal["excluded_page_count"]
        != terminal["page_count"]
        or type(terminal.get("ocr_calls")) is not int
        or not 0 <= terminal["ocr_calls"] <= terminal["page_count"]
        or type(terminal.get("concept_calls")) is not int
        or not 0 <= terminal["concept_calls"] <= 2 * terminal["page_count"]
        or type(terminal.get("ocr_loads")) is not int
        or terminal["ocr_loads"] not in {0, 1}
        or type(terminal.get("concept_loads")) is not int
        or not 0 <= terminal["concept_loads"] <= terminal["page_count"] + 1
        or not isinstance(output, dict)
        or set(output) != output_fields
        or output.get("schema") != OUTPUT_SCHEMA
        or output.get("run_id") != expected_run_id
        or not isinstance(output.get("pages"), list)
        or len(output["pages"]) != terminal["included_page_count"]
        or not isinstance(output.get("excluded_pages"), list)
        or len(output["excluded_pages"]) != terminal["excluded_page_count"]
        or terminal.get("output_id") != output.get("output_id")
        or terminal.get("processing") != output.get("processing")
    ):
        raise MaterialRunOutputError("MATERIAL_OUTPUT_INVALID")
    return bundle, terminal, output


def publish_terminal_outputs(
    learner_id: UUID,
    material_id: UUID,
    run_id: UUID,
    source_sha256: str,
    runtime_binding_sha256: str,
    producer_bundle: dict[str, Any],
    *,
    dsn: str | None = None,
) -> MaterialRunOutputs:
    """先重驗 producer，再以單一 DB transaction 發布 Output、Map 與 binding。"""

    bundle, terminal, producer_output = _validated_producer(producer_bundle, run_id)
    if (
        producer_output.get("material_id") != f"material:sha256:{source_sha256}"
        or terminal.get("runtime_binding_sha256") != runtime_binding_sha256
    ):
        raise MaterialRunOutputError("MATERIAL_OUTPUT_INVALID")
    try:
        study_material_output = build_study_material_output(producer_output)
        knowledge_map = build_review_knowledge_map(study_material_output)
        knowledge_map_view = build_knowledge_map_view(knowledge_map)
    except (KeyError, TypeError, ValueError):
        raise MaterialRunOutputError("MATERIAL_OUTPUT_INVALID") from None

    binding = {
        "schema": "material-run-output-binding/v2",
        "producer_bundle_id": bundle["bundle_id"],
        "producer_run_id": bundle["run_id"],
        "concept_evidence_output_id": producer_output["output_id"],
        "study_material_output_revision": study_material_output["output_id"],
        "knowledge_map_revision": knowledge_map["revision"],
        "runtime_binding_sha256": runtime_binding_sha256,
        "page_count": terminal["page_count"],
        "processing": terminal["processing"],
        "quality": terminal["quality"],
        "decision": terminal["decision"],
        "reason_codes": deepcopy(terminal["reason_codes"]),
        "ocr_calls": terminal["ocr_calls"],
        "concept_calls": terminal["concept_calls"],
    }
    if not _binding_is_valid(binding):
        raise MaterialRunOutputError("MATERIAL_OUTPUT_INVALID")
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
            status = "partial" if terminal["processing"] == "partial" else "succeeded"
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
    """依 owner/run binding 重驗 Output v3 與 review-only Map v2。"""

    if not all(isinstance(value, UUID) for value in (learner_id, material_id, run_id)):
        raise MaterialRunOutputError("MATERIAL_OUTPUT_UNAVAILABLE")
    try:
        with database_session(dsn) as session:
            run = session.execute(
                select(
                    MaterialProcessingRun.source_artifact_id,
                    MaterialProcessingRun.output_binding,
                ).where(
                    MaterialProcessingRun.learner_id == learner_id,
                    MaterialProcessingRun.material_id == material_id,
                    MaterialProcessingRun.run_id == run_id,
                    MaterialProcessingRun.status.in_(("succeeded", "partial")),
                )
            ).one_or_none()
            if run is None or not isinstance(run[1], dict):
                raise MaterialRunOutputError("MATERIAL_OUTPUT_UNAVAILABLE")
            source_artifact_id, binding = run
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
        if (
            map_row[0] != study_material_output.get("output_id")
            or binding["study_material_output_revision"] != study_material_output.get("output_id")
            or binding["knowledge_map_revision"] != knowledge_map.get("revision")
            or validate_study_material_output(study_material_output) is not None
            or validate_knowledge_map(knowledge_map, study_material_output) is not None
        ):
            raise MaterialRunOutputError("MATERIAL_OUTPUT_UNAVAILABLE")
        with open_verified_source_pdf(learner_id, source_artifact_id, dsn=dsn):
            pass
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
