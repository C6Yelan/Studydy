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
from .tables import KnowledgeStructure, MaterialProcessingRun, Material, database_session


class KnowledgeStructureStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredKnowledgeStructure:
    revision: str
    document: dict[str, Any] = field(repr=False)
    view: dict[str, Any] = field(repr=False)


def runtime_binding_is_valid(value: Any) -> bool:
    try:
        if isinstance(value,dict) and value.get("schema")=="material-runtime-binding/v2":
            identity={k:v for k,v in value.items() if k!="runtime_binding_sha256"}
            service=value.get("semantic_service",{})
            return (set(value)=={"schema","python","runtime_lock_sha256","model_id","model_revision","semantic_service","ocr","policy","runtime_binding_sha256"}
                    and value["runtime_binding_sha256"]==canonical_sha256(identity)
                    and set(service)=={"transport","model_id","model_revision","config_sha256","runtime_lock_sha256"}
                    and service["transport"]=="command" and service["model_id"]==value["model_id"]
                    and service["model_revision"]==value["model_revision"] and service["runtime_lock_sha256"]==value["runtime_lock_sha256"]
                    and all(isinstance(service[k],str) and len(service[k])==64 for k in ("config_sha256","runtime_lock_sha256")))
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
            and value["model_id"] == "google/gemma-4-31B-it-qat-w4a16-ct"
            and value["model_revision"] == "52f3f65bc7a02d555763bc923bd1d9094898219d"
            and value["semantic_service"] == {
                "base_url": "http://127.0.0.1:18000",
                "max_model_len": 32768,
                "server": {
                    "package": "vllm", "version": "0.28.0", "python": "3.12",
                    "torch": "2.13.0+cu130", "cuda": "13.0",
                    "transformers": "5.15.1",
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


def _view(document,material_id):
    view=build_knowledge_structure_view(document)
    if "input_binding" in document:
        view["schema"]="knowledge-structure-view/v3"
        view["source_resolver"]=f"/v2/materials/{material_id}/knowledge-structures/{document['revision']}/evidence"
        binding=document['input_binding']
        sources={item['source_id']:item for item in binding['manifest']['items']}
        for concept in view['concepts']:
            for claim in concept['claims']:
                for evidence in claim['evidence']:
                    location=binding['bundle']['pages'][evidence['page']-1]
                    evidence.update(source_id=location['source_id'],source_name=sources[location['source_id']]['original_name'],normalized_page=location['normalized_page'])
    return view


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
    worker_token: UUID | None = None,
) -> StoredKnowledgeStructure:
    if not validate_knowledge_structure(document) or document.get("run_id") != str(run_id):
        raise KnowledgeStructureStoreError("KNOWLEDGE_STRUCTURE_INVALID")
    from ..source_resolver import verify_structure_input
    verify_structure_input(learner_id,run_id,document,dsn=dsn)
    binding = _binding(document)
    if binding["processing"] not in {"succeeded", "partial"}:
        raise KnowledgeStructureStoreError("KNOWLEDGE_STRUCTURE_INVALID")
    try:
        with database_session(dsn) as session:
            material = session.scalar(select(Material).where(Material.learner_id == learner_id,
                Material.material_id == material_id).with_for_update())
            locked_run = session.scalar(select(MaterialProcessingRun).where(MaterialProcessingRun.learner_id == learner_id,
                MaterialProcessingRun.material_id == material_id, MaterialProcessingRun.run_id == run_id).with_for_update())
            if (material is None or material.discard_requested_at is not None or locked_run is None
                or locked_run.cancel_requested_at is not None
                or (worker_token is not None and (locked_run.worker_token != worker_token or locked_run.lease_expires_at <= datetime.now(UTC)))):
                raise KnowledgeStructureStoreError("MATERIAL_RUN_UNAVAILABLE")
            if locked_run.base_revision is not None:
                from ..source_revisions import current_revision
                if current_revision(session, material) != locked_run.base_revision:
                    raise KnowledgeStructureStoreError("REVISION_CONFLICT")
                base = session.scalar(select(KnowledgeStructure.document).where(
                    KnowledgeStructure.learner_id == learner_id,
                    KnowledgeStructure.material_id == material_id,
                    KnowledgeStructure.structure_revision == locked_run.base_revision,
                ))
                if base is None:
                    raise KnowledgeStructureStoreError("REVISION_CONFLICT")
                added_evidence = {item["evidence_id"] for item in document["evidence"]
                                  if item["page"] > base["page_count"]}
                # 舊內容會被重用；只剩舊 Claims 時不能把追加顯示為已完成。
                if not any(added_evidence.intersection(claim["evidence_refs"])
                           for concept in document["concepts"] for claim in concept["claims"]):
                    raise KnowledgeStructureStoreError("NO_USABLE_ADDED_CONTENT")
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
            # 品質提示隨結果保留，不阻擋已驗證且含新增內容的地圖發布。
            material.head_revision = document["revision"]
            material.source_artifact_id = locked_run.source_artifact_id
            if document["schema"] == "knowledge-structure/v4":
                material.ingestion_kind = "sources-v2"
            session.flush()
            if locked_run.base_revision is not None:
                _prune_unreferenced_structures(session, learner_id, material_id, material.head_revision)
    except KnowledgeStructureStoreError:
        raise
    except Exception:
        raise KnowledgeStructureStoreError("KNOWLEDGE_STRUCTURE_STORE_FAILED") from None
    return StoredKnowledgeStructure(
        document["revision"], deepcopy(document), _view(document,material_id)
    )


def _prune_unreferenced_structures(session, owner, material_id, head):
    """保留目前圖與學習紀錄必需的圖；run receipt／SourceSet 留作重播依據。"""
    from .tables import StudySession
    rows = session.scalars(select(KnowledgeStructure).where(KnowledgeStructure.learner_id == owner,
        KnowledgeStructure.material_id == material_id,
        KnowledgeStructure.structure_revision != head)
        .order_by(KnowledgeStructure.structure_revision).with_for_update()).all()
    for row in rows:
        referenced = session.scalar(select(StudySession.study_session_id).where(StudySession.learner_id == owner,
            StudySession.material_id == material_id, StudySession.knowledge_structure_revision == row.structure_revision).limit(1))
        active = session.scalar(select(MaterialProcessingRun.run_id).where(MaterialProcessingRun.material_id == material_id,
            MaterialProcessingRun.base_revision == row.structure_revision, MaterialProcessingRun.status.in_(("pending", "running"))).limit(1))
        if referenced is None and active is None:
            session.delete(row)


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
                KnowledgeStructure.structure_revision,
                KnowledgeStructure.run_id,
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
        document, binding, runtime_binding, source_artifact_id, stored_revision, stored_run_id = row
        if (
            not validate_knowledge_structure(document)
            or document.get("revision") != stored_revision
            or document.get("run_id") != str(stored_run_id)
            or not isinstance(binding, dict)
            or binding != _binding(document)
            or not isinstance(runtime_binding, dict)
            or not runtime_binding_is_valid(runtime_binding)
            or runtime_binding.get("runtime_lock_sha256") != document["provenance"]["runtime_lock_sha256"]
        ):
            raise KnowledgeStructureStoreError("KNOWLEDGE_STRUCTURE_UNAVAILABLE")
        from ..source_resolver import verify_structure_input
        verify_structure_input(learner_id,stored_run_id,document,dsn=dsn)
        if document["schema"] != "knowledge-structure/v4":
            with open_verified_source_pdf(learner_id, source_artifact_id, dsn=dsn) as source:
                if source.material_id != material_id or source.sha256 != document["source_sha256"]:
                    raise KnowledgeStructureStoreError("KNOWLEDGE_STRUCTURE_UNAVAILABLE")
        return StoredKnowledgeStructure(
            document["revision"], deepcopy(document), _view(document,material_id)
        )
    except KnowledgeStructureStoreError:
        raise
    except Exception:
        raise KnowledgeStructureStoreError("KNOWLEDGE_STRUCTURE_UNAVAILABLE") from None
