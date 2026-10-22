from __future__ import annotations

from copy import deepcopy
import json
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import select

from knowledge_map.structure import (
    _view_from_validated_document,
    validate_knowledge_structure,
)

from .tables import KnowledgeStructure, Material, MaterialProcessingRun
from .database import database_session
from .artifacts import open_verified_artifact
from ..errors import SourceError
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
    for row in rows:
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
        if (
            referenced is None
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
        # session 已結束，這份 JSON 專屬本次讀取；不再複製完整大型教材。
        return StoredKnowledgeStructure(
            document["revision"], document, _view(document, material_id)
        )
    except KnowledgeStructureStoreError:
        raise
    except Exception:
        raise KnowledgeStructureStoreError("KNOWLEDGE_STRUCTURE_UNAVAILABLE") from None


def resolve_evidence_source(owner, material_id, revision, evidence_id, *, dsn=None):
    from .knowledge_reads import evidence_source

    with database_session(dsn) as session:
        source, evidence = evidence_source(
            session, owner, material_id, revision, evidence_id
        )
    normalized_page = evidence["normalized_page"]
    with open_verified_artifact(
        owner, UUID(source["mapping_artifact_id"]), dsn=dsn
    ) as opened:
        mapping = json.loads(opened.file.read())
    if (
        mapping["original_sha256"] != source["original_sha256"]
        or mapping["normalized_sha256"] != source["normalized_sha256"]
    ):
        raise SourceError("SOURCE_BINDING_INVALID")

    region = evidence["source_locator"]["region"]

    def overlaps(candidate):
        box = candidate.get("region")
        return box is None or (
            min(box[2], region[2]) > max(box[0], region[0])
            and min(box[3], region[3]) > max(box[1], region[1])
        )

    records = [
        record
        for record in mapping["records"]
        if (record.get("normalized_page") == normalized_page and overlaps(record))
        or any(
            candidate["normalized_page"] == normalized_page and overlaps(candidate)
            for candidate in record.get("candidates", [])
        )
    ]
    if not records:
        accuracy = "unavailable"
    elif all(record["accuracy"] == "exact" for record in records):
        accuracy = "exact"
    else:
        accuracy = "ambiguous"
    locators = [record["origin_locator"] for record in records]
    label = f"轉換後第 {normalized_page} 頁"
    if mapping["format"] == "pdf":
        label = f"原始教材第 {normalized_page} 頁"
    elif mapping["format"] == "pptx" and locators:
        label = f"原教材第 {locators[0]['original_slide_number']} 張投影片（轉換後第 {normalized_page} 頁）"
    elif mapping["format"] in ("txt", "md") and locators:
        first_line = min(locator["line_start"] for locator in locators)
        last_line = max(locator["line_end"] for locator in locators)
        label = f"原文第 {first_line}–{last_line} 行（轉換後第 {normalized_page} 頁）"
    return {
        "schema": "evidence-source/v1",
        "format": mapping["format"],
        "original_name": source["original_name"],
        "original_url": f"/v1/artifacts/{source['original_artifact_id']}/download",
        "preview_url": f"/v1/artifacts/{source['normalized_artifact_id']}#page={normalized_page}",
        "normalized_page": normalized_page,
        "accuracy": accuracy,
        "origin_locators": locators,
        "label": label,
    }
