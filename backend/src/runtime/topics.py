"""先批准學習範圍，再交接 F2；範圍提案不是知識地圖。"""
from copy import deepcopy
from datetime import UTC,datetime,timedelta
from uuid import uuid4
from sqlalchemy import or_,select
from pdf_evidence.ocr_page_evidence import canonical_sha256
from . import research
from .source_normalization import SourceError,create_draft
from .storage.artifacts import _key_digest
from .storage.tables import TopicScope,MaterialResearch,Learner,database_session
from .voice import provider


def _text(value,limit):
    if not isinstance(value,str) or not value.strip() or len(value)>limit or any(ord(c)<32 and c not in '\n\r\t' for c in value):
        raise SourceError('REQUEST_INVALID')
    return value.strip()


def proposal(value):
    if not isinstance(value,dict) or set(value)!={'title','level','goals','topics','exclude'}:raise SourceError('REQUEST_INVALID')
    result={'title':' '.join(_text(value['title'],180).split()).replace('/','／').replace(chr(92),'＼'),'level':_text(value['level'],300)}
    for field in ('goals','topics','exclude'):
        if not isinstance(value[field],list) or (field!='exclude' and not value[field]):raise SourceError('REQUEST_INVALID')
        result[field]=[_text(v,500) for v in value[field]]
    if sum(len(v) for field in ('goals','topics','exclude') for v in result[field])>12000:raise SourceError('REQUEST_INVALID')
    return result


def _owned(db,owner,identity,lock=False):
    statement=select(TopicScope).where(TopicScope.topic_id==identity,TopicScope.learner_id==owner)
    row=db.scalar(statement.with_for_update() if lock else statement)
    if not row:raise SourceError('RESOURCE_NOT_FOUND')
    return row


def _view(row):
    return {k:deepcopy(getattr(row,k)) for k in ('topic_id','request','proposal','version','status','research_id','error_code','created_at','generation_provider','approved_at')}


def create(owner,request,key,*,dsn=None):
    request=_text(request,1000);digest=_key_digest(key)
    with database_session(dsn) as db:
        db.scalar(select(Learner).where(Learner.learner_id==owner).with_for_update())
        old=db.scalar(select(TopicScope).where(TopicScope.learner_id==owner,TopicScope.idempotency_key_sha256==digest))
        if old:
            if old.request!=request:raise SourceError('IDEMPOTENCY_CONFLICT')
            return _view(old)
        row=TopicScope(topic_id=uuid4(),learner_id=owner,request=request,version=1,status='pending',
            idempotency_key_sha256=digest,created_at=datetime.now(UTC))
        db.add(row);db.flush();return _view(row)


def listing(owner,*,dsn=None):
    with database_session(dsn) as db:
        return {'topics':[_view(r) for r in db.scalars(select(TopicScope).where(TopicScope.learner_id==owner,
            TopicScope.status!='cancelled').order_by(TopicScope.created_at.desc()))]}


def read(owner,identity,*,dsn=None):
    with database_session(dsn) as db:value=_view(_owned(db,owner,identity))
    if value['research_id']:value['research']=research.read(owner,value['research_id'],dsn=dsn)
    return value


def approve(owner,identity,expected,edited,key,*,dsn=None):
    edited=proposal(edited);fingerprint=canonical_sha256(edited);digest=_key_digest(key)
    with database_session(dsn) as db:
        row=_owned(db,owner,identity,True)
        if row.status=='approved':
            if row.approved_sha256!=fingerprint or bytes(row.approval_key_sha256)!=digest:raise SourceError('TOPIC_CONFLICT')
            return _view(row)
        if row.status!='ready' or row.version!=expected:raise SourceError('TOPIC_CONFLICT')
        # 與批准同一交易建立 F2 意圖；批准前完全不存在搜尋工作。
        work=MaterialResearch(research_id=uuid4(),learner_id=owner,material_id=None,base_revision=None,
            topic_id=identity,query=row.request,mode='self-study',status='searching',candidates=[],selection=[],
            idempotency_key_sha256=_key_digest('topic:'+str(identity)),created_at=datetime.now(UTC))
        db.add(work);db.flush()
        row.proposal=edited;row.approved_sha256=fingerprint;row.approval_key_sha256=digest;row.approved_at=datetime.now(UTC)
        row.research_id=work.research_id;row.status='approved';row.version+=1
        return _view(row)


def create_material(owner,identity,selected,*,dsn=None):
    with database_session(dsn) as db:
        row=_owned(db,owner,identity,True)
        if row.status!='approved' or not row.research_id:raise SourceError('TOPIC_CONFLICT')
        work=db.get(MaterialResearch,row.research_id)
        if work.material_id:
            if work.selection!=selected:raise SourceError('IDEMPOTENCY_CONFLICT')
            return {'material_id':work.material_id,'research_id':work.research_id}
        if work.status!='selecting':raise SourceError('SOURCE_NOT_READY')
        known={c['id']:c for c in work.candidates}
        if not selected or len(set(selected))!=len(selected) or any(i not in known or not known[i]['eligible'] for i in selected):
            raise SourceError('REQUEST_INVALID')
        # 回應遺失也以固定建立 key 接回同一教材，不用假 PDF 或空 KS。
        identity_material=create_draft(owner,row.proposal['title'],'topic:'+str(identity),dsn=dsn)
        work.material_id=identity_material;work.selection=list(selected);work.status='acquiring'
        work.lease_token=work.lease_expires_at=None
        return {'material_id':identity_material,'research_id':work.research_id}


def action(owner,identity,action,expected,*,dsn=None):
    with database_session(dsn) as db:
        row=_owned(db,owner,identity,True)
        if row.version!=expected:raise SourceError('TOPIC_CONFLICT')
        if action=='retry' and row.status=='failed':row.status='pending';row.error_code=None
        elif action=='cancel':
            work=db.get(MaterialResearch,row.research_id) if row.research_id else None
            if work and work.material_id:raise SourceError('TOPIC_CONFLICT')
            if work:work.status='cancelled';work.lease_token=work.lease_expires_at=None
            row.status='cancelled'
        else:raise SourceError('REQUEST_INVALID')
        row.version+=1;row.lease_token=row.lease_expires_at=None
        return _view(row)


def step(*,dsn=None):
    with database_session(dsn) as db:
        now=datetime.now(UTC)
        row=db.scalar(select(TopicScope).where(TopicScope.status=='pending',
            or_(TopicScope.lease_expires_at.is_(None),TopicScope.lease_expires_at<now)).order_by(TopicScope.created_at)
            .with_for_update(skip_locked=True).limit(1))
        if not row:return False
        token=uuid4();row.lease_token=token;row.lease_expires_at=now+timedelta(minutes=20)
        identity,request=row.topic_id,row.request
    try:
        generated=provider('/scope',{'request':request})
        result=proposal(generated['proposal']);producer=_text(generated['provider'],200);error=None
    except Exception:result=None;producer=None;error='TOPIC_PROVIDER_FAILED'
    with database_session(dsn) as db:
        row=db.scalar(select(TopicScope).where(TopicScope.topic_id==identity).with_for_update())
        if row.lease_token==token and row.status=='pending':
            row.proposal=result;row.generation_provider=producer;row.status='failed' if error else 'ready';row.error_code=error
            row.version+=1;row.lease_token=row.lease_expires_at=None
    return True
