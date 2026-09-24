from __future__ import annotations

from copy import deepcopy
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from knowledge_map.structure import (
    _view_from_validated_document,
    validate_knowledge_structure,
)

from .tables import KnowledgeStructure, Material, MaterialProcessingRun, database_session
from .source_artifacts import open_verified_artifact
from ..source_normalization import SourceError
from ..source_resolver import verify_structure_input, verify_source_files


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

    binding = _binding(document)
    if binding["processing"] not in {"succeeded", "partial"}:
        raise KnowledgeStructureStoreError("KNOWLEDGE_STRUCTURE_INVALID")
    try:
        with database_session(dsn) as session:
            material = session.scalar(select(Material).where(
                Material.learner_id == learner_id,
                Material.material_id == material_id,
            ).with_for_update())
            locked_run = session.scalar(select(MaterialProcessingRun).where(
                MaterialProcessingRun.learner_id == learner_id,
                MaterialProcessingRun.material_id == material_id,
                MaterialProcessingRun.run_id == run_id,
            ).with_for_update())
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
                base = session.scalar(select(KnowledgeStructure.document).where(
                    KnowledgeStructure.learner_id == learner_id,
                    KnowledgeStructure.material_id == material_id,
                    KnowledgeStructure.structure_revision == locked_run.base_revision,
                ))
                if base is None:
                    raise KnowledgeStructureStoreError("REVISION_CONFLICT")
                added_evidence = {
                    item["evidence_id"]
                    for item in document["evidence"]
                    if item["page"] > base["page_count"]
                }
                # 舊內容會被重用；只剩舊 Claims 時不能把追加顯示為已完成。
                review_only = (
                    document.get("source_set_sha256") is not None
                    and document.get("source_set_sha256") == base.get("source_set_sha256")
                    and "material_review" in locked_run.runtime_lock_document
                )
                if not review_only and not any(
                    added_evidence.intersection(claim["evidence_refs"])
                    for concept in document["concepts"]
                    for claim in concept["claims"]
                ):
                    raise KnowledgeStructureStoreError("NO_USABLE_ADDED_CONTENT")
            if locked_run.status != "running" or locked_run.progress_stage != "publishing":
                raise KnowledgeStructureStoreError("MATERIAL_RUN_UNAVAILABLE")
            source_binding = verify_structure_input(session, locked_run, document)
            verify_source_files(learner_id, source_binding, dsn=dsn)
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
            session.flush()
            if locked_run.base_revision is not None and not review_only:
                _prune_unreferenced_structures(session, learner_id, material_id, material.head_revision)
    except KnowledgeStructureStoreError:
        raise
    except Exception:
        raise KnowledgeStructureStoreError("KNOWLEDGE_STRUCTURE_STORE_FAILED") from None
    return StoredKnowledgeStructure(
        document["revision"], deepcopy(document), _view(document, material_id)
    )


def _prune_unreferenced_structures(session, owner, material_id, head):
    """保留目前圖與學習紀錄必需的圖；run receipt／SourceSet 留作重播依據。"""
    from .tables import StudySession

    rows = session.scalars(
        select(KnowledgeStructure).where(
            KnowledgeStructure.learner_id == owner,
            KnowledgeStructure.material_id == material_id,
            KnowledgeStructure.structure_revision != head,
        ).order_by(KnowledgeStructure.structure_revision).with_for_update()
    ).all()
    for row in rows:
        referenced = session.scalar(select(StudySession.study_session_id).where(
            StudySession.learner_id == owner,
            StudySession.material_id == material_id,
            StudySession.knowledge_structure_revision == row.structure_revision,
        ).limit(1))
        active = session.scalar(select(MaterialProcessingRun.run_id).where(
            MaterialProcessingRun.material_id == material_id,
            MaterialProcessingRun.base_revision == row.structure_revision,
            MaterialProcessingRun.status.in_(("pending", "running")),
        ).limit(1))
        if referenced is None and active is None:
            session.delete(row)


def _read_verified_document(session, learner_id, material_id, *, run_id=None, revision=None, lock=False):
    """在呼叫者 snapshot 驗證結構、執行快照及來源關係；不掃描原檔。"""
    if (run_id is None) == (revision is None):
        raise KnowledgeStructureStoreError("KNOWLEDGE_STRUCTURE_UNAVAILABLE")
    statement = select(
        KnowledgeStructure.document, MaterialProcessingRun, KnowledgeStructure.structure_revision,
    ).join(MaterialProcessingRun, KnowledgeStructure.run_id == MaterialProcessingRun.run_id).where(
        KnowledgeStructure.learner_id == learner_id, KnowledgeStructure.material_id == material_id,
        MaterialProcessingRun.learner_id == learner_id, MaterialProcessingRun.material_id == material_id,
        MaterialProcessingRun.status.in_(("succeeded", "partial")),
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
    if (
        not validate_knowledge_structure(document)
        or document["revision"] != stored_revision
        or document.get("run_id") != str(run.run_id)
        or run.output_binding != _binding(document)
    ):
        raise KnowledgeStructureStoreError("KNOWLEDGE_STRUCTURE_UNAVAILABLE")
    verify_structure_input(session, run, document)
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
                session, learner_id, material_id, run_id=run_id, revision=revision,
            )
        return StoredKnowledgeStructure(document["revision"], deepcopy(document), _view(document, material_id))
    except KnowledgeStructureStoreError:
        raise
    except Exception:
        raise KnowledgeStructureStoreError("KNOWLEDGE_STRUCTURE_UNAVAILABLE") from None


def resolve_evidence_source(owner, material_id, revision, evidence_id, *, dsn=None):
    stored = read_knowledge_structure(owner, material_id, revision=revision, dsn=dsn)
    document = stored.document
    evidence = next((item for item in document["evidence"] if item["evidence_id"] == evidence_id), None)
    if evidence is None:
        raise SourceError("RESOURCE_NOT_FOUND")
    binding = document.get("input_binding")
    location = binding["bundle"]["pages"][evidence["page"] - 1]
    source = next(item for item in binding["manifest"]["items"] if item["source_id"] == location["source_id"])
    normalized_page = location["normalized_page"]
    with open_verified_artifact(owner, UUID(source["mapping_artifact_id"]), dsn=dsn) as opened:
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
        record for record in mapping["records"]
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
