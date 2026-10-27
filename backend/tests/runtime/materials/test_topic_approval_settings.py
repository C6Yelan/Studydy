from datetime import date, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from product_fixtures import closed_loop, HEADERS, ORIGIN, _app
from materials.test_topics import fixtures, PLAN
from research_fixtures import search_to_selection
from runtime import topics
from runtime.storage.database import database_session
from runtime.storage.tables import Material, MaterialResearch


@pytest.mark.parametrize('change',['omit','clear','replace'])
def test_approval_settings_are_atomic_replayable_and_carried_to_material(closed_loop,monkeypatch,tmp_path,change):
    owner,_,_,_,dsn,token=closed_loop
    fixtures(monkeypatch)
    client=TestClient(_app(dsn,tmp_path,monkeypatch),base_url=ORIGIN)
    client.cookies.set('studydy_session',token)
    initial_goal={'target_date':(date.today()+timedelta(days=7)).isoformat(),'target_percent':60}
    create_body={'request':'了解佇列','learning_goal':initial_goal}
    create_headers={**HEADERS,'Idempotency-Key':'create-settings'}
    created=client.post('/v1/topics',headers=create_headers,json=create_body)
    assert created.status_code==202,created.text
    identity=created.json()['topic_id']
    assert topics.step(dsn=dsn)
    ready=client.get(f'/v1/topics/{identity}').json()
    settings={}
    expected_goal=initial_goal
    if change=='clear':
        settings={'learning_goal':None}
        expected_goal=None
    elif change=='replace':
        expected_goal={'target_date':(date.today()+timedelta(days=9)).isoformat(),'target_percent':80}
        settings={'learning_goal':expected_goal}
    payload={'expected_version':ready['version'],'proposal':PLAN,**settings}
    headers={**HEADERS,'Idempotency-Key':'approve-settings'}
    url=f'/v1/topics/{identity}/approve'
    approved=client.post(url,headers=headers,json=payload)
    assert approved.status_code==200,approved.text
    value=approved.json()
    assert value['learning_goal']==expected_goal
    assert client.post(url,headers=headers,json=payload).json()==value
    for altered in (
        {**payload,'expected_version':ready['version']+1},
        {**payload,'learning_goal':{'target_date':(date.today()+timedelta(days=20)).isoformat(),'target_percent':100}},
        {**payload,'proposal':{**PLAN,'title':'改名'}},
    ):
        rejected=client.post(url,headers=headers,json=altered)
        assert rejected.status_code==409 and rejected.json()['reason_code']=='TOPIC_CONFLICT'
    assert client.post(url,headers={**headers,'Idempotency-Key':'different'},json=payload).status_code==409
    with database_session(dsn) as db:
        assert db.scalar(select(func.count()).select_from(MaterialResearch).where(MaterialResearch.topic_id==UUID(identity)))==1
    # 核准限制：確認改過設定，舊建立請求重送會回既有衝突碼。
    replay=client.post('/v1/topics',headers=create_headers,json=create_body)
    if change=='omit':
        assert replay.status_code==202
    else:
        assert replay.status_code==409 and replay.json()['reason_code']=='IDEMPOTENCY_CONFLICT'
    search_to_selection(owner.learner_id,UUID(value['research_id']),dsn)
    material=topics.create_material(owner.learner_id,UUID(identity),['paper'],dsn=dsn)
    with database_session(dsn) as db:
        saved=db.get(Material,material['material_id'])
        assert saved.pending_goal==expected_goal


def test_invalid_approval_settings_leave_topic_and_search_unchanged(closed_loop,monkeypatch,tmp_path):
    owner,_,_,_,dsn,token=closed_loop
    fixtures(monkeypatch)
    client=TestClient(_app(dsn,tmp_path,monkeypatch),base_url=ORIGIN)
    client.cookies.set('studydy_session',token)
    created=topics.create(owner.learner_id,'佇列','invalid-settings',dsn=dsn)
    assert topics.step(dsn=dsn)
    ready=topics.read(owner.learner_id,created['topic_id'],dsn=dsn)
    url=f"/v1/topics/{created['topic_id']}/approve"
    base={'expected_version':ready['version'],'proposal':PLAN}
    for settings in (
        {'learning_goal':{'target_date':(date.today()+timedelta(days=7)).isoformat(),'target_percent':70}},
        {'learning_goal':{'target_date':(date.today()-timedelta(days=1)).isoformat(),'target_percent':60}},
    ):
        response=client.post(url,headers={**HEADERS,'Idempotency-Key':'invalid-approve'},json={**base,**settings})
        assert response.status_code==400,response.text
    assert topics.read(owner.learner_id,created['topic_id'],dsn=dsn)==ready
    with database_session(dsn) as db:
        assert db.scalar(select(func.count()).select_from(MaterialResearch).where(MaterialResearch.topic_id==created['topic_id']))==0
