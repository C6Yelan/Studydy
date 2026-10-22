from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from knowledge_map.retrieval import build_passages
from knowledge_map.structure import validate_knowledge_structure

from .knowledge_chapters import enqueue as enqueue_chapters
from .source_resolver import verify_structure_input, verify_source_files
from .storage.database import database_session
from .storage.knowledge_reads import prepare
from .storage.knowledge_structures import (
    KnowledgeStructureStoreError,
    StoredKnowledgeStructure,
    _binding,
    _view,
    _prune_unreferenced_structures,
)
from .storage.tables import KnowledgeStructure, Material, MaterialProcessingRun


def publish_knowledge_structure(
    learner_id: UUID,
    material_id: UUID,
    run_id: UUID,
    document: dict[str, Any],
    *,
    dsn: str | None = None,
    worker_token: UUID | None = None,
    retrieval_passages: list[dict] | None = None,
) -> StoredKnowledgeStructure:
    if not validate_knowledge_structure(document) or document.get("run_id") != str(
        run_id
    ):
        raise KnowledgeStructureStoreError("KNOWLEDGE_STRUCTURE_INVALID")

    binding = _binding(document)
    if binding["processing"] not in {"succeeded", "partial"}:
        raise KnowledgeStructureStoreError("KNOWLEDGE_STRUCTURE_INVALID")
    from .retrieval import publish_index
    passages = (
        retrieval_passages
        if retrieval_passages is not None
        else build_passages(document)
    )
    try:
        with database_session(dsn) as session:
            material = session.scalar(
                select(Material)
                .where(
                    Material.learner_id == learner_id,
                    Material.material_id == material_id,
                )
                .with_for_update()
            )
            locked_run = session.scalar(
                select(MaterialProcessingRun)
                .where(
                    MaterialProcessingRun.learner_id == learner_id,
                    MaterialProcessingRun.material_id == material_id,
                    MaterialProcessingRun.run_id == run_id,
                )
                .with_for_update()
            )
            if (
                material is None
                or material.discard_requested_at is not None
                or locked_run is None
                or locked_run.cancel_requested_at is not None
            ):
                raise KnowledgeStructureStoreError("MATERIAL_RUN_UNAVAILABLE")
            if worker_token is not None and (
                locked_run.worker_token != worker_token
                or locked_run.lease_expires_at <= datetime.now(UTC)
            ):
                raise KnowledgeStructureStoreError("MATERIAL_RUN_UNAVAILABLE")

            review_only = False
            if locked_run.base_revision is not None:
                if material.head_revision != locked_run.base_revision:
                    raise KnowledgeStructureStoreError("REVISION_CONFLICT")
                base = session.scalar(
                    select(KnowledgeStructure.document).where(
                        KnowledgeStructure.learner_id == learner_id,
                        KnowledgeStructure.material_id == material_id,
                        KnowledgeStructure.structure_revision
                        == locked_run.base_revision,
                    )
                )
                if base is None:
                    raise KnowledgeStructureStoreError("REVISION_CONFLICT")
                added_evidence = {
                    item["evidence_id"]
                    for item in document["evidence"]
                    if item["page"] > base["page_count"]
                }
                # 追加必須有可用新 Claim；僅重用已發布內容不能算完成。
                review_only = (
                    document.get("source_set_sha256") is not None
                    and document.get("source_set_sha256")
                    == base.get("source_set_sha256")
                    and "material_review" in locked_run.runtime_lock_document
                )
                if not review_only and not any(
                    added_evidence.intersection(claim["evidence_refs"])
                    for concept in document["concepts"]
                    for claim in concept["claims"]
                ):
                    raise KnowledgeStructureStoreError("NO_USABLE_ADDED_CONTENT")
            if (
                locked_run.status != "running"
                or locked_run.progress_stage != "publishing"
            ):
                raise KnowledgeStructureStoreError("MATERIAL_RUN_UNAVAILABLE")
            source_binding = verify_structure_input(session, locked_run, document)
            verify_source_files(session, learner_id, source_binding)
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
            publish_index(session, learner_id, material_id, document, passages)
            prepare(session, learner_id, material_id, document)
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
            session.flush()
            # 章節分區是此版本的衍生資料，由背景佇列依序計算；失敗時地圖沿用來源檔分區。
            enqueue_chapters(session, learner_id, material_id, document["revision"])
            if locked_run.base_revision is not None and not review_only:
                _prune_unreferenced_structures(
                    session, learner_id, material_id, material.head_revision
                )
    except KnowledgeStructureStoreError:
        raise
    except Exception:
        raise KnowledgeStructureStoreError("KNOWLEDGE_STRUCTURE_STORE_FAILED") from None
    return StoredKnowledgeStructure(
        document["revision"], deepcopy(document), _view(document, material_id)
    )
