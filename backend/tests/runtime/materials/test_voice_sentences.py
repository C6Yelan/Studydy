from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from product_fixtures import closed_loop, ORIGIN, _app
from materials.test_voice import setup, audio_fixture
from qa_fixtures import grounded_answer, qa_model
from runtime import voice
from runtime.errors import SourceError


def prepare(fixture, text):
    owner,material,cid,dsn=setup(fixture)
    turn=voice.add_turn(owner,cid,'sentences','問題',mode='voice',dsn=dsn)['turn_id']
    state=voice.claim(dsn=dsn)
    assert voice.read(owner,cid,dsn=dsn)['turns'][0]['audio_sentences']==[]
    voice.finish(state,grounded_answer(state,dsn,text),dsn=dsn)
    return owner,cid,turn,dsn


def test_verified_sentences_stream_in_order_and_cancel_stops_pending(closed_loop, tmp_path, monkeypatch):
    owner,cid,turn,dsn=prepare(closed_loop,'數值是 3.14。第二句！第三句？')
    view=voice.read(owner,cid,dsn=dsn)['turns'][0]
    assert [s['text'] for s in view['audio_sentences']]==['數值是 3.14。','第二句！','第三句？']
    assert [s['index'] for s in view['audio_sentences']]==[0,1,2]
    state=voice.claim(dsn=dsn)
    assert state['sentence_index']==0
    voice.finish(state,audio_fixture(),dsn=dsn)
    view=voice.read(owner,cid,dsn=dsn)['turns'][0]
    assert view['status']=='speaking' and view['audio_url'] is None
    assert [s['status'] for s in view['audio_sentences']]==['ready','pending','pending']
    client=TestClient(_app(dsn,tmp_path,monkeypatch),base_url=ORIGIN)
    client.cookies.set('studydy_session',closed_loop[5])
    base=f'/v1/voice-conversations/{cid}/turns/{turn}/audio'
    assert client.get(base+'/0').content==audio_fixture()
    assert client.get(base+'/1').status_code==409
    assert client.get(base+'/9').status_code==404
    assert client.get(base).status_code==404
    with pytest.raises(SourceError,match='RESOURCE_NOT_FOUND'):
        voice.audio(uuid4(),cid,turn,index=0,dsn=dsn)
    state=voice.claim(dsn=dsn)
    voice.action(owner,cid,turn,'cancel',dsn=dsn)
    voice.finish(state,audio_fixture(),dsn=dsn)
    cancelled=voice.read(owner,cid,dsn=dsn)['turns'][0]
    assert cancelled['stage']=='cancelled'
    assert [s['status'] for s in cancelled['audio_sentences']]==['ready','cancelled','cancelled']
    assert voice.claim(dsn=dsn) is None


def test_failed_sentence_retry_preserves_ready_audio_and_combines_download(closed_loop):
    from runtime.audio import validate_audio
    owner,cid,turn,dsn=prepare(closed_loop,'第一句。第二句。')
    first=voice.claim(dsn=dsn);voice.finish(first,audio_fixture(),dsn=dsn)
    second=voice.claim(dsn=dsn);voice.finish(second,error='VOICE_PROVIDER_FAILED',dsn=dsn)
    failed=voice.read(owner,cid,dsn=dsn)['turns'][0]
    assert failed['status']=='failed' and failed['stage']=='failed'
    assert [s['status'] for s in failed['audio_sentences']]==['ready','failed']
    voice.action(owner,cid,turn,'retry',dsn=dsn)
    retry=voice.claim(dsn=dsn)
    assert retry['sentence_index']==1 and retry['answer']==second['answer']
    voice.finish(retry,audio_fixture(),dsn=dsn)
    ready=voice.read(owner,cid,dsn=dsn)['turns'][0]
    assert ready['status']=='ready' and ready['stage']=='completed'
    assert validate_audio(voice.audio(owner,cid,turn,dsn=dsn))==2*validate_audio(audio_fixture())
    assert voice.audio(owner,cid,turn,index=0,dsn=dsn)==audio_fixture()


def test_worker_records_five_actual_timings_and_only_sends_verified_sentence(closed_loop,monkeypatch):
    owner,_,cid,dsn=setup(closed_loop)
    voice.add_turn(owner,cid,'measured','教材問題',mode='voice',dsn=dsn)
    observed=[]
    def provider(path,body,*,timings=None):
        current=voice.read(owner,cid,dsn=dsn)['turns'][0]
        observed.append(current['stage'])
        if path=='/semantics':
            assert current['audio_sentences']==[]
            return qa_model(body,'第一句。第二句。')
        assert path=='/audio' and body['script']['segments'][0]['turns'][0]['text'] in ('第一句。','第二句。')
        timings.update(tts_queue_ms=12,tts_synthesis_ms=34)
        return audio_fixture()
    monkeypatch.setattr(voice,'provider',provider)
    assert voice.step(dsn=dsn)
    checked=voice.read(owner,cid,dsn=dsn)['turns'][0]
    assert {'retrieval','answering','checking'}<=set(observed)
    assert all(checked['timings'][k]>=0 for k in ('retrieval_ms','answer_ms','verification_ms'))
    assert voice.step(dsn=dsn) and voice.step(dsn=dsn)
    ready=voice.read(owner,cid,dsn=dsn)['turns'][0]
    assert ready['timings']['tts_queue_ms']>=24
    assert ready['timings']['tts_synthesis_ms']==68
    assert ready['stage']=='completed'


def test_voice_sentence_migration_applies_and_rolls_back(clean_database_dsn,migrations_dir,tmp_path):
    import shutil
    import psycopg
    from runtime.storage.migrations import load_migrations,run_migrations
    previous=tmp_path/'previous';previous.mkdir()
    for path in sorted(migrations_dir.glob('*.sql'))[:26]:shutil.copyfile(path,previous/path.name)
    run_migrations(clean_database_dsn,migrations_dir=previous)
    migrations=load_migrations(migrations_dir)
    with psycopg.connect(clean_database_dsn,autocommit=True) as db:
        with db.transaction(force_rollback=True):
            db.execute(migrations[26].sql)
            db.execute('SELECT stage,timings FROM voice_turns')
        assert db.execute("SELECT to_regclass('voice_sentences')").fetchone()==(None,)
    assert run_migrations(clean_database_dsn,migrations_dir=migrations_dir)==tuple(m.version for m in migrations[26:])


def test_legacy_verified_audio_failure_can_retry_without_blocking_queue(closed_loop,monkeypatch):
    from sqlalchemy import delete
    from runtime.storage.database import database_session
    from runtime.storage.tables import VoiceSentence, VoiceTurn
    owner,cid,turn,dsn=prepare(closed_loop,'舊回答。')
    voice.finish(voice.claim(dsn=dsn),error='VOICE_PROVIDER_FAILED',dsn=dsn)
    with database_session(dsn) as db:
        db.execute(delete(VoiceSentence).where(VoiceSentence.turn_id==turn))
        saved=db.get(VoiceTurn,turn)
        saved.answer={**saved.answer,'schema':'material-answer/v2'}
        saved.stage=saved.timings=None
    before=deepcopy(voice.read(owner,cid,dsn=dsn)['turns'][0]['answer'])
    voice.action(owner,cid,turn,'retry',dsn=dsn)
    calls=[]
    def provider(path,body,**kwargs):
        calls.append(path)
        return audio_fixture()
    monkeypatch.setattr(voice,'provider',provider)
    assert voice.step(dsn=dsn) and not voice.step(dsn=dsn)
    ready=voice.read(owner,cid,dsn=dsn)['turns'][0]
    assert ready['status']=='ready' and ready['answer']==before and calls==['/audio']
