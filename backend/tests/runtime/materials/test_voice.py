from uuid import uuid4
from copy import deepcopy
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from product_fixtures import closed_loop
from sources.test_source_revisions import revisions
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
    assert v['turns'][0]['mode']=='text' and v['turns'][0]['status']=='ready'
    assert v['turns'][0]['audio_url'] is None and voice.claim(dsn=dsn) is None
    voice.remove(owner,c,dsn=dsn)
    with pytest.raises(SourceError,match='RESOURCE_NOT_FOUND'):voice.create(owner,material,'conversation',dsn=dsn)


def test_legacy_transcript_confirmation_and_invalid_reference(closed_loop):
    owner,_,c,dsn=setup(closed_loop)
    t=voice.add_turn(owner,c,'record',recording=b'synthetic media',dsn=dsn)
    # 遷移前的錄音仍保留明確草稿 fallback；新 Voice turn 不走這個流程。
    with database_session(dsn) as db:db.get(VoiceTurn,t['turn_id']).mode=None
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


def test_expired_voice_result_cannot_publish(closed_loop):
    from datetime import UTC, datetime, timedelta
    owner,_,cid,dsn=setup(closed_loop)
    turn=voice.add_turn(owner,cid,'expires','合成問題',dsn=dsn);state=voice.claim(dsn=dsn)
    with database_session(dsn) as db:db.get(VoiceTurn,turn['turn_id']).lease_expires_at=datetime.now(UTC)-timedelta(seconds=1)
    voice.finish(state,{'text':'過期回答','supported':True,'citations':[0]},dsn=dsn)
    assert voice.read(owner,cid,dsn=dsn)['turns'][0]['answer'] is None
    renewed=voice.claim(dsn=dsn);assert renewed['lease_token']!=state['lease_token']
    voice.finish(state,{'text':'舊工作回答','supported':True,'citations':[0]},dsn=dsn)
    voice.finish(renewed,{'text':'有效的回答','supported':True,'citations':[0]},dsn=dsn)
    assert voice.read(owner,cid,dsn=dsn)['turns'][0]['answer']['text']=='有效的回答'


def audio_fixture():
    import io, wave
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(24000)
        wav.writeframes(b'\x00\x01' * 24000)
    return output.getvalue()


def test_voice_worker_auto_chains_transcript_answer_audio_without_send(closed_loop, monkeypatch):
    owner, _, cid, dsn = setup(closed_loop)
    turn = voice.add_turn(owner, cid, 'voice-auto', recording=b'synthetic recording', dsn=dsn)
    paths = []
    def provider(path, body):
        paths.append(path)
        if path == '/transcribe': return {'text': '辨識出的教材問題'}
        if path == '/answer':
            assert body['question'] == '辨識出的教材問題'
            return {'text': '來源支持的回答。', 'supported': True, 'citations': [0]}
        assert path == '/audio'
        return audio_fixture()
    monkeypatch.setattr(voice, 'provider', provider)
    assert voice.step(dsn=dsn)
    result = voice.read(owner, cid, dsn=dsn)
    assert result['title'] == '辨識出的教材問題'
    assert result['turns'][0]['status'] == 'pending'
    assert result['turns'][0]['question'] == '辨識出的教材問題'
    assert result['turns'][0]['mode'] == 'voice'
    with database_session(dsn) as db: assert db.get(VoiceTurn, turn['turn_id']).recording is None
    assert voice.step(dsn=dsn)
    assert voice.read(owner, cid, dsn=dsn)['turns'][0]['status'] == 'speaking'
    assert voice.step(dsn=dsn)
    ready = voice.read(owner, cid, dsn=dsn)['turns'][0]
    assert ready['status'] == 'ready' and ready['audio_url']
    assert voice.audio(owner, cid, turn['turn_id'], dsn=dsn) == audio_fixture()
    assert paths == ['/transcribe', '/answer', '/audio']
    assert not voice.step(dsn=dsn)


def test_text_worker_finishes_without_audio_provider_and_mode_is_part_of_intent(closed_loop, monkeypatch):
    owner, _, cid, dsn = setup(closed_loop)
    turn = voice.add_turn(owner, cid, 'same-intent', 'same bytes', dsn=dsn)
    with pytest.raises(SourceError, match='IDEMPOTENCY_CONFLICT'):
        voice.add_turn(owner, cid, 'same-intent', recording=b'same bytes', dsn=dsn)
    paths = []
    def provider(path, body):
        paths.append(path)
        assert path == '/answer', 'Text mode must not request TTS'
        return {'text': '純文字回答。', 'supported': True, 'citations': [0]}
    monkeypatch.setattr(voice, 'provider', provider)
    assert voice.step(dsn=dsn) and not voice.step(dsn=dsn)
    ready = voice.read(owner, cid, dsn=dsn)['turns'][0]
    assert ready['turn_id'] == turn['turn_id'] and ready['mode'] == 'text'
    assert ready['status'] == 'ready' and ready['audio_url'] is None
    assert paths == ['/answer']


@pytest.mark.parametrize('phase', ['transcribing', 'answering', 'speaking'])
def test_voice_cancel_ignores_late_result_at_each_stage(closed_loop, phase):
    owner, _, cid, dsn = setup(closed_loop)
    turn = voice.add_turn(owner, cid, 'cancel-stage', recording=b'synthetic recording', dsn=dsn)
    state = voice.claim(dsn=dsn)
    answer = {'text': '已保存的回答。', 'supported': True, 'citations': [0]}
    if phase != 'transcribing':
        voice.finish(state, {'text': '問題'}, dsn=dsn)
        state = voice.claim(dsn=dsn)
    if phase == 'speaking':
        voice.finish(state, answer, dsn=dsn)
        state = voice.claim(dsn=dsn)
    assert state['status'] == phase
    voice.action(owner, cid, turn['turn_id'], 'cancel', dsn=dsn)
    late = {'transcribing': {'text': '遲到辨識'}, 'answering': answer, 'speaking': audio_fixture()}[phase]
    voice.finish(state, late, dsn=dsn)
    result = voice.read(owner, cid, dsn=dsn)['turns'][0]
    assert result['status'] == 'cancelled' and result['audio_url'] is None
    assert bool(result['answer']) == (phase == 'speaking')
    assert voice.claim(dsn=dsn) is None


def test_voice_audio_retry_keeps_answer_and_does_not_repeat_stt_or_answer(closed_loop, monkeypatch):
    owner, _, cid, dsn = setup(closed_loop)
    turn = voice.add_turn(owner, cid, 'audio-retry', recording=b'synthetic recording', dsn=dsn)
    voice.finish(voice.claim(dsn=dsn), {'text': '原問題'}, dsn=dsn)
    voice.finish(voice.claim(dsn=dsn), {'text': '保留這份回答。', 'supported': True, 'citations': [0]}, dsn=dsn)
    voice.finish(voice.claim(dsn=dsn), error='VOICE_PROVIDER_FAILED', dsn=dsn)
    before = deepcopy(voice.read(owner, cid, dsn=dsn)['turns'][0]['answer'])
    voice.action(owner, cid, turn['turn_id'], 'retry', dsn=dsn)
    paths = []
    def provider(path, body):
        paths.append(path);assert path == '/audio';return audio_fixture()
    monkeypatch.setattr(voice, 'provider', provider)
    assert voice.step(dsn=dsn)
    after = voice.read(owner, cid, dsn=dsn)['turns'][0]
    assert after['status'] == 'ready' and after['answer'] == before
    assert paths == ['/audio']


def test_legacy_turn_keeps_its_idempotency_and_audio_behavior(closed_loop):
    from hashlib import sha256
    owner, _, cid, dsn = setup(closed_loop)
    turn = voice.add_turn(owner, cid, 'legacy', '舊文字問題', dsn=dsn)
    with database_session(dsn) as db:
        old = db.get(VoiceTurn, turn['turn_id']);old.mode = None
        old.fingerprint = sha256('舊文字問題'.encode()).hexdigest()
    assert voice.add_turn(owner, cid, 'legacy', '舊文字問題', dsn=dsn) == turn
    voice.finish(voice.claim(dsn=dsn), {'text': '舊回合回答。', 'supported': True, 'citations': [0]}, dsn=dsn)
    assert voice.read(owner, cid, dsn=dsn)['turns'][0]['status'] == 'speaking'


def test_invalid_transcript_does_not_send_question_or_keep_recording(closed_loop, monkeypatch):
    owner, _, cid, dsn = setup(closed_loop)
    turn = voice.add_turn(owner, cid, 'empty-transcript', recording=b'synthetic recording', dsn=dsn)
    paths = []
    def provider(path, body):
        paths.append(path);return {'text': '   '}
    monkeypatch.setattr(voice, 'provider', provider)
    assert voice.step(dsn=dsn) and not voice.step(dsn=dsn)
    result = voice.read(owner, cid, dsn=dsn)['turns'][0]
    assert result['status'] == 'failed' and result['error_code'] == 'VOICE_TRANSCRIPT_INVALID'
    assert result['answer'] is None and paths == ['/transcribe']
    with database_session(dsn) as db: assert db.get(VoiceTurn, turn['turn_id']).recording is None


def test_voice_conversation_keeps_its_revision_after_material_head_changes(revisions):
    learner, material, settings, dsn, add, start, execute, _, original, _ = revisions
    owner = learner.learner_id
    conversation = voice.create(owner, material, 'old-voice-revision', revision=original['revision'], dsn=dsn)
    second = add('New.pdf', 'A queue removes the first inserted element first.')
    start([second], 'new-head-for-voice', original['revision'])
    assert execute().status == 'succeeded'
    old = voice.read(owner, conversation['conversation_id'], dsn=dsn)
    assert not old['is_current_revision'] and old['knowledge_structure_revision'] == original['revision']
    voice.add_turn(owner, conversation['conversation_id'], 'old-question', '舊版本問題', dsn=dsn)
    state = voice.claim(dsn=dsn)
    assert state['revision'] == original['revision']
    assert {c['claim_id'] for c in state['claims']} == {q['claim_id'] for c in original['concepts'] for q in c['claims']}
    voice.finish(state, {'text': '引用舊版本的回答。', 'supported': True, 'citations': [0]}, dsn=dsn)
    assert voice.read(owner, conversation['conversation_id'], dsn=dsn)['turns'][0]['status'] == 'ready'


def test_mode_migration_preserves_legacy_turn_and_saved_audio(closed_loop):
    from hashlib import sha256
    from pathlib import Path
    from sqlalchemy import text
    from runtime.storage.migrations import run_migrations
    owner, _, cid, dsn = setup(closed_loop)
    turn = voice.add_turn(owner, cid, 'legacy-migration', '舊文字提問', dsn=dsn)
    voice.finish(voice.claim(dsn=dsn), {'text': '舊回答。', 'supported': False, 'citations': []}, dsn=dsn)
    with database_session(dsn) as db:
        row = db.get(VoiceTurn, turn['turn_id']);row.audio = audio_fixture()
        row.fingerprint = sha256('舊文字提問'.encode()).hexdigest()
        db.flush()
        db.execute(text('ALTER TABLE voice_turns DROP COLUMN mode'))
        db.execute(text((Path(__file__).parents[3]/'migrations/0018_voice_turn_mode.sql').read_text()))
    assert run_migrations(dsn) == ()
    result = voice.read(owner, cid, dsn=dsn)['turns'][0]
    assert result['mode'] is None and result['status'] == 'ready'
    assert result['answer']['text'] == '舊回答。'
    assert voice.audio(owner, cid, turn['turn_id'], dsn=dsn) == audio_fixture()
    assert voice.add_turn(owner, cid, 'legacy-migration', '舊文字提問', dsn=dsn) == turn


def test_typed_question_in_voice_mode_uses_same_history_and_generates_audio(closed_loop, monkeypatch):
    owner, _, cid, dsn = setup(closed_loop)
    turn = voice.add_turn(owner, cid, 'typed-voice', '用文字輸入的語音模式問題', mode='voice', dsn=dsn)
    with pytest.raises(SourceError, match='IDEMPOTENCY_CONFLICT'):
        voice.add_turn(owner, cid, 'typed-voice', '用文字輸入的語音模式問題', mode='text', dsn=dsn)
    paths = []
    def provider(path, body):
        paths.append(path)
        return {'text':'教材支持的回答。','supported':True,'citations':[0]} if path == '/answer' else audio_fixture()
    monkeypatch.setattr(voice, 'provider', provider)
    assert voice.step(dsn=dsn) and voice.step(dsn=dsn) and not voice.step(dsn=dsn)
    ready = voice.read(owner, cid, dsn=dsn)['turns'][0]
    assert ready['turn_id'] == turn['turn_id'] and ready['audio_url'] and ready['status'] == 'ready'
    assert paths == ['/answer', '/audio']
