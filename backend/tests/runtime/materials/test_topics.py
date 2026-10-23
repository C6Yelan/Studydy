"""F3 的確認邊界與真 DB／原發布流程；外部來源及模型為明確替身。"""
from copy import deepcopy
from uuid import uuid4
import pytest
from sqlalchemy import select,func
from product_fixtures import closed_loop
from sources.test_source_revisions import revisions,pdf
from runtime import topics,research
from runtime.storage.materials import MaterialLibraryError
from research_fixtures import review_provider,search_to_selection
from runtime.source_normalization import normalize_next
from runtime.errors import SourceError
from runtime.material_discard import request_material_discard
from runtime.storage.tables import TopicScope, MaterialResearch, Material
from runtime.storage.database import database_session

PLAN={'title':'佇列入門','goals':['理解 FIFO，不討論實作效能最佳化'],'topics':['佇列的資料順序']}
CANDIDATE={'id':'paper','kind':'paper','title':'Queue source','authors':'Fixture','year':2026,'description':'A queue removes the first inserted item first.','doi':None,
    'url':'https://example.org/paper','download_url':'https://example.org/paper.pdf','license':'cc-by','license_url':None,
    'version':'publishedVersion','eligible':True,'reason':'fixture','state':'candidate'}


def fixtures(monkeypatch,empty=False):
    calls=[];data=pdf('A queue removes the first inserted element first.')
    monkeypatch.setattr(topics,'provider',lambda *a,**kwargs:deepcopy(PLAN))
    monkeypatch.setattr(research,'provider',review_provider)
    def search(*a,**kwargs):calls.append(a);return ([] if empty else [deepcopy(CANDIDATE)]),None,[]
    monkeypatch.setattr(research.research_sources,'search',search)
    monkeypatch.setattr(research.research_sources,'acquire',lambda c,**kwargs:(data,'application/pdf','.pdf',{'id':c['id'],'license':'cc-by'}))
    return calls


def prepare(owner,dsn,monkeypatch,empty=False):
    calls=fixtures(monkeypatch,empty)
    row=topics.create(owner,'我想了解佇列','topic',dsn=dsn)
    assert topics.step(dsn=dsn)
    view=topics.read(owner,row['topic_id'],dsn=dsn)
    assert view['status']=='ready' and calls==[]
    return view,calls


def test_topic_approval_then_real_source_pipeline(revisions,monkeypatch):
    learner,_,_,dsn,_,_,execute,_,_,_=revisions;owner=learner.learner_id
    settings=revisions[2]
    with database_session(dsn) as db:before=db.scalar(select(func.count()).select_from(Material))
    view,calls=prepare(owner,dsn,monkeypatch)
    edited={**PLAN,'goals':['確認資料移除順序']}
    approved=topics.approve(owner,view['topic_id'],view['version'],edited,'approve',dsn=dsn)
    assert topics.approve(owner,view['topic_id'],view['version'],edited,'approve',dsn=dsn)==approved
    assert calls==[]
    summary=topics.listing(owner,dsn=dsn)['topics'][0]
    assert summary['research']=={'status':'searching','material_id':None}
    assert topics.listing(uuid4(),dsn=dsn)['topics']==[]
    with database_session(dsn) as db:assert db.scalar(select(func.count()).select_from(Material))==before
    search_to_selection(owner,approved['research_id'],dsn)
    assert len(calls)==1
    with pytest.raises(SourceError,match='REQUEST_INVALID'):
        research.action(owner,approved['research_id'],'acquire',['paper'],dsn=dsn)
    with database_session(dsn) as db:assert db.scalar(select(func.count()).select_from(Material))==before
    created=topics.create_material(owner,view['topic_id'],['paper'],dsn=dsn)
    assert topics.create_material(owner,view['topic_id'],['paper'],dsn=dsn)==created
    research.step(dsn=dsn);assert normalize_next(dsn=dsn)
    for _ in range(3):research.step(dsn=dsn,config=settings)
    linked=topics.read(owner,view['topic_id'],dsn=dsn)
    assert linked['proposal']==edited and linked['research']['status']=='submitted'
    assert execute().status=='succeeded'
    complete=topics.read(owner,view['topic_id'],dsn=dsn)
    assert complete['research']['run']['status']=='succeeded'
    assert complete['research']['material_id']==created['material_id']
    assert topics.listing(owner,dsn=dsn)['topics'][0]['research']=={'status':'submitted','material_id':created['material_id']}
    research.delete(owner,approved['research_id'],dsn=dsn)
    assert research.listing(owner,created['material_id'],dsn=dsn)['researches']==[]
    retained=topics.read(owner,view['topic_id'],dsn=dsn)['research']
    assert retained['deleted'] is True and retained['run']['status']=='succeeded'
    assert retained['candidates']==complete['research']['candidates']
    with pytest.raises(SourceError,match='RESOURCE_NOT_FOUND'):research.read(owner,approved['research_id'],dsn=dsn)


def test_no_source_does_not_create_fake_material_or_map(closed_loop,monkeypatch):
    learner,_,_,_,dsn,_=closed_loop;owner=learner.learner_id
    view,calls=prepare(owner,dsn,monkeypatch,True)
    topics.approve(owner,view['topic_id'],view['version'],PLAN,'approve',dsn=dsn)
    search_to_selection(owner,topics.read(owner,view['topic_id'],dsn=dsn)['research_id'],dsn)
    result=topics.read(owner,view['topic_id'],dsn=dsn)
    assert result['research']['status']=='selecting' and result['research']['candidates']==[]
    with pytest.raises(SourceError,match='REQUEST_INVALID'):topics.create_material(owner,view['topic_id'],['invented'],dsn=dsn)
    assert result['research']['material_id'] is None
    with database_session(dsn) as db:assert db.scalar(select(func.count()).select_from(Material))==1


def test_scope_owner_stale_version_and_cancel_late_generation(closed_loop,monkeypatch):
    learner,_,_,_,dsn,_=closed_loop;owner=learner.learner_id
    view,_=prepare(owner,dsn,monkeypatch)
    with pytest.raises(SourceError,match='RESOURCE_NOT_FOUND'):topics.read(uuid4(),view['topic_id'],dsn=dsn)
    with pytest.raises(SourceError,match='TOPIC_CONFLICT'):topics.approve(owner,view['topic_id'],1,PLAN,'approve',dsn=dsn)
    with database_session(dsn) as db:assert not db.scalars(select(MaterialResearch)).all()
    row=topics.create(owner,'取消測試','late',dsn=dsn)
    def late(*a):topics.action(owner,row['topic_id'],'cancel',row['version'],dsn=dsn);return deepcopy(PLAN)
    monkeypatch.setattr(topics,'provider',late)
    topics.step(dsn=dsn)
    assert topics.read(owner,row['topic_id'],dsn=dsn)['status']=='cancelled'


def test_discard_created_material_cannot_be_resurrected(closed_loop,monkeypatch):
    learner,_,_,_,dsn,_=closed_loop;owner=learner.learner_id
    view,_=prepare(owner,dsn,monkeypatch)
    topics.approve(owner,view['topic_id'],view['version'],PLAN,'approve',dsn=dsn)
    search_to_selection(owner,topics.read(owner,view['topic_id'],dsn=dsn)['research_id'],dsn)
    material=topics.create_material(owner,view['topic_id'],['paper'],dsn=dsn)['material_id']
    assert request_material_discard(owner,material,dsn=dsn)=='removed'
    assert topics.read(owner,view['topic_id'],dsn=dsn)['status']=='cancelled'
    with pytest.raises(SourceError,match='TOPIC_CONFLICT'):topics.create_material(owner,view['topic_id'],['paper'],dsn=dsn)
    assert not research.step(dsn=dsn)


def test_topic_api_requires_identity_origin_and_exact_scope(closed_loop,monkeypatch):
    import runtime.api.app as api
    from fastapi.testclient import TestClient
    learner,_,settings,_,dsn,token=closed_loop
    fixtures(monkeypatch)
    origin='http://127.0.0.1:4183'
    app=api.create_app(api.ApiSettings(profile='local',public_origin=origin,secure_cookie=False,local_config=settings,dsn=dsn))
    client=TestClient(app,base_url=origin);headers={'Origin':origin,'Idempotency-Key':'topic-api'}
    assert client.post('/v1/topics',headers=headers,json={'request':'佇列'}).status_code==401
    client.cookies.set('studydy_session',token)
    assert client.post('/v1/topics',json={'request':'佇列'}).status_code==403
    assert client.post('/v1/topics',headers=headers,json={'request':'佇列','learner_id':str(learner.learner_id)}).status_code==400
    r=client.post('/v1/topics',headers=headers,json={'request':'佇列'});assert r.status_code==202
    identity=r.json()['topic_id'];topics.step(dsn=dsn)
    assert client.post('/v1/materials/'+str(closed_loop[1].material_id)+'/research',headers=headers,json={'query':'bypass','mode':'review'}).status_code==405
    view=client.get('/v1/topics/'+identity).json();assert view['generation_provider']=='codex-exec:gpt-5.6-luna;scope/v6'
    r=client.post('/v1/topics/'+identity+'/approve',headers=headers,json={'expected_version':view['version'],'proposal':PLAN})
    assert r.status_code==200 and r.json()['approved_at']
    assert client.get('/v1/topics/'+identity).headers['cache-control']=='private, no-store'
    # 已保存的舊研究可讀取，但不能跳過新確認流程再啟動搜尋。
    from uuid import UUID
    with database_session(dsn) as db:
        work=db.get(MaterialResearch,UUID(r.json()['research_id']))
        material=db.get(Material,closed_loop[1].material_id)
        work.material_id=material.material_id;work.base_revision=material.head_revision
        work.topic_id=None;work.status='failed';work.selection=None
        research_id=str(work.research_id)
    rejected=client.post('/v1/research/'+research_id+'/actions',headers=headers,json={'action':'retry'})
    assert rejected.status_code==409 and rejected.json()['reason_code']=='RESEARCH_SCOPE_REQUIRED'
    # 研究會自動依缺口補查；手動補查動作與補查方向已移除。
    for body in ({'action':'more'},{'action':'retry','guidance':'focus'}):
        removed=client.post('/v1/research/'+research_id+'/actions',headers=headers,json=body)
        assert removed.status_code==400 and removed.json()['reason_code']=='REQUEST_INVALID'



def test_confirmed_requirements_reach_search_without_degree_labels(closed_loop,monkeypatch):
    learner,_,_,_,dsn,_=closed_loop;owner=learner.learner_id
    view,_=prepare(owner,dsn,monkeypatch)
    edited={**PLAN,'goals':['已有 Python 基礎，想理解資料移除的原理與限制']}
    approved=topics.approve(owner,view['topic_id'],view['version'],edited,'approve-scope',dsn=dsn)
    payloads=[]
    def query(path,body):
        payloads.append(deepcopy(body['request']))
        return review_provider(path,body)
    monkeypatch.setattr(research,'provider',query)
    assert research.step(dsn=dsn)
    assert payloads[0]['approved_scope']==edited
    assert topics.read(owner,view['topic_id'],dsn=dsn)['proposal']==edited
    assert topics.approve(owner,view['topic_id'],view['version'],edited,'approve-scope',dsn=dsn)['research_id']==approved['research_id']


@pytest.mark.parametrize('field,value',[('level','入門'),('exclude',['雲端部署'])])
def test_scope_rejects_removed_settings(field,value):
    with pytest.raises(SourceError,match='REQUEST_INVALID'):
        topics.proposal({**PLAN,field:value})


def test_supplement_uses_confirmed_scope_and_never_searches_before_approval(closed_loop,monkeypatch):
    learner,source,_,_,dsn,_=closed_loop;owner=learner.learner_id
    calls=fixtures(monkeypatch)
    scope=topics.create(owner,'補充佇列的停止條件','supplement-scope',material_id=source.material_id,dsn=dsn)
    assert scope['material_id']==source.material_id and scope['base_revision']
    assert not research.step(dsn=dsn) and not calls
    assert topics.step(dsn=dsn)
    view=topics.read(owner,scope['topic_id'],dsn=dsn)
    assert view['status']=='ready' and view['research_id'] is None
    assert not research.step(dsn=dsn) and not calls
    assert topics.listing(owner,dsn=dsn)['topics']==[]
    assert topics.listing(owner,material_id=source.material_id,dsn=dsn)['topics'][0]['topic_id']==scope['topic_id']
    edited={**PLAN,'topics':['佇列停止：何時沒有可處理資料','錯誤恢復：重試與終止的條件']}
    approved=topics.approve(owner,scope['topic_id'],view['version'],edited,'confirm-supplement',dsn=dsn)
    work=research.read(owner,approved['research_id'],dsn=dsn)
    with database_session(dsn) as db:mode=db.get(MaterialResearch,approved['research_id']).mode
    assert work['material_id']==source.material_id and mode=='review'
    assert work['approved_scope']==edited
    assert research.step(dsn=dsn)
    progress=research.read(owner,approved['research_id'],dsn=dsn)['progress']
    assert [t['topic'] for t in progress['topics']]==edited['topics']
    assert not calls
    search_to_selection(owner,approved['research_id'],dsn)
    assert calls
    with pytest.raises(MaterialLibraryError,match='RESOURCE_NOT_FOUND'):
        topics.create(uuid4(),'foreign material','foreign',material_id=source.material_id,dsn=dsn)
    with pytest.raises(SourceError,match='TOPIC_CONFLICT'):
        topics.create_material(owner,scope['topic_id'],['paper'],dsn=dsn)


def test_supplement_scope_checks_material_revision_before_search(revisions,monkeypatch):
    learner,material,_,dsn,add,start,execute,_,old,_=revisions;owner=learner.learner_id
    fixtures(monkeypatch)
    scope=topics.create(owner,'補充','revision-scope',material_id=material,dsn=dsn)
    assert topics.step(dsn=dsn)
    view=topics.read(owner,scope['topic_id'],dsn=dsn)
    second=add('new.pdf','A tree contains nodes and edges.')
    start([second],'new-revision',old['revision']);assert execute().status=='succeeded'
    with pytest.raises(SourceError,match='REVISION_CONFLICT'):
        topics.approve(owner,scope['topic_id'],view['version'],PLAN,'stale-approve',dsn=dsn)
    with database_session(dsn) as db:assert not db.scalars(select(MaterialResearch)).all()


def test_discard_cancels_pending_supplement_scope_without_resurrection(closed_loop):
    learner,source,_,_,dsn,_=closed_loop
    scope=topics.create(learner.learner_id,'補充尚未確認的內容','pending-discard',material_id=source.material_id,dsn=dsn)
    assert request_material_discard(learner.learner_id,source.material_id,dsn=dsn)=='removed'
    saved=topics.read(learner.learner_id,scope['topic_id'],dsn=dsn)
    assert saved['status']=='cancelled' and saved['material_id'] is None and saved['research_id'] is None
    assert not topics.step(dsn=dsn) and not research.step(dsn=dsn)


@pytest.mark.parametrize('supplement',[False,True])
def test_cancel_unconfirmed_scope_removes_only_the_plan(closed_loop,monkeypatch,supplement):
    learner,source,_,_,dsn,_=closed_loop;owner=learner.learner_id
    fixtures(monkeypatch)
    material_id=source.material_id if supplement else None
    with database_session(dsn) as db:
        original_head=db.get(Material,source.material_id).head_revision
    created=topics.create(owner,'需要確認的搜尋範圍','delete-plan',material_id=material_id,dsn=dsn)
    assert topics.step(dsn=dsn)
    ready=topics.read(owner,created['topic_id'],dsn=dsn)
    assert ready['status']=='ready' and ready['research_id'] is None
    topics.action(owner,ready['topic_id'],'cancel',ready['version'],dsn=dsn)
    assert topics.listing(owner,material_id=material_id,dsn=dsn)['topics']==[]
    with pytest.raises(SourceError,match='TOPIC_CONFLICT'):
        topics.approve(owner,ready['topic_id'],ready['version'],PLAN,'late-approval',dsn=dsn)
    with database_session(dsn) as db:
        assert db.get(Material,source.material_id).head_revision==original_head
        assert not db.scalars(select(MaterialResearch)).all()
