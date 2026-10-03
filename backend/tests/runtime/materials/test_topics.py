"""F3 的確認邊界與真 DB／原發布流程；外部來源及模型為明確替身。"""
from copy import deepcopy
from uuid import uuid4
import pytest
from sqlalchemy import select,func
from product_fixtures import closed_loop
from sources.test_source_revisions import revisions,pdf
from runtime import topics,research
from runtime.source_normalization import SourceError,normalize_next
from runtime.material_discard import request_material_discard
from runtime.storage.tables import TopicScope,MaterialResearch,Material,database_session

PLAN={'title':'佇列入門','level':'入門','goals':['理解 FIFO'],'topics':['佇列的資料順序'],'exclude':['實作效能最佳化']}
CANDIDATE={'id':'paper','kind':'paper','title':'Queue source','authors':'Fixture','year':2026,'doi':None,
    'url':'https://example.org/paper','download_url':'https://example.org/paper.pdf','license':'cc-by','license_url':None,
    'version':'publishedVersion','eligible':True,'reason':'fixture','state':'candidate'}


def fixtures(monkeypatch,empty=False):
    calls=[];data=pdf('A queue removes the first inserted element first.')
    monkeypatch.setattr(topics,'provider',lambda *a:{'proposal':deepcopy(PLAN),'provider':'synthetic-test'})
    monkeypatch.setattr(research,'provider',lambda *a:{'query':'queue FIFO'})
    def search(*a):calls.append(a);return ([] if empty else [deepcopy(CANDIDATE)]),None,[]
    monkeypatch.setattr(research.research_sources,'search',search)
    monkeypatch.setattr(research.research_sources,'acquire',lambda c:(data,'application/pdf','.pdf',{'id':c['id'],'license':'cc-by'}))
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
    edited={**PLAN,'level':'有基礎','goals':['確認資料移除順序']}
    approved=topics.approve(owner,view['topic_id'],view['version'],edited,'approve',dsn=dsn)
    assert topics.approve(owner,view['topic_id'],view['version'],edited,'approve',dsn=dsn)==approved
    assert calls==[]
    with database_session(dsn) as db:assert db.scalar(select(func.count()).select_from(Material))==before
    research.step(dsn=dsn)
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


def test_no_source_does_not_create_fake_material_or_map(closed_loop,monkeypatch):
    learner,_,_,_,dsn,_=closed_loop;owner=learner.learner_id
    view,calls=prepare(owner,dsn,monkeypatch,True)
    topics.approve(owner,view['topic_id'],view['version'],PLAN,'approve',dsn=dsn)
    research.step(dsn=dsn)
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
    def late(*a):topics.action(owner,row['topic_id'],'cancel',row['version'],dsn=dsn);return {'proposal':PLAN,'provider':'synthetic-test'}
    monkeypatch.setattr(topics,'provider',late)
    topics.step(dsn=dsn)
    assert topics.read(owner,row['topic_id'],dsn=dsn)['status']=='cancelled'


def test_discard_created_material_cannot_be_resurrected(closed_loop,monkeypatch):
    learner,_,_,_,dsn,_=closed_loop;owner=learner.learner_id
    view,_=prepare(owner,dsn,monkeypatch)
    topics.approve(owner,view['topic_id'],view['version'],PLAN,'approve',dsn=dsn)
    research.step(dsn=dsn)
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
    view=client.get('/v1/topics/'+identity).json();assert view['generation_provider']=='synthetic-test'
    r=client.post('/v1/topics/'+identity+'/approve',headers=headers,json={'expected_version':view['version'],'proposal':PLAN})
    assert r.status_code==200 and r.json()['approved_at']
    assert client.get('/v1/topics/'+identity).headers['cache-control']=='private, no-store'
