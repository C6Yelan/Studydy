from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from knowledge_map.structure import (
    build_knowledge_structure_view,
    validate_knowledge_structure,
)
from pdf_evidence.ocr_page_evidence import canonical_sha256

from .artifacts import open_verified_source_pdf
from .tables import KnowledgeStructure, MaterialProcessingRun, database_session


class KnowledgeStructureStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredKnowledgeStructure:
    revision: str
    document: dict[str, Any] = field(repr=False)
    view: dict[str, Any] = field(repr=False)


def runtime_binding_is_valid(value: Any) -> bool:
    try:
        if not isinstance(value, dict) or set(value) != {
            "schema", "python", "runtime_lock_sha256", "model_id", "model_revision",
            "semantic_service", "ocr", "policy", "runtime_binding_sha256",
        }:
            return False
        identity = {
            key: item for key, item in value.items() if key != "runtime_binding_sha256"
        }
        return (
            value["schema"] == "material-runtime-binding/v1"
            and value["python"] == "3.12"
            and value["runtime_binding_sha256"] == canonical_sha256(identity)
            and isinstance(value["runtime_lock_sha256"], str)
            and len(value["runtime_lock_sha256"]) == 64
            and all(character in "0123456789abcdef" for character in value["runtime_lock_sha256"])
            and value["model_id"] == "Qwen/Qwen3.8-27B-FP8"
            and isinstance(value["model_revision"], str)
            and len(value["model_revision"]) == 40
            and all(character in "0123456789abcdef" for character in value["model_revision"])
            and value["semantic_service"] == {
                "base_url": "http://127.0.0.1:8000",
                "max_model_len": 32768,
                "server": {
                    "package": "vllm", "version": "0.28.0", "python": "3.12",
                    "torch": "2.13.0+cu130", "cuda": "13.0",
                    "transformers": "5.16.1",
                },
            }
            and value["ocr"] == {
                "model_id": "Unlimited-OCR",
                "revision": "07dea832e22aefee32ad281d4b80551282e1c168",
            }
            and value["policy"] == "evidence-unified-semantics-product/v1"
        )
    except (KeyError, TypeError, ValueError):
        return False


def _binding(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "material-run-output-binding/v4",
        "knowledge_structure_revision": document["revision"],
        "runtime_lock_sha256": document["provenance"]["runtime_lock_sha256"],
        "page_count": document["page_count"],
        "processing": document["status"]["processing"],
        "quality": document["status"]["quality"],
        "decision": document["status"]["decision"],
        "reason_codes": deepcopy(document["status"]["reason_codes"]),
        "ocr_calls": document["metrics"]["ocr_calls"],
        "semantic_calls": document["metrics"]["semantic_calls"],
    }


def publish_knowledge_structure(
    learner_id: UUID,
    material_id: UUID,
    run_id: UUID,
    document: dict[str, Any],
    *,
    dsn: str | None = None,
) -> StoredKnowledgeStructure:
    if not validate_knowledge_structure(document) or document.get("run_id") != str(run_id):
        raise KnowledgeStructureStoreError("KNOWLEDGE_STRUCTURE_INVALID")
    binding = _binding(document)
    if binding["processing"] not in {"succeeded", "partial"}:
        raise KnowledgeStructureStoreError("KNOWLEDGE_STRUCTURE_INVALID")
    try:
        with database_session(dsn) as session:
            run = session.execute(
                select(
                    MaterialProcessingRun.runtime_binding,
                    MaterialProcessingRun.source_artifact_id,
                ).where(
                    MaterialProcessingRun.learner_id == learner_id,
                    MaterialProcessingRun.material_id == material_id,
                    MaterialProcessingRun.run_id == run_id,
                    MaterialProcessingRun.status == "running",
                    MaterialProcessingRun.progress_stage == "publishing",
                )
            ).one_or_none()
            if (
                run is None
                or not runtime_binding_is_valid(run[0])
                or run[0].get("runtime_lock_sha256") != binding["runtime_lock_sha256"]
            ):
                raise KnowledgeStructureStoreError("MATERIAL_RUN_UNAVAILABLE")
            session.execute(
                pg_insert(KnowledgeStructure)
                .values(
                    learner_id=learner_id,
                    material_id=material_id,
                    structure_revision=document["revision"],
                    run_id=run_id,
                    document=document,
                    created_at=datetime.now(UTC),
                )
                .on_conflict_do_nothing()
            )
            stored = session.execute(
                select(KnowledgeStructure.run_id, KnowledgeStructure.document).where(
                    KnowledgeStructure.learner_id == learner_id,
                    KnowledgeStructure.material_id == material_id,
                    KnowledgeStructure.structure_revision == document["revision"],
                )
            ).one_or_none()
            if stored != (run_id, document):
                raise KnowledgeStructureStoreError("KNOWLEDGE_STRUCTURE_CONFLICT")
            status = binding["processing"]
            updated = session.execute(
                update(MaterialProcessingRun)
                .where(
                    MaterialProcessingRun.learner_id == learner_id,
                    MaterialProcessingRun.material_id == material_id,
                    MaterialProcessingRun.run_id == run_id,
                    MaterialProcessingRun.status == "running",
                    MaterialProcessingRun.progress_stage == "publishing",
                )
                .values(
                    status=status,
                    progress_stage="completed",
                    completed_pages=document["page_count"],
                    total_pages=document["page_count"],
                    output_binding=binding,
                    completed_at=func.clock_timestamp(),
                    updated_at=func.clock_timestamp(),
                )
                .returning(MaterialProcessingRun.run_id)
            ).scalar_one_or_none()
            if updated is None:
                raise KnowledgeStructureStoreError("MATERIAL_RUN_UNAVAILABLE")
    except KnowledgeStructureStoreError:
        raise
    except Exception:
        raise KnowledgeStructureStoreError("KNOWLEDGE_STRUCTURE_STORE_FAILED") from None
    return StoredKnowledgeStructure(
        document["revision"], deepcopy(document), build_knowledge_structure_view(document)
    )


def read_knowledge_structure(
    learner_id: UUID,
    material_id: UUID,
    *,
    run_id: UUID | None = None,
    revision: str | None = None,
    dsn: str | None = None,
) -> StoredKnowledgeStructure:
    if (run_id is None) == (revision is None):
        raise KnowledgeStructureStoreError("KNOWLEDGE_STRUCTURE_UNAVAILABLE")
    try:
        with database_session(dsn) as session:
            statement = select(
                KnowledgeStructure.document,
                MaterialProcessingRun.output_binding,
                MaterialProcessingRun.runtime_binding,
                MaterialProcessingRun.source_artifact_id,
            ).join(
                MaterialProcessingRun,
                KnowledgeStructure.run_id == MaterialProcessingRun.run_id,
            ).where(
                KnowledgeStructure.learner_id == learner_id,
                KnowledgeStructure.material_id == material_id,
                MaterialProcessingRun.learner_id == learner_id,
                MaterialProcessingRun.material_id == material_id,
                MaterialProcessingRun.status.in_(("succeeded", "partial")),
            )
            statement = statement.where(
                KnowledgeStructure.run_id == run_id
                if run_id is not None
                else KnowledgeStructure.structure_revision == revision
            )
            row = session.execute(statement).one_or_none()
        if row is None:
            raise KnowledgeStructureStoreError("KNOWLEDGE_STRUCTURE_UNAVAILABLE")
        document, binding, runtime_binding, source_artifact_id = row
        if (
            not validate_knowledge_structure(document)
            or not isinstance(binding, dict)
            or binding != _binding(document)
            or not isinstance(runtime_binding, dict)
            or not runtime_binding_is_valid(runtime_binding)
            or runtime_binding.get("runtime_lock_sha256") != document["provenance"]["runtime_lock_sha256"]
        ):
            raise KnowledgeStructureStoreError("KNOWLEDGE_STRUCTURE_UNAVAILABLE")
        with open_verified_source_pdf(learner_id, source_artifact_id, dsn=dsn) as source:
            if source.material_id != material_id or source.sha256 != document["source_sha256"]:
                raise KnowledgeStructureStoreError("KNOWLEDGE_STRUCTURE_UNAVAILABLE")
        return StoredKnowledgeStructure(
            document["revision"], deepcopy(document), build_knowledge_structure_view(document)
        )
    except KnowledgeStructureStoreError:
        raise
    except Exception:
        raise KnowledgeStructureStoreError("KNOWLEDGE_STRUCTURE_UNAVAILABLE") from None
