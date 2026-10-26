from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import select

from knowledge_map.structure import (
    _view_from_validated_document,
    validate_knowledge_structure,
)

from .tables import KnowledgeStructure, MaterialProcessingRun
from .database import database_session
from ..source_resolver import verify_structure_input


class KnowledgeStructureStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredKnowledgeStructure:
    revision: str
    document: dict[str, Any] = field(repr=False)
    view: dict[str, Any] = field(repr=False)


def _view(document, material_id):
    view = _view_from_validated_document(document)
    view["schema"] = "knowledge-structure-view/v1"
    view["source_resolver"] = (
        f"/v1/materials/{material_id}/knowledge-structures/{document['revision']}/evidence"
    )
    binding = document["input_binding"]
    sources = {item["source_id"]: item for item in binding["manifest"]["items"]}
    pages = binding["bundle"]["pages"]
    for concept in view["concepts"]:
        for claim in concept["claims"]:
            for evidence in claim["evidence"]:
                location = pages[evidence["page"] - 1]
                evidence.update(
                    source_id=location["source_id"],
                    source_name=sources[location["source_id"]]["original_name"],
                    normalized_page=location["normalized_page"],
                )
    return view


def _binding(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "material-run-output-binding/v1",
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


def _prune_unreferenced_structures(session, owner, material_id, head):
    """保留被引用的地圖及工作的重播依據。"""
    from .tables import (
        CardSet,
        MaterialResearch,
        NoteAnnotation, MaterialNotes,
        Podcast,
        StudySession,
        VoiceConversation,
    )

    rows = session.scalars(
        select(KnowledgeStructure)
        .where(
            KnowledgeStructure.learner_id == owner,
            KnowledgeStructure.material_id == material_id,
            KnowledgeStructure.structure_revision != head,
        )
        .order_by(KnowledgeStructure.structure_revision)
        .with_for_update()
    ).all()
    note_revisions = set(session.scalars(select(MaterialNotes.structure_revision).join(KnowledgeStructure,
        (KnowledgeStructure.learner_id == MaterialNotes.learner_id)
        & (KnowledgeStructure.material_id == MaterialNotes.material_id)
        & (KnowledgeStructure.structure_revision == MaterialNotes.structure_revision))
        .where(MaterialNotes.learner_id == owner, MaterialNotes.material_id == material_id,
               MaterialNotes.paragraphs.is_not(None), MaterialNotes.generation > 0)
        .distinct(MaterialNotes.kind)
        .order_by(MaterialNotes.kind, KnowledgeStructure.created_at.desc(), MaterialNotes.updated_at.desc())))
    for row in rows:
        # 更新期間仍要回查最後一份可讀筆記的來源，兩種筆記各保護最近一份。
        if row.structure_revision in note_revisions:
            continue
        # 此次維護承諾封存原版；後續追加來源時也保留它。
        if session.scalar(select(MaterialProcessingRun.run_id).where(
            MaterialProcessingRun.material_id == material_id,
            MaterialProcessingRun.base_revision == row.structure_revision,
            MaterialProcessingRun.maintenance.is_not(None),
        ).limit(1)) is not None:
            continue
        referenced = session.scalar(
            select(StudySession.study_session_id)
            .where(
                StudySession.learner_id == owner,
                StudySession.material_id == material_id,
                StudySession.knowledge_structure_revision == row.structure_revision,
            )
            .limit(1)
        )
        active = session.scalar(
            select(MaterialProcessingRun.run_id)
            .where(
                MaterialProcessingRun.material_id == material_id,
                MaterialProcessingRun.base_revision == row.structure_revision,
                MaterialProcessingRun.status.in_(("pending", "running")),
            )
            .limit(1)
        )
        card_set = session.scalar(
            select(CardSet.card_set_id)
            .where(
                CardSet.learner_id == owner,
                CardSet.material_id == material_id,
                CardSet.knowledge_structure_revision == row.structure_revision,
                CardSet.deleted_at.is_(None),
            )
            .limit(1)
        )
        podcast = session.scalar(
            select(Podcast.podcast_id)
            .where(
                Podcast.learner_id == owner,
                Podcast.material_id == material_id,
                Podcast.knowledge_structure_revision == row.structure_revision,
            )
            .limit(1)
        )
        conversation = session.scalar(
            select(VoiceConversation.conversation_id)
            .where(
                VoiceConversation.material_id == material_id,
                VoiceConversation.knowledge_structure_revision
                == row.structure_revision,
                VoiceConversation.deleted_at.is_(None),
            )
            .limit(1)
        )
        # 尚保留的研究以舊版本作比較依據；教材追加後仍須能回查該版本。
        research = session.scalar(
            select(MaterialResearch.research_id)
            .where(
                MaterialResearch.learner_id == owner,
                MaterialResearch.material_id == material_id,
                MaterialResearch.base_revision == row.structure_revision,
                MaterialResearch.deleted_at.is_(None),
            )
            .limit(1)
        )
        # 個人註記的原段落仍需能回查當時來源；不能因教材新版就清掉版本。
        annotation = session.scalar(select(NoteAnnotation.annotation_id).where(
            NoteAnnotation.learner_id == owner, NoteAnnotation.material_id == material_id,
            NoteAnnotation.structure_revision == row.structure_revision).limit(1))
        if (
            annotation is None
            and referenced is None
            and active is None
            and card_set is None
            and podcast is None
            and conversation is None
            and research is None
        ):
            session.delete(row)


def _read_verified_document(
    session, learner_id, material_id, *, run_id=None, revision=None, lock=False
):
    """在呼叫者 snapshot 驗證結構、執行快照及來源關係；不掃描原檔。"""
    if (run_id is None) == (revision is None):
        raise KnowledgeStructureStoreError("KNOWLEDGE_STRUCTURE_UNAVAILABLE")
    # 僅在呼叫者明確建立的唯讀一致 snapshot 內共用，跨請求仍重新驗證。
    snapshot = session.info.get("read_snapshot")
    cache = snapshot["documents"] if snapshot is not None and not lock else None
    key = (learner_id, material_id, run_id, revision)
    if cache is not None and key in cache:
        return cache[key]
    statement = (
        select(
            KnowledgeStructure.document,
            MaterialProcessingRun,
            KnowledgeStructure.structure_revision,
        )
        .join(
            MaterialProcessingRun,
            KnowledgeStructure.run_id == MaterialProcessingRun.run_id,
        )
        .where(
            KnowledgeStructure.learner_id == learner_id,
            KnowledgeStructure.material_id == material_id,
            MaterialProcessingRun.learner_id == learner_id,
            MaterialProcessingRun.material_id == material_id,
            MaterialProcessingRun.status.in_(("succeeded", "partial")),
        )
    )
    statement = statement.where(
        KnowledgeStructure.run_id == run_id
        if run_id is not None
        else KnowledgeStructure.structure_revision == revision
    )
    if lock:
        statement = statement.with_for_update(of=KnowledgeStructure)
    row = session.execute(statement).one_or_none()
    if row is None:
        raise KnowledgeStructureStoreError("KNOWLEDGE_STRUCTURE_UNAVAILABLE")
    document, run, stored_revision = row
    if document.get("input_binding", {}).get("schema") != "structure-input-binding/v2":
        raise KnowledgeStructureStoreError("MATERIAL_FORMAT_UNSUPPORTED")
    if (
        not validate_knowledge_structure(document)
        or document["revision"] != stored_revision
        or document.get("run_id") != str(run.run_id)
        or run.output_binding != _binding(document)
    ):
        raise KnowledgeStructureStoreError("KNOWLEDGE_STRUCTURE_UNAVAILABLE")
    verify_structure_input(session, run, document)
    if cache is not None:
        cache[key] = document
    return document


def read_knowledge_structure(
    learner_id: UUID,
    material_id: UUID,
    *,
    run_id: UUID | None = None,
    revision: str | None = None,
    dsn: str | None = None,
) -> StoredKnowledgeStructure:
    try:
        with database_session(dsn) as session:
            document = _read_verified_document(
                session,
                learner_id,
                material_id,
                run_id=run_id,
                revision=revision,
            )
            from ..review_flags import uncertain_concepts
            flags = uncertain_concepts(session, learner_id, material_id, document["revision"])
        # 旗標只加公開投影，原 document 與 hash 不改寫。
        view = _view(document, material_id)
        for concept in view["concepts"]:
            concept["uncertain"] = concept["concept_id"] in flags
        return StoredKnowledgeStructure(document["revision"], document, view)
    except KnowledgeStructureStoreError:
        raise
    except Exception:
        raise KnowledgeStructureStoreError("KNOWLEDGE_STRUCTURE_UNAVAILABLE") from None
