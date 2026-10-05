"""真 API／隔離 DB 的 Podcast→Voice→Assessment browser；provider 完全受控。"""
import json
from threading import Event, Thread
from sqlalchemy import select, func
import runtime.api.app as api
from runtime import voice, podcast_scenes as scenes
from runtime.scene_alignment import normalized
from runtime.storage.tables import VoiceConversation, VoiceTurn, StudySession, AnswerEvent, database_session
from browser_e2e_runner import PORT, local_api, main as run_browser
from product_fixtures import closed_loop
from materials.test_podcasts import wav
from materials.test_podcast_interaction import beats, new_head


def test_podcast_voice_assessment_browser_uses_original_revision(closed_loop,monkeypatch):
    learner,source,settings,document,dsn,_=closed_loop
    view=beats(closed_loop, spoken='堆疊會依後進先出的順序處理。先放入的項目會留在較後面處理，最後放入的項目會先被取出。例如先放入第一個項目，再放入第二個項目，取出時會先得到第二個項目。')
    state=scenes.claim(dsn=dsn)
    quote=normalized(' '.join(t['text'] for t in state['episode']['script']['segments'][0]['turns']))[:12]
    from runtime.podcast_cues import align_captions, caption_units
    turns = [t['text'] for s in state['episode']['script']['segments'] for t in s['turns']]
    units = caption_units(turns)
    words = [{'word': unit['text'], 'start': i/len(units), 'end': (i+.8)/len(units)} for i, unit in enumerate(units)]
    scenes.finish(state,{'starts':[0],'anchors':[{'script_offset':0,'text':quote,'audio_start':0,
        'boundary_script_offset':0,'boundary_text':quote,'boundary_audio_start':0}],
        'duration':1,'method':'synthetic','producer':'fixture',
        'caption_alignment':align_captions(turns, words, 1)},dsn=dsn)
    new_head(closed_loop)
    requests=[]
    def provider(path,body):
        requests.append(path)
        if path=='/answer':
            assert body['context']['turns'] and body['claims']
            return {'text':'這個回答引用原版本教材的必要條件。','supported':True,'citations':[0]}
        if path=='/audio':return wav()
        raise AssertionError('UNEXPECTED_PROVIDER_REQUEST')
    monkeypatch.setattr(voice,'provider',provider)
    monkeypatch.setattr(api,'runtime_binding',lambda _: {})
    app=api.create_app(api.ApiSettings(profile='local',public_origin=f'http://127.0.0.1:{PORT}',
        secure_cookie=False,local_config=settings,dsn=dsn))
    monkeypatch.setenv('STUDYDY_E2E_PODCAST_INTERACTION','true')
    monkeypatch.setenv('STUDYDY_E2E_PODCAST_DATA',json.dumps({'podcast':str(view['podcast_id']),
        'material':str(source.material_id),'revision':document['revision'],'run':view['run_id']}))
    stop=Event();errors=[]
    def worker():
        while not stop.wait(.05):
            try:voice.step(dsn=dsn)
            except Exception as error:errors.append(type(error).__name__);return
    thread=Thread(target=worker);thread.start()
    try:
        with local_api(app):assert run_browser('e2e/api/podcast-interaction.spec.ts',timeout_seconds=120)==0
    finally:stop.set();thread.join(timeout=10)
    assert not thread.is_alive() and not errors and requests==['/answer','/audio']
    with database_session(dsn) as db:
        conversation=db.scalar(select(VoiceConversation));turn=db.scalar(select(VoiceTurn));study=db.scalar(select(StudySession))
        assert conversation.knowledge_structure_revision==study.knowledge_structure_revision==document['revision']
        assert turn.status=='ready' and turn.context['podcast_id']==str(view['podcast_id'])
        assert db.scalar(select(func.count()).select_from(AnswerEvent))==0
