from uuid import uuid4
from copy import deepcopy
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from product_fixtures import closed_loop
from sources.test_source_revisions import revisions
from runtime import voice
from qa_fixtures import grounded_answer, qa_model
from runtime.errors import SourceError
from runtime.storage.tables import VoiceConversation, VoiceTurn
from runtime.storage.database import database_session
from runtime.material_discard import request_material_discard
import runtime.api.app as api


def setup(f):
    owner,source,_,_,dsn,_=f
    c=voice.create(owner.learner_id,source.material_id,'conversation',dsn=dsn)
    return owner.learner_id,source.material_id,c['conversation_id'],dsn


def test_conversation_reads_do_not_wait_for_another_material_reader(closed_loop):
    from runtime.storage.materials import require_material
    owner, material, identity, dsn = setup(closed_loop)
    with database_session(dsn) as db:
        require_material(db, owner, material, read=True)
        assert voice.listing(owner, material, dsn=dsn)['conversations'][0]['conversation_id'] == identity
        assert voice.read(owner, identity, dsn=dsn)['turns'] == []


def test_voice_answer_persistence_replay_cancel_and_owner(closed_loop):
    owner,material,c,dsn=setup(closed_loop)
    assert voice.create(owner,material,'conversation',dsn=dsn)['conversation_id']==c
    t=voice.add_turn(owner,c,'first','什麼是佇列？',dsn=dsn)
    assert voice.add_turn(owner,c,'first','什麼是佇列？',dsn=dsn)==t
    with pytest.raises(SourceError,match='IDEMPOTENCY_CONFLICT'):voice.add_turn(owner,c,'first','不同',dsn=dsn)
    with pytest.raises(SourceError,match='RESOURCE_NOT_FOUND'):voice.read(uuid4(),c,dsn=dsn)
    state=voice.claim(dsn=dsn)
    assert state['retrieved']=={} and state['history']==[]
    answer=grounded_answer(state,dsn)
    voice.finish(state,answer,dsn=dsn)
    v=voice.read(owner,c,dsn=dsn)
    assert v['turns'][0]['answer']['citations']==answer['citations']
    assert v['turns'][0]['mode']=='text' and v['turns'][0]['status']=='ready'
    assert v['turns'][0]['audio_url'] is None and voice.claim(dsn=dsn) is None
    voice.remove(owner,c,dsn=dsn)
    with pytest.raises(SourceError,match='RESOURCE_NOT_FOUND'):voice.create(owner,material,'conversation',dsn=dsn)


def test_conversation_memory_persists_without_recompressing_or_reading_future(closed_loop, monkeypatch):
    owner,_,cid,dsn=setup(closed_loop)
    old=voice.add_turn(owner,cid,'old-failure','最早未成功的問題',dsn=dsn)
    voice.finish(voice.claim(dsn=dsn),error='VOICE_PROVIDER_FAILED',dsn=dsn)
    for i in range(7):
        voice.add_turn(owner,cid,f'history-{i}',f'條件 {i}',dsn=dsn)
        state=voice.claim(dsn=dsn)
        voice.finish(state,grounded_answer(state,dsn,'舊的完整教材說明。'*70),dsn=dsn)
    calls=[]
    def provider(path, body):
        calls.append(deepcopy(body))
        if body['schema']['required']==['summary']:
            return {'summary':'使用者已提供條件 0 至 6，曾討論教材概念。'}
        return qa_model(body)
    monkeypatch.setattr(voice,'provider',provider)
    voice.add_turn(owner,cid,'compact','沿用早期條件',dsn=dsn)
    assert voice.step(dsn=dsn)
    with database_session(dsn) as db:
        memory=deepcopy(db.get(VoiceConversation,cid).memory)
    assert memory and any(c['schema']['required']==['summary'] for c in calls)
    assert len(voice.read(owner,cid,dsn=dsn)['turns'])==9
    # 對話標題固定是第一個問題，後續提問不改名。
    assert voice.read(owner,cid,dsn=dsn)['title']=='最早未成功的問題'
    calls.clear()
    voice.add_turn(owner,cid,'reuse','繼續',dsn=dsn)
    assert voice.step(dsn=dsn)
    assert all(c['schema']['required']!=['summary'] for c in calls)
    assert calls[0]['request']['history']['summary']==memory['summary']
    voice.action(owner,cid,old['turn_id'],'retry',dsn=dsn)
    state=voice.claim(dsn=dsn)
    assert state['memory'] is None and state['history']==[]
    voice.finish(state,grounded_answer(state,dsn),dsn=dsn)
    with database_session(dsn) as db:
        assert db.get(VoiceConversation,cid).memory==memory
    pending=voice.add_turn(owner,cid,'cancel-memory','取消測試',dsn=dsn)
    state=voice.claim(dsn=dsn)
    state['memory_update']={**memory,'summary':'不能發布的摘要'}
    voice.action(owner,cid,pending['turn_id'],'cancel',dsn=dsn)
    voice.finish(state,grounded_answer(state,dsn),dsn=dsn)
    with database_session(dsn) as db:
        assert db.get(VoiceConversation,cid).memory==memory
    voice.remove(owner,cid,dsn=dsn)
    with database_session(dsn) as db:
        assert db.get(VoiceConversation,cid).memory is None


def test_missing_index_is_rejected_and_cannot_leave_an_unclaimable_turn(closed_loop):
    from sqlalchemy import delete
    from runtime.storage.tables import MaterialRetrievalIndex
    owner,material,cid,dsn=setup(closed_loop)
    turn=voice.add_turn(owner,cid,'before-index-loss','Stack 是什麼？',dsn=dsn)
    with database_session(dsn) as db:
        db.execute(delete(MaterialRetrievalIndex).where(MaterialRetrievalIndex.material_id==material))
    assert voice.claim(dsn=dsn) is None
    with database_session(dsn) as db:
        saved=db.get(VoiceTurn,turn['turn_id'])
        assert saved.status=='failed' and saved.error_code=='RETRIEVAL_INDEX_REQUIRED' and saved.lease_token is None
    with pytest.raises(SourceError,match='RETRIEVAL_INDEX_REQUIRED'):
        voice.add_turn(owner,cid,'after-index-loss','另一題',dsn=dsn)


def test_transcript_auto_continues_and_invalid_reference_is_rejected(closed_loop):
    owner,_,c,dsn=setup(closed_loop)
    t=voice.add_turn(owner,c,'record',recording=b'synthetic media',dsn=dsn)
    state=voice.claim(dsn=dsn)
    voice.finish(state,{'text':'修改前的問題'},dsn=dsn)
    with database_session(dsn) as db:assert db.get(VoiceTurn,t['turn_id']).recording is None
    state=voice.claim(dsn=dsn)
    assert state['question']=='修改前的問題'
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
    h={'Origin':origin,'Idempotency-Key':'voice-api','X-Studydy-Browser-Id':str(uuid4())}
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
    voice.finish(renewed,grounded_answer(renewed,dsn,'有效的回答'),dsn=dsn)
    assert voice.read(owner,cid,dsn=dsn)['turns'][0]['answer']['text']=='有效的回答'


def audio_fixture():
    import io, wave
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(24000)
        wav.writeframes(b'\x00\x01' * 24000)
    return output.getvalue()


def test_saved_voice_audio_supports_seeking_without_bypassing_owner(closed_loop):
    from runtime.learner_session import register_account
    _,_,settings,_,dsn,token=closed_loop
    owner,_,cid,_=setup(closed_loop)
    turn=voice.add_turn(owner,cid,'range-audio','教材問題',mode='voice',dsn=dsn)
    state=voice.claim(dsn=dsn)
    voice.finish(state,grounded_answer(state,dsn),dsn=dsn)
    voice.finish(voice.claim(dsn=dsn),audio_fixture(),dsn=dsn)
    origin='http://127.0.0.1:4183'
    app=api.create_app(api.ApiSettings(profile='local',public_origin=origin,secure_cookie=False,local_config=settings,dsn=dsn))
    client=TestClient(app,base_url=origin)
    url=f'/v1/voice-conversations/{cid}/turns/{turn["turn_id"]}/audio'
    assert client.get(url,headers={'Range':'invalid'}).status_code==401
    client.cookies.set('studydy_session',token)
    full=client.get(url)
    assert full.status_code==200 and full.content==audio_fixture()
    assert full.headers['accept-ranges']=='bytes' and full.headers['cache-control']=='private, no-store'
    for value,expected in [('bytes=0-43',audio_fixture()[:44]),('bytes=44-',audio_fixture()[44:]),('bytes=-20',audio_fixture()[-20:])]:
        response=client.get(url,headers={'Range':value})
        assert response.status_code==206 and response.content==expected
        assert response.headers['content-range'].endswith(f'/{len(audio_fixture())}')
        assert int(response.headers['content-length'])==len(expected)
    for value in ('bytes=999999-','bytes=-0','bytes=20-1','bytes=1-2,5-6','invalid'):
        response=client.get(url,headers={'Range':value})
        assert response.status_code==416 and not response.content
    stranger=register_account('voice-range-other@example.com','Synthetic other password 42',dsn=dsn)
    client.cookies.set('studydy_session',stranger.raw_token)
    assert client.get(url,headers={'Range':'bytes=0-43'}).status_code==404
    assert voice.audio(owner,cid,turn['turn_id'],dsn=dsn)==audio_fixture()


def test_voice_worker_auto_chains_transcript_answer_audio_without_send(closed_loop, monkeypatch):
    owner, _, cid, dsn = setup(closed_loop)
    turn = voice.add_turn(owner, cid, 'voice-auto', recording=b'synthetic recording', dsn=dsn)
    paths = []
    def provider(path, body):
        paths.append(path)
        if path == '/transcribe':
            assert isinstance(body['hints'],str) and len(body['hints'])<=320
            return {'text': '辨識出的教材問題','hints':body['hints'],'segments':[
                {'text':'辨識出的教材問題','start':0.0,'end':2.0,'avg_logprob':-.2,'no_speech_prob':.01,'compression_ratio':1.0}]}
        if path == '/semantics':
            assert body['request']['question'] == '辨識出的教材問題'
            return qa_model(body)
        assert path == '/audio'
        return audio_fixture()
    monkeypatch.setattr(voice, 'provider', provider)
    assert voice.step(dsn=dsn)
    result = voice.read(owner, cid, dsn=dsn)
    assert result['title'] == '辨識出的教材問題'
    assert result['turns'][0]['status'] == 'pending'
    assert result['turns'][0]['question'] == '辨識出的教材問題'
    assert result['turns'][0]['mode'] == 'voice'
    assert result['turns'][0]['transcription']['segments'][0]['avg_logprob']==-.2
    with database_session(dsn) as db: assert db.get(VoiceTurn, turn['turn_id']).recording is None
    assert voice.step(dsn=dsn)
    assert voice.read(owner, cid, dsn=dsn)['turns'][0]['status'] == 'speaking'
    assert voice.step(dsn=dsn)
    ready = voice.read(owner, cid, dsn=dsn)['turns'][0]
    assert ready['status'] == 'ready' and ready['audio_url']
    assert voice.audio(owner, cid, turn['turn_id'], dsn=dsn) == audio_fixture()
    assert paths == ['/transcribe', '/semantics', '/semantics', '/semantics', '/audio']
    assert not voice.step(dsn=dsn)


def test_text_worker_finishes_without_audio_provider_and_mode_is_part_of_intent(closed_loop, monkeypatch):
    owner, _, cid, dsn = setup(closed_loop)
    turn = voice.add_turn(owner, cid, 'same-intent', 'same bytes', dsn=dsn)
    with pytest.raises(SourceError, match='IDEMPOTENCY_CONFLICT'):
        voice.add_turn(owner, cid, 'same-intent', recording=b'same bytes', dsn=dsn)
    paths = []
    def provider(path, body):
        paths.append(path)
        assert path == '/semantics', 'Text mode must not request TTS'
        return qa_model(body, '純文字回答。')
    monkeypatch.setattr(voice, 'provider', provider)
    assert voice.step(dsn=dsn) and not voice.step(dsn=dsn)
    ready = voice.read(owner, cid, dsn=dsn)['turns'][0]
    assert ready['turn_id'] == turn['turn_id'] and ready['mode'] == 'text'
    assert ready['status'] == 'ready' and ready['audio_url'] is None
    assert paths == ['/semantics', '/semantics', '/semantics']


def test_recording_keeps_material_topic_when_recent_answer_changes_topic(closed_loop):
    from runtime.storage.tables import Material
    owner,material,cid,dsn=setup(closed_loop)
    title='檢索與 LLM 入門'
    with database_session(dsn) as db:
        db.get(Material,material).display_name=title
    voice.add_turn(owner,cid,'previous-topic','什麼是堆疊？',dsn=dsn)
    state=voice.claim(dsn=dsn)
    answer=grounded_answer(state,dsn)
    answer['speech_terms']=['堆疊','LIFO']
    voice.finish(state,answer,dsn=dsn)
    voice.add_turn(owner,cid,'new-recording',recording=b'synthetic recording',dsn=dsn)
    state=voice.claim(dsn=dsn)
    assert state['status']=='transcribing'
    assert title in state['stt_hints'] and '堆疊' in state['stt_hints'] and 'LIFO' in state['stt_hints']
    assert len(state['stt_hints'])<=320


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
        voice.finish(state, grounded_answer(state,dsn,'已保存的回答。'), dsn=dsn)
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
    state=voice.claim(dsn=dsn)
    voice.finish(state, grounded_answer(state,dsn,'保留這份回答。'), dsn=dsn)
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
    answer=grounded_answer(state,dsn,'引用舊版本的回答。')
    assert answer['citations'][0]['evidence'][0]['evidence_id'] in {e['evidence_id'] for e in original['evidence']}
    voice.finish(state, answer, dsn=dsn)
    assert voice.read(owner, conversation['conversation_id'], dsn=dsn)['turns'][0]['status'] == 'ready'




def test_typed_question_in_voice_mode_uses_same_history_and_generates_audio(closed_loop, monkeypatch):
    owner, _, cid, dsn = setup(closed_loop)
    turn = voice.add_turn(owner, cid, 'typed-voice', '用文字輸入的語音模式問題', mode='voice', dsn=dsn)
    with pytest.raises(SourceError, match='IDEMPOTENCY_CONFLICT'):
        voice.add_turn(owner, cid, 'typed-voice', '用文字輸入的語音模式問題', mode='text', dsn=dsn)
    paths = []
    def provider(path, body):
        paths.append(path)
        return qa_model(body, '教材支持的回答。') if path == '/semantics' else audio_fixture()
    monkeypatch.setattr(voice, 'provider', provider)
    assert voice.step(dsn=dsn) and voice.step(dsn=dsn) and not voice.step(dsn=dsn)
    ready = voice.read(owner, cid, dsn=dsn)['turns'][0]
    assert ready['turn_id'] == turn['turn_id'] and ready['audio_url'] and ready['status'] == 'ready'
    assert paths == ['/semantics', '/semantics', '/semantics', '/audio']
