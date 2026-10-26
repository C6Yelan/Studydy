from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from content_identity import canonical_sha256
from knowledge_map.retrieval import build_passages
from knowledge_map.source_identity import _claims
from learning_adaptation.learning_states import progress_plan
from learning_adaptation.map_context import _context_from_validated_document
from knowledge_map.structure import validate_knowledge_structure

from .knowledge_chapters import enqueue as enqueue_chapters
from .material_notes import enqueue as enqueue_notes
from .source_resolver import verify_structure_input, verify_source_files
from .storage.database import database_session
from .storage.knowledge_reads import prepare_concept_index
from .storage.knowledge_structures import (
    KnowledgeStructureStoreError,
    StoredKnowledgeStructure,
    _binding,
    _view,
    _prune_unreferenced_structures,
    _read_verified_document,
)
from .storage.tables import KnowledgeStructure, Material, MaterialProcessingRun, KnowledgeReadIndex, KnowledgeEvidenceRef


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

            full_reanalysis = bool(locked_run.maintenance)
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
                    not full_reanalysis
                    and document.get("source_set_sha256") is not None
                    and document.get("source_set_sha256")
                    == base.get("source_set_sha256")
                    and "material_review" in locked_run.runtime_lock_document
                )
                if full_reanalysis and document.get("source_set_sha256") != base.get("source_set_sha256"):
                    raise KnowledgeStructureStoreError("SOURCE_BINDING_INVALID")
                if not review_only and not full_reanalysis and not any(
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
            from learning_adaptation.learning_goals import rebase_goal_for_revision
            rebase_goal_for_revision(session, learner_id, material_id, document["revision"])
            # 章節分區是此版本的衍生資料，由背景佇列依序計算；失敗時地圖沿用來源檔分區。
            enqueue_chapters(session, learner_id, material_id, document["revision"])
            enqueue_notes(session, learner_id, material_id, document["revision"])
            if locked_run.base_revision is not None and not review_only and not full_reanalysis:
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


def prepare(session, owner, material_id, document):
    """由 publisher 或既有資料升級呼叫；不在一般 GET 交易中重新建索引。"""
    revision = document["revision"]
    prepare_concept_index(session, owner, material_id, revision, document=document)
    if session.get(KnowledgeReadIndex, (owner, material_id, revision)) is not None:
        return
    concepts = [
        {
            "concept_id": c["concept_id"],
            "label": c["label"],
            "aliases": c["aliases"],
            "claim_count": len(c["claims"]),
        }
        for c in document["concepts"]
    ]
    outline = {
        "schema": "knowledge-outline/v1",
        "material_id": str(material_id),
        "knowledge_structure_revision": revision,
        "run_id": document["run_id"],
        "source_resolver": f"/v1/materials/{material_id}/knowledge-structures/{revision}/evidence",
        "status": document["status"],
        "concepts": concepts,
        "relations": [
            {
                key: r[key]
                for key in (
                    "relation_id",
                    "source_concept_id",
                    "target_concept_id",
                    "type",
                )
            }
            for r in document["relations"]
        ],
        "section_count": len(document["document_tree"]["sections"]),
        "initial_learning_path": document["initial_learning_path"],
        "excluded_pages": document["excluded_pages"],
    }
    learning = progress_plan(_context_from_validated_document(material_id, document))
    learning["path_steps"] = document["initial_learning_path"]
    learning["claim_signatures"] = dict(_claims(document))
    learning["run_id"] = document["run_id"]
    session.add(
        KnowledgeReadIndex(
            learner_id=owner,
            material_id=material_id,
            structure_revision=revision,
            outline=outline,
            outline_sha256=canonical_sha256(outline),
            learning=learning,
            learning_sha256=canonical_sha256(learning),
        )
    )
    session.flush()
    pages = document["input_binding"]["bundle"]["pages"]
    references = []
    for evidence in document["evidence"]:
        location = pages[evidence["page"] - 1]
        payload = {
            "source_id": location["source_id"],
            "normalized_page": location["normalized_page"],
            "source_locator": evidence["source_locator"],
        }
        references.append(
            {
                "learner_id": owner,
                "material_id": material_id,
                "structure_revision": revision,
                "evidence_id": evidence["evidence_id"],
                "payload": payload,
                "content_sha256": canonical_sha256(payload),
            }
        )
    if references:
        session.execute(insert(KnowledgeEvidenceRef), references)


def backfill(*, dsn=None):
    """明確補建缺失索引／關係引用；只更新衍生投影，不改 canonical 資料。"""
    with database_session(dsn) as session:
        rows = session.execute(text("""
            SELECT k.learner_id, k.material_id, k.structure_revision
            FROM knowledge_structures k
            LEFT JOIN card_source_indexes c USING (learner_id, material_id, structure_revision)
            LEFT JOIN knowledge_read_indexes r USING (learner_id, material_id, structure_revision)
            WHERE c.structure_revision IS NULL OR r.structure_revision IS NULL
               OR EXISTS (
                   SELECT 1 FROM card_source_concepts p
                   WHERE p.learner_id=k.learner_id AND p.material_id=k.material_id
                     AND p.structure_revision=k.structure_revision
                     AND NOT (
                         jsonb_path_query_array(p.payload, '$.relation_evidence[*].evidence_id')
                         @> jsonb_path_query_array(p.payload, '$.relations[*].evidence_refs[*]')
                     )
               )
        """)).all()
    for owner, material_id, revision in rows:
        with database_session(dsn) as session:
            session.scalar(
                select(Material)
                .where(
                    Material.learner_id == owner, Material.material_id == material_id
                )
                .with_for_update()
            )
            document = _read_verified_document(
                session, owner, material_id, revision=revision
            )
            prepare_concept_index(
                session, owner, material_id, revision, document=document, rebuild=True
            )
            prepare(session, owner, material_id, document)
    return len(rows)
