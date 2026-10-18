"""研究期間保留已驗證的 PDF 或網頁正文文字；選取後沿用同一份內容。"""
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256

from sqlalchemy import delete, select

from .errors import SourceError
from .storage.tables import ResearchDocument, database_session


def snapshot(data, metadata):
    """收據綁定實際匯入 bytes；HTML 的原頁 hash 另留 metadata。"""
    digest=sha256(data).hexdigest()
    pdf=data.startswith(b'%PDF-')
    if not data or len(data)>90*1024*1024 or (not pdf and metadata.get('format')!='html'):
        raise SourceError('RESEARCH_FORMAT_UNSUPPORTED')
    if pdf and metadata.get('original_sha256',digest)!=digest:raise SourceError('RESEARCH_SOURCE_CHANGED')
    metadata=deepcopy(metadata)
    metadata.setdefault('original_sha256',digest)
    metadata['acquired_at']=metadata.get('acquired_at') or datetime.now(UTC).isoformat()
    return {'sha256':digest,'size_bytes':len(data),'metadata':metadata}


def persist(db,research_id,candidates):
    # 呼叫者已持有研究租約與父資料鎖；原檔和收據隨這次進度一起提交。
    for candidate in candidates:
        document=candidate.pop('_acquired_document',None)
        if document is None:continue
        data,metadata=document
        receipt=snapshot(data,metadata);digest=bytes.fromhex(receipt['sha256'])
        if db.get(ResearchDocument,(research_id,digest)) is None:
            db.add(ResearchDocument(research_id=research_id,sha256=digest,size_bytes=len(data),content=data))
        candidate['_document_snapshot']=receipt


def read(research_id,receipt,expected_sha256,*,dsn=None):
    if receipt.get('sha256')!=expected_sha256:raise SourceError('RESEARCH_SNAPSHOT_UNAVAILABLE')
    try:digest=bytes.fromhex(receipt['sha256'])
    except (KeyError,TypeError,ValueError):raise SourceError('RESEARCH_SNAPSHOT_UNAVAILABLE') from None
    with database_session(dsn) as db:
        row=db.execute(select(ResearchDocument.content,ResearchDocument.size_bytes).where(
            ResearchDocument.research_id==research_id,ResearchDocument.sha256==digest)).one_or_none()
        if row is None:raise SourceError('RESEARCH_SNAPSHOT_UNAVAILABLE')
        data=bytes(row.content)
    if len(data)!=receipt.get('size_bytes') or len(data)!=row.size_bytes or sha256(data).digest()!=digest:
        raise SourceError('RESEARCH_SNAPSHOT_UNAVAILABLE')
    metadata=deepcopy(receipt['metadata'])
    media,suffix=('text/plain','.txt') if metadata.get('format')=='html' else ('application/pdf','.pdf')
    return data,media,suffix,metadata


def discard(db,research_id,*,keep_sha256=()):
    # 只移除尚未成為教材原檔的準備資料；已匯入的 Artifact 由原生命週期管理。
    statement=delete(ResearchDocument).where(ResearchDocument.research_id==research_id)
    if keep_sha256:statement=statement.where(ResearchDocument.sha256.not_in([bytes.fromhex(value) for value in keep_sha256]))
    db.execute(statement)
