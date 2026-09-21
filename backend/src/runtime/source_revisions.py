"""追加來源的短交易：封存有序集合、保存重播意圖；PDF 各自保存。"""
from copy import deepcopy
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pymupdf
from sqlalchemy import select

from pdf_evidence.ocr_page_evidence import canonical_bytes, canonical_sha256
from .source_normalization import SourceError, _material
from .storage.artifacts import _key_digest
from .storage.source_artifacts import open_verified_artifact, write_blob, reconcile_new_artifacts
from .storage.tables import (Artifact, KnowledgeStructure, MaterialProcessingRun, MaterialSource,
    MaterialSourceSet, MaterialSourceSetItem, SourceNormalization, database_session)


def current_revision(session, material):
    if material.head_revision:
        return material.head_revision
    # 已保存單 PDF 的 exact run 仍是目前資料，首次追加時才接到共用來源集合。
    return session.scalar(select(KnowledgeStructure.structure_revision).join(MaterialProcessingRun,
        KnowledgeStructure.run_id == MaterialProcessingRun.run_id).where(
        KnowledgeStructure.learner_id == material.learner_id, KnowledgeStructure.material_id == material.material_id,
        MaterialProcessingRun.status.in_(("succeeded", "partial")),
        MaterialProcessingRun.base_revision.is_(None)).order_by(KnowledgeStructure.created_at.desc()).limit(1))


def _descriptor(session, job):
    source = session.get(MaterialSource, job.source_id)
    original = session.get(Artifact, source.original_artifact_id)
    normalized = session.get(Artifact, job.normalized_artifact_id)
    mapping = session.get(Artifact, job.mapping_artifact_id) if job.mapping_artifact_id else None
    if mapping is None or job.page_count is None:
        raise SourceError("SOURCE_NOT_READY")
    return {
        "source_id": str(source.source_id), "normalization_id": str(job.normalization_id),
        "original_artifact_id": str(original.artifact_id), "original_sha256": bytes(original.sha256).hex(),
        "normalized_artifact_id": str(normalized.artifact_id), "normalized_sha256": bytes(normalized.sha256).hex(),
        "mapping_artifact_id": str(mapping.artifact_id), "mapping_sha256": bytes(mapping.sha256).hex(),
        "media_type": source.media_type, "original_name": source.original_name,
        "policy_sha256": canonical_sha256(job.policy), "page_count": job.page_count,
    }


def _pdf_identity(owner, material_id, artifact_id, *, dsn):
    """為實際存在的單 PDF 補完整 descriptor；不改舊 ready row／KS／答案。"""
    with open_verified_artifact(owner, artifact_id, dsn=dsn) as stream:
        with pymupdf.open(stream=stream.file.read(), filetype="pdf") as pdf:
            page_count = len(pdf)
        digest = stream.sha256
    mapping = {"schema": "source-mapping/v1", "format": "pdf", "original_sha256": digest,
        "normalized_sha256": digest, "page_count": page_count,
        "records": [{"normalized_page": n, "origin_locator": {"original_page": n}, "accuracy": "exact"}
                    for n in range(1, page_count + 1)]}
    with database_session(dsn) as session:
        _material(session, owner, material_id)
        source = session.scalar(select(MaterialSource).where(MaterialSource.learner_id == owner,
            MaterialSource.material_id == material_id, MaterialSource.original_artifact_id == artifact_id))
        if source is None:
            raise SourceError("SOURCE_BINDING_INVALID")
        ready = session.scalar(select(SourceNormalization).where(SourceNormalization.source_id == source.source_id,
            SourceNormalization.status == "ready", SourceNormalization.mapping_artifact_id.is_not(None)))
        if ready is None:
            metadata = write_blob(session, owner, material_id, canonical_bytes(mapping), "source_mapping", "application/json")
            now = datetime.now(UTC)
            ready = SourceNormalization(normalization_id=uuid4(), learner_id=owner, material_id=material_id,
                source_id=source.source_id, policy={"schema": "normalization-policy/v1", "renderer": "pdf-identity"},
                status="ready", normalized_artifact_id=artifact_id, mapping_artifact_id=metadata.artifact_id,
                page_count=page_count, created_at=now, updated_at=now)
            session.add(ready)
            session.flush()
        return _descriptor(session, ready)


def _fingerprint(material_id, normalization_ids, runtime, base_revision):
    value = {"material_id": str(material_id), "normalizations": [str(x) for x in normalization_ids], "runtime": runtime}
    if base_revision is not None:
        value["base_revision"] = base_revision
    return bytes.fromhex(canonical_sha256(value))


def retry_revision(owner,run_id,key,config,*,dsn=None):
    """重試沿用原封存輸入，不能從目前 staged 清單猜測來源或順序。"""
    with database_session(dsn) as session:
        run=session.scalar(select(MaterialProcessingRun).where(MaterialProcessingRun.learner_id==owner,
            MaterialProcessingRun.run_id==run_id))
        if run is None:raise SourceError('RESOURCE_NOT_FOUND')
        if run.status!='failed' or run.input_source_set_id is None:raise SourceError('REQUEST_INVALID')
        _material(session,owner,run.material_id)
        source_set=session.get(MaterialSourceSet,run.input_source_set_id)
        prefix=0
        if run.base_revision:
            base=session.scalar(select(MaterialProcessingRun).where(MaterialProcessingRun.learner_id==owner,
                MaterialProcessingRun.material_id==run.material_id,
                MaterialProcessingRun.output_binding['knowledge_structure_revision'].astext==run.base_revision))
            if base is None:raise SourceError('REVISION_CONFLICT')
            base_set=session.get(MaterialSourceSet,base.input_source_set_id) if base.input_source_set_id else None
            prefix=len(base_set.manifest['items']) if base_set else 1
        additions=[UUID(item['normalization_id']) for item in source_set.manifest['items'][prefix:]]
        material_id,base_revision=run.material_id,run.base_revision
    return create_revision(owner,material_id,additions,key,config,base_revision=base_revision,dsn=dsn)


def create_revision(owner, material_id, normalization_ids, key, config, *, base_revision=None, dsn=None):
    from .material_processing import runtime_binding, _row
    # 空追加 + 明確現行版本代表只檢核已保存分析；來源集合維持完全相同。
    if (not normalization_ids and base_revision is None) or len(set(normalization_ids)) != len(normalization_ids):
        raise SourceError("REQUEST_INVALID")
    if not normalization_ids and 'material_review' not in config['runtime_lock']:
        raise SourceError("REQUEST_INVALID")
    digest = _key_digest(key)
    try:
        # 先查已保存意圖：head 已切換或目前環境設定改變，不使合法重播失效。
        with database_session(dsn) as session:
            material = _material(session, owner, material_id)
            existing = session.scalar(select(MaterialProcessingRun).where(MaterialProcessingRun.learner_id == owner,
                MaterialProcessingRun.idempotency_key_sha256 == digest))
            if existing is not None:
                if bytes(existing.request_fingerprint) != _fingerprint(material_id, normalization_ids, existing.runtime_binding, base_revision):
                    raise SourceError("IDEMPOTENCY_CONFLICT")
                return _row(existing)
            if current_revision(session, material) != base_revision:
                raise SourceError("REVISION_CONFLICT")
            if session.scalar(select(MaterialProcessingRun.run_id).where(MaterialProcessingRun.material_id == material_id,
                    MaterialProcessingRun.status.in_(("pending", "running")))):
                raise SourceError("REVISION_IN_PROGRESS")
            before = session.scalar(select(KnowledgeStructure).where(KnowledgeStructure.learner_id == owner,
                KnowledgeStructure.material_id == material_id, KnowledgeStructure.structure_revision == base_revision)) if base_revision else None
            old_binding = deepcopy(before.document.get("input_binding")) if before else None
            if not normalization_ids and (not old_binding or old_binding['schema'] != 'structure-input-binding/v2'):
                raise SourceError("REQUEST_INVALID")
            old_run = session.get(MaterialProcessingRun, before.run_id) if before else None
            original_pdf = old_run.source_artifact_id if old_run and old_binding is None else None
        old_items = deepcopy(old_binding["manifest"]["items"]) if old_binding else []
        if original_pdf:
            old_items = [_pdf_identity(owner, material_id, original_pdf, dsn=dsn)]
        runtime = runtime_binding(config)
        with database_session(dsn) as session:
            material = _material(session, owner, material_id)
            existing = session.scalar(select(MaterialProcessingRun).where(MaterialProcessingRun.learner_id == owner,
                MaterialProcessingRun.idempotency_key_sha256 == digest))
            if existing:
                if bytes(existing.request_fingerprint) != _fingerprint(material_id, normalization_ids, existing.runtime_binding, base_revision):
                    raise SourceError("IDEMPOTENCY_CONFLICT")
                return _row(existing)
            if current_revision(session, material) != base_revision:
                raise SourceError("REVISION_CONFLICT")
            if session.scalar(select(MaterialProcessingRun.run_id).where(MaterialProcessingRun.material_id == material_id,
                    MaterialProcessingRun.status.in_(("pending", "running")))):
                raise SourceError("REVISION_IN_PROGRESS")
            items = old_items
            for identity in normalization_ids:
                job = session.scalar(select(SourceNormalization).where(SourceNormalization.learner_id == owner,
                    SourceNormalization.material_id == material_id, SourceNormalization.normalization_id == identity,
                    SourceNormalization.status == "ready"))
                if job is None:
                    raise SourceError("SOURCE_NOT_READY")
                item = _descriptor(session, job)
                if any(old["original_sha256"] == item["original_sha256"] for old in items):
                    raise SourceError("DUPLICATE_SOURCE")
                items.append(item)
            manifest = {"schema": "source-set/v1", "items": items}
            manifest_hash = canonical_sha256(manifest)
            source_set = session.scalar(select(MaterialSourceSet).where(MaterialSourceSet.learner_id == owner,
                MaterialSourceSet.material_id == material_id, MaterialSourceSet.digest == manifest_hash))
            now = datetime.now(UTC)
            if source_set is None:
                source_set = MaterialSourceSet(source_set_id=uuid4(), learner_id=owner, material_id=material_id,
                    manifest=manifest, digest=manifest_hash, created_at=now)
                session.add(source_set)
                session.flush()
                for ordinal, item in enumerate(items, 1):
                    session.add(MaterialSourceSetItem(source_set_id=source_set.source_set_id, ordinal=ordinal,
                        learner_id=owner, material_id=material_id, source_id=UUID(item["source_id"]),
                        normalization_id=UUID(item["normalization_id"])))
                session.flush()
            pages = []
            for item in items:
                for n in range(1, item["page_count"] + 1):
                    pages.append({"page": len(pages) + 1, "source_id": item["source_id"], "normalized_page": n})
            bundle = {"schema": "bundle-manifest/v2", "source_set_digest": manifest_hash,
                "processing_policy": "source-boundary-incremental/v1", "pages": pages,
                "source_names": [item["original_name"] for item in items]}
            if base_revision is not None and material.head_revision is None:
                material.head_revision = base_revision
            row = MaterialProcessingRun(run_id=uuid4(), learner_id=owner, material_id=material_id,
                source_artifact_id=UUID(items[0]["normalized_artifact_id"]), input_source_set_id=source_set.source_set_id,
                base_revision=base_revision, bundle_manifest=bundle, bundle_manifest_sha256=canonical_sha256(bundle),
                idempotency_key_sha256=digest,
                request_fingerprint=_fingerprint(material_id, normalization_ids, runtime, base_revision),
                runtime_binding=runtime, runtime_lock_document=deepcopy(config['runtime_lock']),
                status="pending", progress_stage="queued", completed_pages=0,
                total_pages=None, created_at=now, updated_at=now)
            session.add(row)
            session.flush()
            return _row(row)
    finally:
        reconcile_new_artifacts(dsn=dsn)
