"""單來源 v2 ingestion：原檔 receipt → durable normalization → frozen source set。"""
from __future__ import annotations
from datetime import UTC,datetime,timedelta
from hashlib import sha256
import json
from pathlib import PurePath
from uuid import UUID,uuid4
from sqlalchemy import select,or_,func
from document_normalization.converter import MAX_FILE_BYTES,MIME,NormalizationError,configured_python,conversion_policy,convert
from pdf_evidence.ocr_page_evidence import canonical_bytes,canonical_sha256
from .storage.artifacts import _key_digest,ArtifactError
from .storage.source_artifacts import write_blob,open_verified_artifact,reconcile_new_artifacts
from .storage.tables import (Learner,Material,Artifact,MaterialSource,SourceNormalization,MaterialSourceSet,MaterialSourceSetItem,
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
    name=_name(name);digest=_key_digest(key);fingerprint=bytes.fromhex(canonical_sha256({'name':name,'version':2}))
    with database_session(dsn) as session:
        session.scalar(select(Learner).where(Learner.learner_id==owner).with_for_update())
        existing=session.scalar(select(Material).where(Material.learner_id==owner,Material.upload_idempotency_key_sha256==digest))
        if existing:
            if existing.ingestion_kind!='sources-v2' or bytes(existing.upload_request_fingerprint)!=fingerprint:raise SourceError('IDEMPOTENCY_CONFLICT')
            return existing.material_id
        row=Material(material_id=uuid4(),learner_id=owner,source_artifact_id=None,ingestion_kind='sources-v2',display_name=name,
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
            if material.ingestion_kind!='sources-v2':raise SourceError('REQUEST_INVALID')
            existing=session.scalar(select(MaterialSource).where(MaterialSource.learner_id==owner,MaterialSource.material_id==material_id,MaterialSource.idempotency_key_sha256==digest))
            if existing:
                if bytes(existing.request_fingerprint)!=fingerprint:raise SourceError('IDEMPOTENCY_CONFLICT')
                return existing.source_id
            # B2-I 僅單來源；B3 才開放追加。先鎖 Material，避免同時上傳突破限制。
            if session.scalar(select(MaterialSource.source_id).where(MaterialSource.material_id==material_id)) is not None:raise SourceError('SINGLE_SOURCE_ONLY')
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
        if session.scalar(select(Material.material_id).where(Material.learner_id==owner,Material.material_id==material_id)) is None:raise SourceError('RESOURCE_NOT_FOUND')
        rows=session.execute(select(MaterialSource,SourceNormalization).join(SourceNormalization,SourceNormalization.source_id==MaterialSource.source_id)
             .where(MaterialSource.learner_id==owner,MaterialSource.material_id==material_id).distinct(MaterialSource.source_id).order_by(MaterialSource.source_id,SourceNormalization.created_at.desc())).all()
        return [{'source_id':s.source_id,'normalization_id':n.normalization_id,'original_artifact_id':s.original_artifact_id,
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
            # 相容既有單來源 library/run projection；真正 provenance 綁在 SourceSet。
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

def create_revision(owner,material_id,normalization_ids,key,config,*,dsn=None):
    from .material_processing import runtime_binding,_row
    if len(normalization_ids)!=1:raise SourceError('SINGLE_SOURCE_ONLY')
    digest=_key_digest(key)
    def fingerprint_for(runtime):
        return bytes.fromhex(canonical_sha256({'material_id':str(material_id),'normalizations':[str(x) for x in normalization_ids],'runtime':runtime}))
    with database_session(dsn) as session:
        material=_material(session,owner,material_id)
        if material.ingestion_kind!='sources-v2':raise SourceError('REQUEST_INVALID')
        existing=session.scalar(select(MaterialProcessingRun).where(MaterialProcessingRun.learner_id==owner,MaterialProcessingRun.idempotency_key_sha256==digest))
        if existing:
            if bytes(existing.request_fingerprint)!=fingerprint_for(existing.runtime_binding):raise SourceError('IDEMPOTENCY_CONFLICT')
            return _row(existing)
        runtime=runtime_binding(config);fingerprint=fingerprint_for(runtime)
        job=session.scalar(select(SourceNormalization).where(SourceNormalization.learner_id==owner,SourceNormalization.material_id==material_id,
                     SourceNormalization.normalization_id==normalization_ids[0],SourceNormalization.status=='ready'))
        if job is None:raise SourceError('SOURCE_NOT_READY')
        source=session.get(MaterialSource,job.source_id);original=session.get(Artifact,source.original_artifact_id)
        normalized=session.get(Artifact,job.normalized_artifact_id);mapping=session.get(Artifact,job.mapping_artifact_id)
        item={'source_id':str(source.source_id),'normalization_id':str(job.normalization_id),'original_artifact_id':str(original.artifact_id),
              'original_sha256':bytes(original.sha256).hex(),'normalized_artifact_id':str(normalized.artifact_id),'normalized_sha256':bytes(normalized.sha256).hex(),
              'mapping_artifact_id':str(mapping.artifact_id),'mapping_sha256':bytes(mapping.sha256).hex(),'media_type':source.media_type,
              'original_name':source.original_name,'policy_sha256':canonical_sha256(job.policy),'page_count':job.page_count}
        manifest={'schema':'source-set/v1','items':[item]};manifest_hash=canonical_sha256(manifest)
        source_set=session.scalar(select(MaterialSourceSet).where(MaterialSourceSet.learner_id==owner,MaterialSourceSet.material_id==material_id,MaterialSourceSet.digest==manifest_hash))
        now=datetime.now(UTC)
        if source_set is None:
            source_set=MaterialSourceSet(source_set_id=uuid4(),learner_id=owner,material_id=material_id,manifest=manifest,digest=manifest_hash,created_at=now)
            session.add(source_set);session.flush()
            session.add(MaterialSourceSetItem(source_set_id=source_set.source_set_id,ordinal=1,learner_id=owner,material_id=material_id,source_id=source.source_id,normalization_id=job.normalization_id));session.flush()
        # 單來源 canonical PDF 共享已驗證 normalized bytes，不為 role 重複複製。
        bundle={'schema':'bundle-manifest/v1','source_set_digest':manifest_hash,'canonical_artifact_id':str(normalized.artifact_id),
                'canonical_sha256':bytes(normalized.sha256).hex(),'pages':[{'page':n,'source_id':str(source.source_id),'normalized_page':n} for n in range(1,job.page_count+1)]}
        row=MaterialProcessingRun(run_id=uuid4(),learner_id=owner,material_id=material_id,source_artifact_id=normalized.artifact_id,
             idempotency_key_sha256=digest,request_fingerprint=fingerprint,runtime_binding=runtime,input_source_set_id=source_set.source_set_id,
             bundle_manifest=bundle,bundle_manifest_sha256=canonical_sha256(bundle),status='pending',progress_stage='queued',completed_pages=0,total_pages=None,
             created_at=now,updated_at=now)
        session.add(row);session.flush();return _row(row)
