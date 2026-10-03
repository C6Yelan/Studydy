"""補充研究的逐項取得與既有來源／revision 交接。"""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import UUID,uuid4
import re
from sqlalchemy import or_,select
from . import research_sources
from .card_sets import _material
from .source_normalization import SourceError,upload_source
from .source_revisions import create_revision
from .storage.artifacts import _key_digest
from .storage.tables import Material,MaterialResearch as Research,SourceNormalization,MaterialProcessingRun,database_session
from .voice import provider

ACTIVE=('searching','acquiring','normalizing')


def _owned(db,owner,identity):
    row=db.get(Research,identity)
    if not row or row.learner_id!=owner:raise SourceError('RESOURCE_NOT_FOUND')
    material=_material(db,owner,row.material_id)
    db.refresh(row)
    return row,material


def _view(row):
    value={k:deepcopy(getattr(row,k)) for k in ('research_id','material_id','base_revision','query','mode','status','candidates','selection','search_query','cursor','error_code','run_id','created_at')}
    # 下載目標與授權快照保留在後端，前端只消費來源說明與取得結果。
    for item in value['candidates']:
        item.pop('download_url',None)
    return value


def create(owner,material_id,query,mode,key,*,dsn=None):
    if not query.strip() or len(query)>1000 or mode not in ('review','self-study'):raise SourceError('REQUEST_INVALID')
    with database_session(dsn) as db:
        material=_material(db,owner,material_id)
        digest=_key_digest(key)
        old=db.scalar(select(Research).where(Research.material_id==material_id,Research.idempotency_key_sha256==digest))
        if old:
            if old.query!=query.strip() or old.mode!=mode:raise SourceError('IDEMPOTENCY_CONFLICT')
            return _view(old)
        if not material.head_revision:raise SourceError('SOURCE_NOT_READY')
        row=Research(research_id=uuid4(),learner_id=owner,material_id=material_id,base_revision=material.head_revision,
            query=query.strip(),mode=mode,status='searching',candidates=[],selection=[],idempotency_key_sha256=digest,created_at=datetime.now(UTC))
        db.add(row);db.flush();return _view(row)


def listing(owner,material_id,*,dsn=None):
    with database_session(dsn) as db:
        _material(db,owner,material_id)
        return {'researches':[_view(r) for r in db.scalars(select(Research).where(Research.material_id==material_id).order_by(Research.created_at.desc()))]}


def read(owner,identity,*,dsn=None):
    with database_session(dsn) as db:
        row,material=_owned(db,owner,identity)
        value=_view(row);value['is_current_revision']=row.base_revision==material.head_revision
        if row.run_id:
            run=db.get(MaterialProcessingRun,row.run_id)
            value['run']={'run_id':str(run.run_id),'status':run.status,'error_code':run.error_code,
                'output_binding':run.output_binding} if run else None
        return value


def action(owner,identity,action,selected=None,*,dsn=None):
    with database_session(dsn) as db:
        row,_=_owned(db,owner,identity)
        if action=='more':
            if row.status!='selecting' or not row.cursor:raise SourceError('REQUEST_INVALID')
            row.status='searching'
        elif action=='acquire':
            if row.status=='acquiring' and row.selection==selected:return _view(row)
            if row.status not in ('selecting','ready','failed'):raise SourceError('REQUEST_INVALID')
            known={c['id']:c for c in row.candidates}
            if not selected or len(set(selected))!=len(selected) or any(i not in known or not known[i]['eligible'] for i in selected):
                raise SourceError('REQUEST_INVALID')
            row.selection=list(selected)
            candidates=deepcopy(row.candidates)
            for c in candidates:
                if c['id'] in selected and c['state']=='failed':c['state']='candidate';c.pop('error_code',None)
            row.candidates=candidates;row.status='acquiring'
        elif action=='cancel':
            if row.status=='submitted':raise SourceError('REQUEST_INVALID')
            row.status='cancelled';row.staged_content=None;row.staged_metadata=None
        elif action=='retry':
            if row.status not in ('failed','cancelled'):raise SourceError('REQUEST_INVALID')
            if row.selection:
                candidates=deepcopy(row.candidates)
                for c in candidates:
                    if c['id'] in row.selection and c['state']=='failed' and not c.get('source_id'):
                        c['state']='candidate';c.pop('error_code',None)
                row.candidates=candidates
            row.status='acquiring' if row.selection else 'searching'
        else:raise SourceError('REQUEST_INVALID')
        if action in ('retry','acquire') and row.selection:
            candidates=deepcopy(row.candidates)
            for c in candidates:
                if c['id'] not in row.selection or not c.get('source_id') or c['state'] not in ('failed','candidate'):
                    continue
                job=db.scalar(select(SourceNormalization).where(SourceNormalization.source_id==UUID(c['source_id'])).with_for_update())
                if job and job.status=='failed':
                    job.status='pending';job.error_code=None;job.attempt=0;job.updated_at=datetime.now(UTC)
                if job:
                    c['state']='normalizing';c.pop('error_code',None)
            row.candidates=candidates
        row.error_code=None;row.lease_token=row.lease_expires_at=None
        return _view(row)


def submit(owner,identity,config,*,dsn=None):
    with database_session(dsn) as db:
        row,_=_owned(db,owner,identity)
        if row.status=='submitted':return _view(row)
        if row.status!='ready':raise SourceError('SOURCE_NOT_READY')
        candidates={c['id']:c for c in row.candidates}
        items=[candidates[i] for i in row.selection]
        if any(c['state']!='ready' for c in items):raise SourceError('SOURCE_NOT_READY')
        material_id,base_revision=row.material_id,row.base_revision
        normalizations=[UUID(c['normalization_id']) for c in items]
    create_revision(owner,material_id,normalizations,'research:'+str(identity),config,
        base_revision=base_revision,dsn=dsn,research_id=identity)
    return read(owner,identity,dsn=dsn)


def claim(*,dsn=None):
    with database_session(dsn) as db:
        now=datetime.now(UTC)
        row=db.scalar(select(Research).join(Material,Material.material_id==Research.material_id).where(
            Research.status.in_(ACTIVE),Material.discard_requested_at.is_(None),
            or_(Research.lease_expires_at.is_(None),Research.lease_expires_at<now))
            .order_by(Research.lease_expires_at.asc().nullsfirst(),Research.created_at).with_for_update(of=Research,skip_locked=True).limit(1))
        if not row:return None
        row.lease_token=uuid4();row.lease_expires_at=now+timedelta(minutes=30)
        return {**_view(row),'owner':row.learner_id,'lease_token':row.lease_token,'candidates':deepcopy(row.candidates),
            'material_title':db.get(Material,row.material_id).display_name,
            'staged_content':bytes(row.staged_content) if row.staged_content else None,'staged_metadata':deepcopy(row.staged_metadata)}


def save(state,mutate,*,dsn=None,release=True):
    with database_session(dsn) as db:
        material=db.scalar(select(Material).where(Material.material_id==state['material_id']).with_for_update())
        if not material or material.discard_requested_at:return False
        row=db.scalar(select(Research).where(Research.research_id==state['research_id']).with_for_update())
        if not row or row.lease_token!=state['lease_token'] or row.status!=state['status']:return False
        mutate(row)
        if release:
            # 已輪詢的工作排到後面，轉檔中的舊研究不阻塞新搜尋。
            row.lease_token=None;row.lease_expires_at=datetime.now(UTC)
        return True


def step(*,dsn=None):
    state=claim(dsn=dsn)
    if not state:return False
    try:
        if state['status']=='searching':
            query=state['search_query']
            if not query:
                query=provider('/search-query',{'query':state['query'], 'material_title':state['material_title'], 'mode':state['mode']})['query']
                if not isinstance(query,str) or not 1<=len(query)<=300:raise SourceError('RESEARCH_SEARCH_FAILED')
            candidates,cursor,warnings=research_sources.search(query,state['cursor'])
            def found(row):
                known={c['id'] for c in row.candidates}
                row.candidates=row.candidates+[c for c in candidates if c['id'] not in known]
                row.cursor=cursor;row.search_query=query;row.status='selecting';row.error_code='RESEARCH_OFFICIAL_UNAVAILABLE' if warnings else None
            save(state,found,dsn=dsn)
        elif state['status']=='acquiring':
            items=[c for c in state['candidates'] if c['id'] in state['selection']]
            candidate=next((c for c in items if c['state']=='candidate'),None)
            if candidate is None:
                save(state,lambda r:setattr(r,'status','normalizing'),dsn=dsn);return True
            try:
                metadata=state['staged_metadata']
                if state['staged_content'] and metadata and metadata['id']==candidate['id']:
                    data=state['staged_content'];media=metadata['media'];suffix=metadata['suffix']
                else:
                    data,media,suffix,metadata=research_sources.acquire(candidate)
                    metadata.update(id=candidate['id'],media=media,suffix=suffix,acquired_at=datetime.now(UTC).isoformat())
                    def stage(row):row.staged_content=data;row.staged_metadata=metadata
                    if not save(state,stage,dsn=dsn,release=False):return True
                name='補充｜'+re.sub(r'[\x00-\x1f/\\]',' ',candidate['title'])[:150]+suffix
                source_id=upload_source(state['owner'],state['material_id'],data,name,media,
                    'research:'+str(state['research_id'])+':'+candidate['id'],dsn=dsn,
                    research_attempt=(state['research_id'],state['lease_token']))
                candidate.update(state='normalizing',source_id=str(source_id),acquisition=metadata)
            except Exception as error:
                candidate.update(state='failed',error_code=str(error) if isinstance(error,SourceError) else 'RESEARCH_DOWNLOAD_FAILED')
            def acquired(row):row.candidates=state['candidates'];row.staged_content=None;row.staged_metadata=None
            save(state,acquired,dsn=dsn)
        else:
            items=[c for c in state['candidates'] if c['id'] in state['selection']]
            with database_session(dsn) as db:
                for c in items:
                    if c['state']!='normalizing':continue
                    job=db.scalar(select(SourceNormalization).where(SourceNormalization.source_id==UUID(c['source_id'])).order_by(SourceNormalization.created_at.desc()))
                    if job and job.status=='ready':c.update(state='ready',normalization_id=str(job.normalization_id))
                    elif job and job.status in ('failed','cancelled'):c.update(state='failed',error_code=job.error_code or 'RESEARCH_NORMALIZATION_FAILED')
            def normalized(row):
                row.candidates=state['candidates']
                if all(c['state'] in ('ready','failed') for c in items):
                    row.status='ready' if all(c['state']=='ready' for c in items) else 'failed'
                    if row.status=='failed':row.error_code='RESEARCH_ITEMS_FAILED'
            save(state,normalized,dsn=dsn)
    except Exception as error:
        def failed(row):row.status='failed';row.error_code=str(error) if isinstance(error,SourceError) else 'RESEARCH_SEARCH_FAILED'
        save(state,failed,dsn=dsn)
    return True
