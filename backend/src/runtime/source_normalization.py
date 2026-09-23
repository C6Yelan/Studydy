"""原檔 receipt 與 durable normalization；來源集合由明確確認的 revision 操作封存。"""
from __future__ import annotations
from datetime import UTC,datetime,timedelta
from hashlib import sha256
from pathlib import PurePath
from uuid import uuid4
from sqlalchemy import select,or_,delete
from document_normalization.converter import MAX_FILE_BYTES,MIME,NormalizationError,conversion_policy,convert
from pdf_evidence.ocr_page_evidence import canonical_bytes,canonical_sha256
from .storage.artifacts import _key_digest
from .storage.source_artifacts import write_blob,open_verified_artifact,reconcile_new_artifacts
from .storage.tables import (Learner,Material,Artifact,MaterialSource,SourceNormalization,MaterialSourceSetItem,
                             MaterialProcessingRun,database_session)

class SourceError(RuntimeError):pass

def _material(session,owner,identity):
    row=session.scalar(select(Material).where(Material.learner_id==owner,Material.material_id==identity).with_for_update())
    if row is None:raise SourceError('RESOURCE_NOT_FOUND')
    if row.discard_requested_at is not None:raise SourceError('MATERIAL_NOT_DISCARDABLE')
    return row

def _name(value):
    if not isinstance(value,str) or not value.strip() or len(value)>200 or any(ord(c)<32 or ord(c)==127 or c in '/\\' for c in value):raise SourceError('REQUEST_INVALID')
    return value

def create_draft(owner,name,key,*,dsn=None):
    name=_name(name);digest=_key_digest(key);fingerprint=bytes.fromhex(canonical_sha256({'name':name,'version':1}))
    with database_session(dsn) as session:
        session.scalar(select(Learner).where(Learner.learner_id==owner).with_for_update())
        existing=session.scalar(select(Material).where(Material.learner_id==owner,Material.upload_idempotency_key_sha256==digest))
        if existing:
            if bytes(existing.upload_request_fingerprint)!=fingerprint:raise SourceError('IDEMPOTENCY_CONFLICT')
            return existing.material_id
        row=Material(material_id=uuid4(),learner_id=owner,source_artifact_id=None,display_name=name,
                     upload_idempotency_key_sha256=digest,upload_request_fingerprint=fingerprint,created_at=datetime.now(UTC))
        session.add(row);session.flush();return row.material_id

def upload_source(owner,material_id,data,name,media,key,*,dsn=None):
    name=_name(name);extension=PurePath(name).suffix.lower()
    if MIME.get(extension)!=media:raise SourceError('UNSUPPORTED_MEDIA_TYPE')
    if not data or len(data)>MAX_FILE_BYTES:raise SourceError('MATERIAL_TOO_LARGE')
    digest=_key_digest(key)
    fingerprint=bytes.fromhex(canonical_sha256({'sha256':sha256(data).hexdigest(),'name':name,'media_type':media}))
    try:
        with database_session(dsn) as session:
            material=_material(session,owner,material_id)
            existing=session.scalar(select(MaterialSource).where(MaterialSource.learner_id==owner,MaterialSource.material_id==material_id,MaterialSource.idempotency_key_sha256==digest))
            if existing:
                if bytes(existing.request_fingerprint)!=fingerprint:raise SourceError('IDEMPOTENCY_CONFLICT')
                return existing.source_id
            if session.scalar(select(MaterialSource.source_id).join(Artifact,Artifact.artifact_id==MaterialSource.original_artifact_id)
                .where(MaterialSource.material_id==material_id,Artifact.sha256==sha256(data).digest())) is not None:
                raise SourceError('DUPLICATE_SOURCE')
            policy=conversion_policy()
            blob=write_blob(session,owner,material_id,data,'original',media);now=datetime.now(UTC);identity=uuid4()
            session.add(MaterialSource(source_id=identity,learner_id=owner,material_id=material_id,original_artifact_id=blob.artifact_id,
                         original_name=name,media_type=media,idempotency_key_sha256=digest,request_fingerprint=fingerprint,created_at=now))
            session.flush()
            session.add(SourceNormalization(normalization_id=uuid4(),learner_id=owner,material_id=material_id,source_id=identity,
                         policy=policy,status='pending',created_at=now,updated_at=now));session.flush()
        return identity
    finally:reconcile_new_artifacts(dsn=dsn)

def read_sources(owner,material_id,*,dsn=None):
    with database_session(dsn) as session:
        material=session.scalar(select(Material).where(Material.learner_id==owner,Material.material_id==material_id))
        if material is None:raise SourceError('RESOURCE_NOT_FOUND')
        from .source_revisions import current_revision
        from .storage.tables import KnowledgeStructure
        revision=current_revision(session,material)
        structure=session.scalar(select(KnowledgeStructure).where(KnowledgeStructure.learner_id==owner,KnowledgeStructure.material_id==material_id,KnowledgeStructure.structure_revision==revision)) if revision else None
        included={item['source_id'] for item in structure.document['input_binding']['manifest']['items']} if structure else set()
        rows=session.execute(select(MaterialSource,SourceNormalization).join(SourceNormalization,SourceNormalization.source_id==MaterialSource.source_id)
             .where(MaterialSource.learner_id==owner,MaterialSource.material_id==material_id).distinct(MaterialSource.source_id).order_by(MaterialSource.source_id,SourceNormalization.created_at.desc())).all()
        rows=sorted(rows,key=lambda pair:pair[0].created_at)
        return [{'source_id':s.source_id,'normalization_id':n.normalization_id,'original_artifact_id':s.original_artifact_id,
                 'included':str(s.source_id) in included,
                 'original_name':s.original_name,'media_type':s.media_type,'status':n.status,'error_code':n.error_code,
                 'normalized_artifact_id':n.normalized_artifact_id,'page_count':n.page_count} for s,n in rows]

def normalize_next(*,dsn=None):
    now=datetime.now(UTC)
    with database_session(dsn) as session:
        job=session.scalar(select(SourceNormalization).join(Material,Material.material_id==SourceNormalization.material_id)
            .where(Material.discard_requested_at.is_(None),or_(SourceNormalization.status=='pending',
                (SourceNormalization.status=='running') & (SourceNormalization.lease_expires_at<now)))
            .order_by(SourceNormalization.created_at).with_for_update(of=SourceNormalization,skip_locked=True).limit(1))
        if job is None:return False
        if job.attempt>=3:
            job.status='failed';job.error_code='NORMALIZATION_RETRY_EXHAUSTED';job.lease_token=None;job.lease_expires_at=None;job.updated_at=now
            return True
        token=uuid4();job.status='running';job.attempt+=1;job.lease_token=token;job.lease_expires_at=now+timedelta(seconds=120);job.updated_at=now
        source=session.get(MaterialSource,job.source_id)
        claim=(job.normalization_id,job.learner_id,job.material_id,source.original_artifact_id,source.original_name,source.media_type,job.policy)
    identity,owner,material_id,original_id,name,media,policy=claim
    try:
        with open_verified_artifact(owner,original_id,dsn=dsn) as stream:data=stream.file.read()
        pdf,mapping=convert(data,PurePath(name).suffix.lower(),media,policy)
        with database_session(dsn) as session:
            material=_material(session,owner,material_id)
            job=session.scalar(select(SourceNormalization).where(SourceNormalization.normalization_id==identity).with_for_update())
            if job is None or job.lease_token!=token:return True
            normalized=write_blob(session,owner,material_id,pdf,'normalized_pdf','application/pdf')
            metadata=write_blob(session,owner,material_id,canonical_bytes(mapping),'source_mapping','application/json')
            job.normalized_artifact_id=normalized.artifact_id;job.mapping_artifact_id=metadata.artifact_id
            job.page_count=mapping['page_count'];job.status='ready';job.lease_token=None;job.lease_expires_at=None;job.updated_at=datetime.now(UTC)
            # 保存教材代表 PDF；完整來源與頁碼由 SourceSet 綁定。
            if material.source_artifact_id is None:
                material.source_artifact_id=normalized.artifact_id
    except Exception as error:
        with database_session(dsn) as session:
            job=session.scalar(select(SourceNormalization).where(SourceNormalization.normalization_id==identity).with_for_update())
            if job and job.lease_token==token:
                job.status='failed';job.error_code=str(error) if isinstance(error,(NormalizationError,SourceError)) else 'NORMALIZATION_FAILED'
                job.lease_token=None;job.lease_expires_at=None;job.updated_at=datetime.now(UTC)
    finally:reconcile_new_artifacts(dsn=dsn)
    return True

def retry_normalization(owner,material_id,identity,*,dsn=None):
    with database_session(dsn) as session:
        _material(session,owner,material_id)
        job=session.scalar(select(SourceNormalization).where(SourceNormalization.learner_id==owner,SourceNormalization.material_id==material_id,SourceNormalization.normalization_id==identity).with_for_update())
        if job is None:raise SourceError('RESOURCE_NOT_FOUND')
        if job.status=='failed':
            policy=conversion_policy();now=datetime.now(UTC)
            if job.policy!=policy:
                session.add(SourceNormalization(normalization_id=uuid4(),learner_id=owner,material_id=material_id,source_id=job.source_id,
                            policy=policy,status='pending',created_at=now,updated_at=now))
            else:
                job.status='pending';job.error_code=None;job.attempt=0;job.updated_at=now


def remove_staged_source(owner,material_id,source_id,*,dsn=None):
    from .storage.artifacts import quarantine_source_pdf,reconcile_discarded_sources
    artifacts=[]
    try:
        with database_session(dsn) as session:
            material=_material(session,owner,material_id)
            source=session.scalar(select(MaterialSource).where(MaterialSource.learner_id==owner,
                MaterialSource.material_id==material_id,MaterialSource.source_id==source_id).with_for_update())
            if source is None:return
            if session.scalar(select(MaterialSourceSetItem.source_set_id).where(MaterialSourceSetItem.source_id==source_id).limit(1)):
                raise SourceError('SOURCE_IN_USE')
            jobs=session.scalars(select(SourceNormalization).where(SourceNormalization.source_id==source_id).with_for_update()).all()
            if any(job.status=='running' for job in jobs):raise SourceError('SOURCE_BUSY')
            artifacts=list({source.original_artifact_id,*[identity for job in jobs for identity in (job.normalized_artifact_id,job.mapping_artifact_id) if identity]})
            if session.scalar(select(MaterialProcessingRun.run_id).where(MaterialProcessingRun.source_artifact_id.in_(artifacts)).limit(1)):
                raise SourceError('SOURCE_IN_USE')
            for identity in artifacts:quarantine_source_pdf(session,identity)
            if material.source_artifact_id in artifacts:
                material.source_artifact_id=None;session.flush()
            session.execute(delete(SourceNormalization).where(SourceNormalization.source_id==source_id))
            session.delete(source);session.flush()
            session.execute(delete(Artifact).where(Artifact.learner_id==owner,Artifact.material_id==material_id,Artifact.artifact_id.in_(artifacts)))
    finally:
        for identity in artifacts:reconcile_discarded_sources(dsn=dsn,artifact_id=identity)
