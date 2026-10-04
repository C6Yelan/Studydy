from uuid import uuid4
from copy import deepcopy
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from product_fixtures import closed_loop
from runtime import voice
from runtime.source_normalization import SourceError
from runtime.card_sets import CardSetError
from runtime.storage.tables import VoiceTurn, database_session
from runtime.material_discard import request_material_discard
import runtime.api.app as api


def setup(f):
    owner,source,_,_,dsn,_=f
    c=voice.create(owner.learner_id,source.material_id,'conversation',dsn=dsn)
    return owner.learner_id,source.material_id,c['conversation_id'],dsn


def test_voice_answer_persistence_replay_cancel_and_owner(closed_loop):
    owner,material,c,dsn=setup(closed_loop)
    assert voice.create(owner,material,'conversation',dsn=dsn)['conversation_id']==c
    t=voice.add_turn(owner,c,'first','什麼是佇列？',dsn=dsn)
    assert voice.add_turn(owner,c,'first','什麼是佇列？',dsn=dsn)==t
    with pytest.raises(SourceError,match='IDEMPOTENCY_CONFLICT'):voice.add_turn(owner,c,'first','不同',dsn=dsn)
    with pytest.raises(SourceError,match='RESOURCE_NOT_FOUND'):voice.read(uuid4(),c,dsn=dsn)
    state=voice.claim(dsn=dsn)
    assert state['claims'] and state['history']==[]
    voice.finish(state,{'text':'依照教材說明這個概念。','supported':True,'citations':[0]},dsn=dsn)
    v=voice.read(owner,c,dsn=dsn)
    assert v['turns'][0]['answer']['citations'][0]==state['claims'][0]
    audio_state=voice.claim(dsn=dsn)
    voice.action(owner,c,t['turn_id'],'cancel',dsn=dsn)
    voice.finish(audio_state,b'late invalid audio',dsn=dsn)
    assert voice.read(owner,c,dsn=dsn)['turns'][0]['status']=='cancelled'
    voice.remove(owner,c,dsn=dsn)
    with pytest.raises(SourceError,match='RESOURCE_NOT_FOUND'):voice.create(owner,material,'conversation',dsn=dsn)


def test_transcript_confirmation_and_invalid_reference(closed_loop):
    owner,_,c,dsn=setup(closed_loop)
    t=voice.add_turn(owner,c,'record',recording=b'synthetic media',dsn=dsn)
    state=voice.claim(dsn=dsn)
    voice.finish(state,{'text':'修改前的問題'},dsn=dsn)
    with database_session(dsn) as db:assert db.get(VoiceTurn,t['turn_id']).recording is None
    voice.action(owner,c,t['turn_id'],'send','修改後的問題',dsn=dsn)
    state=voice.claim(dsn=dsn)
    assert state['question']=='修改後的問題'
    with pytest.raises(SourceError,match='VOICE_ANSWER_INVALID'):
        voice.finish(state,{'text':'錯誤來源不得發表','supported':True,'citations':[99999]},dsn=dsn)
    voice.finish(state,error='VOICE_PROVIDER_FAILED',dsn=dsn)
    voice.action(owner,c,t['turn_id'],'retry',dsn=dsn)
    assert voice.claim(dsn=dsn)


def test_voice_discard_and_late_result(closed_loop):
    owner,material,c,dsn=setup(closed_loop)
    voice.add_turn(owner,c,'first','測試教材問題',dsn=dsn)
    state=voice.claim(dsn=dsn)
    assert request_material_discard(owner,material,dsn=dsn)=='removed'
    voice.finish(state,{'text':'遲到內容','supported':False,'citations':[]},dsn=dsn)
    with database_session(dsn) as db:assert not db.scalars(select(VoiceTurn)).all()


def test_voice_api_origin_owner_and_validation(closed_loop):
    learner,source,settings,_,dsn,token=closed_loop
    origin='http://127.0.0.1:4183'
    app=api.create_app(api.ApiSettings(profile='local',public_origin=origin,secure_cookie=False,local_config=settings,dsn=dsn))
    client=TestClient(app,base_url=origin)
    url=f'/v1/materials/{source.material_id}/voice-conversations'
    h={'Origin':origin,'Idempotency-Key':'voice-api'}
    assert client.post(url,headers=h).status_code==401
    client.cookies.set('studydy_session',token)
    assert client.post(url).status_code==403
    r=client.post(url,headers=h);assert r.status_code==201,r.text
    c=r.json()['conversation_id']
    assert client.get(f'/v1/voice-conversations/{c}').headers['cache-control']=='private, no-store'
    assert client.post(f'/v1/voice-conversations/{c}/turns',headers=h,json={'question':'問題','owner':'fake'}).status_code==400
    r=client.post(f'/v1/voice-conversations/{c}/turns',headers=h,json={'question':'教材問題'})
    assert r.status_code==202,r.text
