"""保存原始來源與轉檔工作，由 revision 操作封存來源集合。"""

from __future__ import annotations

from .errors import SourceError

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import PurePath
from uuid import uuid4

from sqlalchemy import delete, or_, select

from document_normalization.converter import (
    MAX_AUDIO_BYTES,
    VIDEO_AUDIO_MIME,
    VIDEO_MIME,
    YOUTUBE_MIME,
    NormalizationError,
    conversion_policy,
    convert,
    is_media,
    media_policy,
)
from content_identity import canonical_bytes, canonical_sha256
from .storage.artifacts import (
    _key_digest,
    open_verified_artifact,
    reconcile_new_artifacts,
    write_blob,
)
from .storage.tables import (
    Artifact,
    Learner,
    Material,
    MaterialProcessingRun,
    MaterialSource,
    MaterialSourceSetItem,
    SourceNormalization,
)
from .generation_jobs import lease_is_current
from .storage.database import database_session
from .storage.materials import require_source_material


def _name(value):
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 200
        or any(ord(char) < 32 or ord(char) == 127 or char in "/\\" for char in value)
    ):
        raise SourceError("REQUEST_INVALID")
    return value


def _policy(media):
    """文件沿用原轉檔政策；圖片組、音訊、影片與 YouTube 使用媒體政策。"""
    return media_policy() if is_media(media) else conversion_policy()


def create_draft(owner, name, key, *, dsn=None):
    name = _name(name)
    digest = _key_digest(key)
    fingerprint = bytes.fromhex(canonical_sha256({"name": name, "version": 1}))
    with database_session(dsn) as session:
        session.scalar(
            select(Learner).where(Learner.learner_id == owner).with_for_update()
        )
        existing = session.scalar(
            select(Material).where(
                Material.learner_id == owner,
                Material.upload_idempotency_key_sha256 == digest,
            )
        )
        if existing:
            if bytes(existing.upload_request_fingerprint) != fingerprint:
                raise SourceError("IDEMPOTENCY_CONFLICT")
            return existing.material_id
        row = Material(
            material_id=uuid4(),
            learner_id=owner,
            source_artifact_id=None,
            display_name=name,
            upload_idempotency_key_sha256=digest,
            upload_request_fingerprint=fingerprint,
            created_at=datetime.now(UTC),
        )
        session.add(row)
        session.flush()
        return row.material_id


def upload_source(
    owner,
    material_id,
    data,
    name,
    media,
    key,
    *,
    dsn=None,
    research_attempt=None,
    analysis_scope=None,
):
    from .media_sources import upload_limit

    name = _name(name)
    if media == YOUTUBE_MIME:
        # YouTube 參照只由連結入口建立，內容是影片 ID 與標題，不是使用者檔案。
        if PurePath(name).suffix != ".youtube" or research_attempt is not None:
            raise SourceError("UNSUPPORTED_MEDIA_TYPE")
        limit = 65536
    else:
        # 影片在此之前已抽成音軌，保存上限與音訊相同。
        limit = min(upload_limit(name, media), MAX_AUDIO_BYTES)
    if not data or len(data) > limit:
        raise SourceError("MATERIAL_TOO_LARGE")
    if analysis_scope is not None and (
        research_attempt is None
        or analysis_scope.get("source_sha256") != sha256(data).hexdigest()
        or analysis_scope.get("schema") != "source-analysis-scope/v1"
    ):
        raise SourceError("SOURCE_SCOPE_INVALID")
    digest = _key_digest(key)
    fingerprint = bytes.fromhex(
        canonical_sha256(
            {
                "sha256": sha256(data).hexdigest(),
                "name": name,
                "media_type": media,
                "analysis_scope": analysis_scope,
            }
        )
    )
    try:
        with database_session(dsn) as session:
            material = require_source_material(session, owner, material_id)
            if research_attempt is not None:
                from .storage.tables import MaterialResearch

                research = session.scalar(
                    select(MaterialResearch)
                    .where(
                        MaterialResearch.research_id == research_attempt[0],
                        MaterialResearch.material_id == material_id,
                        MaterialResearch.learner_id == owner,
                    )
                    .with_for_update()
                )
                if (
                    research is None
                    or research.status != "acquiring"
                    or not lease_is_current(research, research_attempt[1])
                ):
                    raise SourceError("REQUEST_INVALID")
            existing = session.scalar(
                select(MaterialSource).where(
                    MaterialSource.learner_id == owner,
                    MaterialSource.material_id == material_id,
                    MaterialSource.idempotency_key_sha256 == digest,
                )
            )
            if existing:
                if existing.removed_at is not None:
                    raise SourceError("RESOURCE_NOT_FOUND")
                if bytes(existing.request_fingerprint) != fingerprint:
                    raise SourceError("IDEMPOTENCY_CONFLICT")
                return existing.source_id
            duplicate = session.scalar(
                select(MaterialSource)
                .join(
                    Artifact,
                    Artifact.artifact_id == MaterialSource.original_artifact_id,
                )
                .where(
                    MaterialSource.material_id == material_id,
                    MaterialSource.removed_at.is_(None),
                    Artifact.sha256 == sha256(data).digest(),
                )
            )
            if duplicate is not None:
                if research_attempt is not None:
                    from .storage.tables import KnowledgeStructure

                    head = (
                        session.get(
                            KnowledgeStructure,
                            (owner, material_id, material.head_revision),
                        )
                        if material.head_revision
                        else None
                    )
                    if material.head_revision and head is None:
                        raise SourceError("SOURCE_NOT_READY")
                    included = (
                        {
                            item["source_id"]
                            for item in head.document["input_binding"]["manifest"][
                                "items"
                            ]
                        }
                        if head
                        else set()
                    )
                    # 只有相同分析範圍才能重用 staged 原檔；來源的範圍與已發布引用均不可被新研究覆寫。
                    if (
                        str(duplicate.source_id) not in included
                        and duplicate.analysis_scope == analysis_scope
                    ):
                        return duplicate.source_id
                raise SourceError("DUPLICATE_SOURCE")
            policy = _policy(media)
            blob = write_blob(
                session,
                owner,
                material_id,
                data,
                "original",
                VIDEO_AUDIO_MIME if media in VIDEO_MIME.values() else media,
            )
            now = datetime.now(UTC)
            identity = uuid4()
            session.add(
                MaterialSource(
                    source_id=identity,
                    learner_id=owner,
                    material_id=material_id,
                    original_artifact_id=blob.artifact_id,
                    original_name=name,
                    media_type=media,
                    analysis_scope=analysis_scope,
                    idempotency_key_sha256=digest,
                    request_fingerprint=fingerprint,
                    created_at=now,
                )
            )
            session.flush()
            session.add(
                SourceNormalization(
                    normalization_id=uuid4(),
                    learner_id=owner,
                    material_id=material_id,
                    source_id=identity,
                    policy=policy,
                    status="pending",
                    created_at=now,
                    updated_at=now,
                )
            )
            session.flush()
        return identity
    finally:
        reconcile_new_artifacts(dsn=dsn)


def read_sources(owner, material_id, *, dsn=None):
    with database_session(dsn) as session:
        material = session.scalar(
            select(Material).where(
                Material.learner_id == owner,
                Material.material_id == material_id,
            )
        )
        if material is None:
            raise SourceError("RESOURCE_NOT_FOUND")
        from .storage.tables import KnowledgeStructure, MaterialResearch

        revision = material.head_revision
        structure = (
            session.execute(
                select(
                    KnowledgeStructure.document["input_binding"].label("input_binding")
                ).where(
                    KnowledgeStructure.learner_id == owner,
                    KnowledgeStructure.material_id == material_id,
                    KnowledgeStructure.structure_revision == revision,
                )
            ).first()
            if revision
            else None
        )
        included = (
            {
                item["source_id"]
                for item in structure.input_binding["manifest"]["items"]
            }
            if structure
            else set()
        )
        rows = session.execute(
            select(MaterialSource, SourceNormalization)
            .join(
                SourceNormalization,
                SourceNormalization.source_id == MaterialSource.source_id,
            )
            .where(
                MaterialSource.learner_id == owner,
                MaterialSource.material_id == material_id,
                MaterialSource.removed_at.is_(None),
            )
            .order_by(MaterialSource.created_at, MaterialSource.source_id)
        ).all()
        # 研究候選保存 canonical source_id；僅投影來源分類，不建立第二份來源。
        research_sources = {
            candidate["source_id"]
            for candidates in session.scalars(
                select(MaterialResearch.candidates).where(
                    MaterialResearch.learner_id == owner,
                    MaterialResearch.material_id == material_id,
                )
            )
            for candidate in candidates
            if candidate.get("source_id")
        }
        sources = [
            {
                "origin": "research"
                if str(source.source_id) in research_sources
                else "upload",
                "source_id": source.source_id,
                "normalization_id": job.normalization_id,
                "original_artifact_id": source.original_artifact_id,
                "included": str(source.source_id) in included,
                "original_name": source.original_name,
                "media_type": source.media_type,
                "status": job.status,
                "error_code": job.error_code,
                "normalized_artifact_id": job.normalized_artifact_id,
                "page_count": job.page_count,
                "analysis_page_count": len(job.analysis_scope["pages"])
                if job.analysis_scope
                else job.page_count,
                "analysis_mode": "selected" if source.analysis_scope else "full",
            }
            for source, job in rows
        ]
        return {
            "schema": "material-sources/v1",
            "material_id": material_id,
            "sources": sources,
            "discard_requested": material.discard_requested_at is not None,
        }


def normalize_next(*, dsn=None):
    now = datetime.now(UTC)
    with database_session(dsn) as session:
        job = session.scalar(
            select(SourceNormalization)
            .join(Material, Material.material_id == SourceNormalization.material_id)
            .where(
                Material.discard_requested_at.is_(None),
                or_(
                    SourceNormalization.status == "pending",
                    (SourceNormalization.status == "running")
                    & (SourceNormalization.lease_expires_at < now),
                ),
            )
            .order_by(SourceNormalization.created_at)
            .with_for_update(of=SourceNormalization, skip_locked=True)
            .limit(1)
        )
        if job is None:
            return False
        if job.attempt >= 3:
            job.status = "failed"
            job.error_code = "NORMALIZATION_RETRY_EXHAUSTED"
            job.lease_token = job.lease_expires_at = None
            job.updated_at = now
            return True
        token = uuid4()
        job.status = "running"
        job.attempt += 1
        job.lease_token = token
        job.lease_expires_at = now + timedelta(seconds=120)
        job.updated_at = now
        source = session.get(MaterialSource, job.source_id)
        claim = (
            job.normalization_id,
            job.learner_id,
            job.material_id,
            source.original_artifact_id,
            source.original_name,
            source.media_type,
            job.policy,
            source.analysis_scope,
        )
    identity, owner, material_id, original_id, name, media, policy, selected_scope = (
        claim
    )
    try:
        from .generation_jobs import check_job

        if is_media(media):
            from .media_sources import normalize_media

            pdf, mapping = normalize_media(
                identity, token, owner, original_id, name, media, policy, dsn=dsn
            )
        else:
            with open_verified_artifact(owner, original_id, dsn=dsn) as stream:
                data = stream.file.read()
            pdf, mapping = convert(
                data,
                PurePath(name).suffix.lower(),
                media,
                policy,
                cancellation_check=lambda: check_job(
                    SourceNormalization, identity, token, dsn=dsn
                ),
            )
        from pdf_evidence.analysis_scope import locate_scope

        # 範圍定位只讀轉檔結果，不使用模型，也不改完整 PDF。
        if selected_scope is not None:
            try:
                selected_scope = locate_scope(pdf, selected_scope)
            except ValueError as error:
                raise SourceError(str(error)) from None
        with database_session(dsn) as session:
            material = require_source_material(session, owner, material_id)
            job = session.scalar(
                select(SourceNormalization)
                .where(
                    SourceNormalization.normalization_id == identity,
                )
                .with_for_update()
            )
            if (
                job is None
                or not lease_is_current(job, token)
                or job.status != "running"
            ):
                return True
            normalized = write_blob(
                session, owner, material_id, pdf, "normalized_pdf", "application/pdf"
            )
            metadata = write_blob(
                session,
                owner,
                material_id,
                canonical_bytes(mapping),
                "source_mapping",
                "application/json",
            )
            job.normalized_artifact_id = normalized.artifact_id
            job.mapping_artifact_id = metadata.artifact_id
            job.page_count = mapping["page_count"]
            job.analysis_scope = selected_scope
            job.status = "ready"
            job.lease_token = job.lease_expires_at = None
            job.updated_at = datetime.now(UTC)
            # 保存教材代表 PDF；完整來源與頁碼由 SourceSet 綁定。
            if material.source_artifact_id is None:
                material.source_artifact_id = normalized.artifact_id
    except Exception as error:
        with database_session(dsn) as session:
            job = session.scalar(
                select(SourceNormalization)
                .where(
                    SourceNormalization.normalization_id == identity,
                )
                .with_for_update()
            )
            if (
                job
                and lease_is_current(job, token)
                and job.status == "running"
            ):
                job.status = "failed"
                job.error_code = (
                    str(error)
                    if isinstance(error, (NormalizationError, SourceError))
                    else "NORMALIZATION_FAILED"
                )
                job.lease_token = job.lease_expires_at = None
                job.updated_at = datetime.now(UTC)
    finally:
        reconcile_new_artifacts(dsn=dsn)
    return True


def retry_normalization(owner, material_id, identity, *, dsn=None):
    with database_session(dsn) as session:
        require_source_material(session, owner, material_id)
        job = session.scalar(
            select(SourceNormalization)
            .where(
                SourceNormalization.learner_id == owner,
                SourceNormalization.material_id == material_id,
                SourceNormalization.normalization_id == identity,
            )
            .with_for_update()
        )
        if job is None:
            raise SourceError("RESOURCE_NOT_FOUND")
        source = session.get(MaterialSource, job.source_id)
        if source.removed_at is not None:
            raise SourceError("RESOURCE_NOT_FOUND")
        if job.status == "failed":
            # 重試以現行轉檔政策處理原檔；已完成的來源快照保持不變。
            job.policy = _policy(source.media_type)
            job.status = "pending"
            job.error_code = None
            job.attempt = 0
            job.updated_at = datetime.now(UTC)


def remove_staged_source(owner, material_id, source_id, *, dsn=None):
    from .storage.artifacts import quarantine_artifact, reconcile_discarded_artifacts

    artifacts = []
    try:
        with database_session(dsn) as session:
            material = require_source_material(session, owner, material_id)
            source = session.scalar(
                select(MaterialSource)
                .where(
                    MaterialSource.learner_id == owner,
                    MaterialSource.material_id == material_id,
                    MaterialSource.source_id == source_id,
                )
                .with_for_update()
            )
            if source is None or source.removed_at is not None:
                return
            jobs = session.scalars(
                select(SourceNormalization)
                .where(
                    SourceNormalization.source_id == source_id,
                )
                .with_for_update()
            ).all()
            if any(job.status == "running" for job in jobs):
                raise SourceError("SOURCE_BUSY")
            artifacts = list(
                {
                    source.original_artifact_id,
                    *[
                        identity
                        for job in jobs
                        for identity in (
                            job.normalized_artifact_id,
                            job.mapping_artifact_id,
                        )
                        if identity
                    ],
                }
            )
            source_sets = select(MaterialSourceSetItem.source_set_id).where(
                MaterialSourceSetItem.source_id == source_id,
            )
            referenced = session.scalars(
                select(MaterialProcessingRun).where(
                    MaterialProcessingRun.material_id == material_id,
                    or_(
                        MaterialProcessingRun.input_source_set_id.in_(source_sets),
                        MaterialProcessingRun.source_artifact_id.in_(artifacts),
                    ),
                )
            ).all()
            if referenced:
                if any(run.status not in ("failed", "cancelled") for run in referenced):
                    raise SourceError("SOURCE_IN_USE")
                # 失敗工作仍可回查原檔；移除只影響後續來源選擇，不改封存身分。
                source.removed_at = datetime.now(UTC)
                if material.source_artifact_id in artifacts:
                    material.source_artifact_id = None
                return
            for identity in artifacts:
                quarantine_artifact(session, identity)
            if material.source_artifact_id in artifacts:
                material.source_artifact_id = None
                session.flush()
            session.execute(
                delete(SourceNormalization).where(
                    SourceNormalization.source_id == source_id
                )
            )
            session.delete(source)
            session.flush()
            session.execute(
                delete(Artifact).where(
                    Artifact.learner_id == owner,
                    Artifact.material_id == material_id,
                    Artifact.artifact_id.in_(artifacts),
                )
            )
    finally:
        for identity in artifacts:
            reconcile_discarded_artifacts(dsn=dsn, artifact_id=identity)
