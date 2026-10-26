"""追加來源的短交易：封存有序集合、保存重播意圖；PDF 各自保存。"""

from copy import deepcopy
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select, update

from content_identity import canonical_sha256
from pdf_evidence.analysis_scope import selected_pages
from .storage.materials import require_source_material
from .errors import SourceError
from .material_runtime import runtime_binding
from .storage.artifacts import _key_digest
from .storage.tables import (
    Artifact,
    KnowledgeStructure,
    MaterialProcessingRun,
    MaterialSource,
    MaterialSourceSet,
    MaterialSourceSetItem,
    MaterialResearch,
    SourceNormalization,
    TopicScope,
)
from .generation_jobs import lease_is_current
from .storage.database import database_session
from .source_transcript import transcript_metadata


def _descriptor(session, job):
    source = session.get(MaterialSource, job.source_id)
    if source.removed_at is not None:
        raise SourceError("SOURCE_NOT_READY")
    original = session.get(Artifact, source.original_artifact_id)
    normalized = session.get(Artifact, job.normalized_artifact_id)
    mapping = (
        session.get(Artifact, job.mapping_artifact_id)
        if job.mapping_artifact_id
        else None
    )
    if mapping is None or job.page_count is None:
        raise SourceError("SOURCE_NOT_READY")
    item = {
        "source_id": str(source.source_id),
        "normalization_id": str(job.normalization_id),
        "original_artifact_id": str(original.artifact_id),
        "original_sha256": bytes(original.sha256).hex(),
        "normalized_artifact_id": str(normalized.artifact_id),
        "normalized_sha256": bytes(normalized.sha256).hex(),
        "mapping_artifact_id": str(mapping.artifact_id),
        "mapping_sha256": bytes(mapping.sha256).hex(),
        "media_type": source.media_type,
        "original_name": source.original_name,
        "policy_sha256": canonical_sha256(job.policy),
        "page_count": job.page_count,
        "analysis_scope": deepcopy(job.analysis_scope),
    }
    transcript = transcript_metadata(job.learner_id, item, session)
    if transcript is not None:
        item["transcript"] = transcript
    return item


def _fingerprint(material_id, normalization_ids, runtime, base_revision):
    value = {
        "material_id": str(material_id),
        "normalizations": [str(identity) for identity in normalization_ids],
        "runtime": runtime,
    }
    if base_revision is not None:
        value["base_revision"] = base_revision
    return bytes.fromhex(canonical_sha256(value))


def retry_revision(owner, run_id, key, config, *, dsn=None):
    """重試沿用原封存輸入，不能從目前 staged 清單猜測來源或順序。"""
    with database_session(dsn) as session:
        run = session.scalar(
            select(MaterialProcessingRun).where(
                MaterialProcessingRun.learner_id == owner,
                MaterialProcessingRun.run_id == run_id,
            )
        )
        if run is None:
            raise SourceError("RESOURCE_NOT_FOUND")
        if run.status != "failed" or run.input_source_set_id is None or run.maintenance:
            raise SourceError("REQUEST_INVALID")
        require_source_material(session, owner, run.material_id)
        source_set = session.get(MaterialSourceSet, run.input_source_set_id)
        prefix = 0
        if run.base_revision:
            base = session.scalar(
                select(MaterialProcessingRun).where(
                    MaterialProcessingRun.learner_id == owner,
                    MaterialProcessingRun.material_id == run.material_id,
                    MaterialProcessingRun.output_binding[
                        "knowledge_structure_revision"
                    ].astext
                    == run.base_revision,
                )
            )
            if base is None:
                raise SourceError("REVISION_CONFLICT")
            base_set = session.get(MaterialSourceSet, base.input_source_set_id)
            if base_set is None:
                raise SourceError("SOURCE_BINDING_INVALID")
            prefix = len(base_set.manifest["items"])
        additions = [
            UUID(item["normalization_id"])
            for item in source_set.manifest["items"][prefix:]
        ]
        material_id, base_revision = run.material_id, run.base_revision
    return create_revision(
        owner,
        material_id,
        additions,
        key,
        config,
        base_revision=base_revision,
        dsn=dsn,
        research_retry_of=run_id,
    )


def _check_revision_intent(
    session, owner, material_id, digest, normalization_ids, base_revision
):
    """兩個交易都須在父鎖內重驗意圖與 head；共用規則，不省略第二次核對。"""
    material = require_source_material(session, owner, material_id)
    existing = session.scalar(
        select(MaterialProcessingRun).where(
            MaterialProcessingRun.learner_id == owner,
            MaterialProcessingRun.idempotency_key_sha256 == digest,
        )
    )
    if existing is not None:
        if bytes(existing.request_fingerprint) != _fingerprint(
            material_id,
            normalization_ids,
            existing.runtime_binding,
            base_revision,
        ):
            raise SourceError("IDEMPOTENCY_CONFLICT")
        return material, existing
    if material.head_revision != base_revision:
        raise SourceError("REVISION_CONFLICT")
    if session.scalar(
        select(MaterialProcessingRun.run_id).where(
            MaterialProcessingRun.material_id == material_id,
            MaterialProcessingRun.status.in_(("pending", "running")),
        )
    ):
        raise SourceError("REVISION_IN_PROGRESS")
    return material, None


def create_revision(
    owner,
    material_id,
    normalization_ids,
    key,
    config,
    *,
    base_revision=None,
    dsn=None,
    research_id=None,
    research_retry_of=None,
    research_token=None,
    full_reanalysis=False,
):
    from .material_processing import _row

    if full_reanalysis and (normalization_ids or base_revision is None):
        raise SourceError("REQUEST_INVALID")

    def link_retry(session, result):
        if research_retry_of is not None:
            # 與新 run 同一交易更新研究入口，避免重試成功後仍指向舊失敗頁。
            session.execute(
                update(MaterialResearch)
                .where(
                    MaterialResearch.learner_id == owner,
                    MaterialResearch.material_id == material_id,
                    MaterialResearch.run_id == research_retry_of,
                    MaterialResearch.status == "submitted",
                )
                .values(run_id=result.run_id)
            )
        if result.display_summary is None:
            from .material_run_summary import run_summary
            result.display_summary = run_summary(session, result)
        return _row(result)

    # 空追加 + 明確現行版本代表只檢核已保存分析；來源集合維持完全相同。
    if (not normalization_ids and base_revision is None) or len(
        set(normalization_ids)
    ) != len(normalization_ids):
        raise SourceError("REQUEST_INVALID")
    if not normalization_ids and "material_review" not in config["runtime_lock"]:
        raise SourceError("REQUEST_INVALID")
    digest = _key_digest(key)
    # 先查已保存意圖：head 已切換或目前環境設定改變，不使合法重播失效。
    with database_session(dsn) as session:
        material, existing = _check_revision_intent(
            session,
            owner,
            material_id,
            digest,
            normalization_ids,
            base_revision,
        )
        if existing is not None:
            return link_retry(session, existing)
        before = (
            session.scalar(
                select(KnowledgeStructure).where(
                    KnowledgeStructure.learner_id == owner,
                    KnowledgeStructure.material_id == material_id,
                    KnowledgeStructure.structure_revision == base_revision,
                )
            )
            if base_revision
            else None
        )
        old_binding = deepcopy(before.document["input_binding"]) if before else None
        if old_binding and old_binding.get("schema") != "structure-input-binding/v2":
            raise SourceError("MATERIAL_FORMAT_UNSUPPORTED")
        if not normalization_ids and (
            not old_binding or old_binding["schema"] != "structure-input-binding/v2"
        ):
            raise SourceError("REQUEST_INVALID")
    old_items = deepcopy(old_binding["manifest"]["items"]) if old_binding else []
    runtime = runtime_binding(config)
    with database_session(dsn) as session:
        material, existing = _check_revision_intent(
            session,
            owner,
            material_id,
            digest,
            normalization_ids,
            base_revision,
        )
        if existing is not None:
            return link_retry(session, existing)
        research = None
        if research_id is not None:
            research = session.scalar(
                select(MaterialResearch)
                .where(
                    MaterialResearch.research_id == research_id,
                    MaterialResearch.learner_id == owner,
                    MaterialResearch.material_id == material_id,
                )
                .with_for_update()
            )
            if (
                research is None
                or research.status != "ready"
                or research.base_revision != base_revision
            ):
                raise SourceError("SOURCE_NOT_READY")
            if research_token is not None and not lease_is_current(
                research, research_token
            ):
                raise SourceError("SOURCE_NOT_READY")
            selected = {c["id"]: c for c in research.candidates}
            if any(
                selected[i]["state"] not in ("ready", "failed")
                for i in research.selection
            ):
                raise SourceError("SOURCE_NOT_READY")
            remaining_normalizations = list(
                dict.fromkeys(
                    UUID(selected[i]["normalization_id"])
                    for i in research.selection
                    if selected[i]["state"] == "ready"
                    and selected[i].get("source_id")
                    not in {item["source_id"] for item in old_items}
                )
            )
            if normalization_ids != remaining_normalizations:
                raise SourceError("REQUEST_INVALID")
        items = old_items
        for identity in normalization_ids:
            job = session.scalar(
                select(SourceNormalization).where(
                    SourceNormalization.learner_id == owner,
                    SourceNormalization.material_id == material_id,
                    SourceNormalization.normalization_id == identity,
                    SourceNormalization.status == "ready",
                )
            )
            if job is None:
                raise SourceError("SOURCE_NOT_READY")
            item = _descriptor(session, job)
            if any(old["original_sha256"] == item["original_sha256"] for old in items):
                raise SourceError("DUPLICATE_SOURCE")
            items.append(item)
        manifest = {"schema": "source-set/v2", "items": items}
        manifest_hash = canonical_sha256(manifest)
        source_set = session.scalar(
            select(MaterialSourceSet).where(
                MaterialSourceSet.learner_id == owner,
                MaterialSourceSet.material_id == material_id,
                MaterialSourceSet.digest == manifest_hash,
            )
        )
        now = datetime.now(UTC)
        if source_set is None:
            source_set = MaterialSourceSet(
                source_set_id=uuid4(),
                learner_id=owner,
                material_id=material_id,
                manifest=manifest,
                digest=manifest_hash,
                created_at=now,
            )
            session.add(source_set)
            session.flush()
            for ordinal, item in enumerate(items, 1):
                session.add(
                    MaterialSourceSetItem(
                        source_set_id=source_set.source_set_id,
                        ordinal=ordinal,
                        learner_id=owner,
                        material_id=material_id,
                        source_id=UUID(item["source_id"]),
                        normalization_id=UUID(item["normalization_id"]),
                    )
                )
            session.flush()
        pages = []
        for item in items:
            for normalized_page in selected_pages(item):
                pages.append(
                    {
                        "page": len(pages) + 1,
                        "source_id": item["source_id"],
                        "normalized_page": normalized_page,
                    }
                )
        bundle = {
            "schema": "bundle-manifest/v2",
            "source_set_digest": manifest_hash,
            "processing_policy": "source-boundary-incremental/v1",
            "pages": pages,
            "source_names": [item["original_name"] for item in items],
        }
        # 選段展開會帶入相鄰原文；把已確認的學習範圍一併封存，避免它們變成額外必學主題。
        learning_scopes = (
            deepcopy(old_binding["bundle"].get("learning_scopes", {}))
            if old_binding
            else {}
        )
        added_sources = {
            item["source_id"]
            for item in items
            if UUID(item["normalization_id"]) in normalization_ids
        }
        for work, topic in session.execute(
            select(MaterialResearch, TopicScope)
            .join(TopicScope, MaterialResearch.topic_id == TopicScope.topic_id)
            .where(
                MaterialResearch.learner_id == owner,
                MaterialResearch.material_id == material_id,
                TopicScope.learner_id == owner,
                TopicScope.status == "approved",
            )
        ):
            for candidate in work.candidates:
                if candidate.get("source_id") in added_sources:
                    learning_scopes[candidate["source_id"]] = deepcopy(topic.proposal)
        if learning_scopes:
            bundle["learning_scopes"] = learning_scopes
        row = MaterialProcessingRun(
            run_id=uuid4(),
            learner_id=owner,
            material_id=material_id,
            source_artifact_id=UUID(items[0]["normalized_artifact_id"]),
            input_source_set_id=source_set.source_set_id,
            base_revision=base_revision,
            bundle_manifest=bundle,
            bundle_manifest_sha256=canonical_sha256(bundle),
            idempotency_key_sha256=digest,
            request_fingerprint=_fingerprint(
                material_id, normalization_ids, runtime, base_revision
            ),
            maintenance={"mode": "demo-full-reanalysis/v1", "limit": 300, "reserved_calls": 0} if full_reanalysis else None,
            runtime_binding=runtime,
            runtime_lock_document=deepcopy(config["runtime_lock"]),
            status="pending",
            progress_stage="queued",
            completed_pages=0,
            total_pages=None,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.flush()
        if research is not None:
            research.run_id = row.run_id
            research.status = "submitted"
            research.lease_token = research.lease_expires_at = None
        return link_retry(session, row)
