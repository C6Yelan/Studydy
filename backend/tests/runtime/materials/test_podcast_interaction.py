"""Podcast → exact-revision Voice／Assessment；只用隔離 DB 與合成內容。"""
from copy import deepcopy
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func, text
from product_fixtures import closed_loop, seed_run, _structure, publish_fixture_structure
from materials.test_podcasts import create, complete, wav
from materials.test_podcast_videos import bundle
from runtime import podcasts, podcast_scenes as scenes, podcast_videos as videos, voice
from runtime.api.app import create_app, ApiSettings
from runtime.podcast_script import SCHEMA, digest
from runtime.podcast_timeline import build_timeline, webvtt
from runtime.scene_alignment import normalized
from runtime.source_normalization import SourceError
from runtime.storage.tables import Podcast, PodcastScenes, PodcastVideo, VoiceTurn, AnswerEvent, database_session
from runtime.storage.migrations import run_migrations
from runtime.material_processing import claim_next_material_processing_run, _record_progress
from learning_adaptation.study_sessions import create_study_session, set_current_study_concept

MASTERING={'policy':'podcast-mastering/v1','integrated_lufs':-19,'true_peak_dbtp':-2,'loudness_range_lu':3}


def beats(fixture, *, spoken=None):
    owner,_,_,_,dsn,_=fixture
    saved=create(fixture)
    while (state:=podcasts.claim_step(dsn=dsn)):
        episode=state['episode']
        if not episode['script']:
            turns=[{'speaker':'host','text':spoken or c['text'],'parts':[{'text':spoken or c['text'],'source_refs':[{
                'source_index':i,'evidence_ids':[e['evidence_id'] for e in c['evidence']]}]}]} for i,c in enumerate(episode['claims'])]
            script={'schema':SCHEMA,'provider':'synthetic','segments':[{'beat_id':'beat-0','title':'整合教學重點','turns':turns}],
                'review':{k:{'passed':True,'reason':'synthetic'} for k in ('correctness','teaching_quality')}}
            podcasts.finish_step(state,script=script,dsn=dsn)
        else:podcasts.finish_step(state,audio=wav(),audio_provider='synthetic',audio_mastering=MASTERING,dsn=dsn)
    return podcasts.read_podcast(owner.learner_id,saved['podcast_id'],dsn=dsn)


def context(view):
    episode=view['episodes'][0]
    return {'podcast_id':str(view['podcast_id']),'episode_index':0,'script_sha256':digest(episode['script']),
        'source_refs':[{'segment_index':0,'turn_index':0,'start':0,'end':len(episode['script']['segments'][0]['turns'][0]['text'])}]}


def new_head(f):
    owner,source,settings,_,dsn,_=f
    run=seed_run(owner.learner_id,source.material_id,'new-head-interaction',settings,dsn=dsn)
    assert claim_next_material_processing_run(dsn=dsn).run.run_id==run.run_id
    for stage in ('evidence','semantics','publishing'):_record_progress(run.run_id,stage,1,1,dsn=dsn)
    document=_structure(str(run.run_id),source.sha256,settings['runtime_lock'],partial=True)
    publish_fixture_structure(owner.learner_id,source.material_id,run.run_id,document,dsn=dsn)
    return document


def test_beats_queue_alignment_and_timeline_without_video(closed_loop):
    owner,_,_,_,dsn,_=closed_loop
    view=beats(closed_loop, spoken='堆疊會依後進先出的順序處理。先放入的項目會留在較後面處理，最後放入的項目會先被取出。例如先放入第一個項目，再放入第二個項目，取出時會先得到第二個項目。')
    assert videos.read(owner.learner_id,view['podcast_id'],0,dsn=dsn)['status']=='pending'
    state=scenes.claim(dsn=dsn);assert state
    spoken=' '.join(t['text'] for t in state['episode']['script']['segments'][0]['turns']);quote=normalized(spoken)[:12]
    alignment={'starts':[0],'anchors':[{'script_offset':0,'text':quote,'audio_start':0,
        'boundary_script_offset':0,'boundary_text':quote,'boundary_audio_start':0}],
        'duration':1,'method':'synthetic','producer':'fixture'}
    from runtime.podcast_cues import align_captions, caption_units
    turns = [t['text'] for s in state['episode']['script']['segments'] for t in s['turns']]
    units = caption_units(turns)
    words = [{'word': unit['text'], 'start': i/len(units), 'end': (i+.8)/len(units)} for i, unit in enumerate(units)]
    alignment['caption_alignment'] = align_captions(turns, words, 1)
    scenes.finish(state,alignment,dsn=dsn)
    manifest=scenes.read(owner.learner_id,view['podcast_id'],dsn=dsn)['episodes'][0]['manifest']
    timeline=build_timeline(view['podcast_id'],0,view['episodes'][0],manifest)
    assert timeline['segments'][0]['beat_ids']==['beat-0']
    assert len(timeline['segments'][0]['source_bindings'])==len(view['episodes'][0]['claims'])
    assert timeline['source_resolver']==view['source_resolver']
    assert webvtt(timeline).startswith('WEBVTT')
    assert len(timeline['captions']) > len(timeline['segments'])
    assert webvtt(timeline).count(' --> ') == len(timeline['captions'])
    assert timeline['segments'][0]['source_refs'] == [
        {'segment_index': 0, 'turn_index': i, 'start': 0, 'end': len(text)} for i, text in enumerate(turns)]


def test_voice_old_revision_context_and_assessment_authority(closed_loop):
    learner,source,_,original,dsn,_=closed_loop;owner=learner.learner_id;view=beats(closed_loop);locator=context(view)
    updated=new_head(closed_loop)
    conversation=voice.create(owner,source.material_id,'from-podcast',revision=original['revision'],dsn=dsn)
    cid=conversation['conversation_id']
    assert voice.create(owner,source.material_id,'from-podcast',revision=original['revision'],dsn=dsn)==conversation
    with pytest.raises(SourceError,match='IDEMPOTENCY_CONFLICT'):
        voice.create(owner,source.material_id,'from-podcast',revision=updated['revision'],dsn=dsn)
    turn=voice.add_turn(owner,cid,'question','這裡的條件是什麼？',context=locator,dsn=dsn)
    assert voice.add_turn(owner,cid,'question','這裡的條件是什麼？',context=locator,dsn=dsn)==turn
    changed=deepcopy(locator);changed['source_refs'][0]['end']-=1
    with pytest.raises(SourceError,match='IDEMPOTENCY_CONFLICT'):voice.add_turn(owner,cid,'question','這裡的條件是什麼？',context=changed,dsn=dsn)
    state=voice.claim(dsn=dsn);assert state['revision']==original['revision']
    voice.finish(state,{'text':'原版本教材支持的回答。','supported':True,'citations':[0]},dsn=dsn)
    voice.finish(voice.claim(dsn=dsn),wav(),dsn=dsn)
    read=voice.read(owner,cid,dsn=dsn)
    assert not read['is_current_revision'] and read['source_resolver']==view['source_resolver']
    with database_session(dsn) as db:
        stored=db.get(VoiceTurn,turn['turn_id']).context
        assert set(stored)=={'podcast_id','episode_index','script_sha256','source_refs','references'}
        assert 'text' not in str(stored) and 'script' not in {k for k in stored if k!='script_sha256'}
        assert db.scalar(select(func.count()).select_from(AnswerEvent))==0
    concept=view['concept_ids'][0]
    study=create_study_session(learner,source.material_id,original['revision'],'podcast-assessment',current_concept_id=concept,dsn=dsn)
    same=create_study_session(learner,source.material_id,original['revision'],'podcast-assessment-again',current_concept_id=concept,dsn=dsn)
    assert same.study_session_id==study.study_session_id and same.knowledge_structure_revision==original['revision']
    with database_session(dsn) as db:assert db.scalar(select(func.count()).select_from(AnswerEvent))==0
    podcasts.delete_podcast(owner,view['podcast_id'],dsn=dsn)
    assert voice.read(owner,cid,dsn=dsn)['turns'][0]['answer']==read['turns'][0]['answer']
    with pytest.raises(SourceError,match='VOICE_PODCAST_CONTEXT_INVALID'):
        voice.add_turn(owner,cid,'after-delete','再說一次？',context=locator,dsn=dsn)


@pytest.mark.parametrize('change',['owner','revision','hash','range','negative','boolean','overlap'])
def test_context_rejects_forgery(closed_loop,change):
    learner,source,_,document,dsn,_=closed_loop;view=beats(closed_loop);locator=context(view)
    cid=voice.create(learner.learner_id,source.material_id,'context-rejection',revision=document['revision'],dsn=dsn)['conversation_id']
    if change=='revision':
        updated=new_head(closed_loop);cid=voice.create(learner.learner_id,source.material_id,'wrong-revision',revision=updated['revision'],dsn=dsn)['conversation_id']
    elif change=='hash':locator['script_sha256']='f'*64
    elif change=='range':locator['source_refs'][0]['end']=99999
    elif change=='negative':locator['source_refs'][0]['segment_index']=-1
    elif change=='boolean':locator['episode_index']=False
    elif change=='overlap':locator['source_refs']*=2
    with pytest.raises(RuntimeError):
        voice.add_turn(uuid4() if change=='owner' else learner.learner_id,cid,'bad','問題',context=locator,dsn=dsn)
    assert voice.read(learner.learner_id,cid,dsn=dsn)['turns']==[]


def test_recording_context_and_late_answer_after_cancel_or_delete(closed_loop):
    learner,source,_,doc,dsn,_=closed_loop;owner=learner.learner_id;view=beats(closed_loop);locator=context(view)
    cid=voice.create(owner,source.material_id,'recording-context',revision=doc['revision'],dsn=dsn)['conversation_id']
    turn=voice.add_turn(owner,cid,'record',recording=b'synthetic',context=locator,dsn=dsn)
    voice.finish(voice.claim(dsn=dsn),{'text':'修改前的問題'},dsn=dsn)
    voice.action(owner,cid,turn['turn_id'],'send','已確認的問題',dsn=dsn)
    state=voice.claim(dsn=dsn);assert state['context']['source_refs']==locator['source_refs']
    voice.action(owner,cid,turn['turn_id'],'cancel',dsn=dsn)
    voice.finish(state,{'text':'晚到回答','supported':True,'citations':[0]},dsn=dsn)
    assert voice.read(owner,cid,dsn=dsn)['turns'][0]['answer'] is None
    voice.add_turn(owner,cid,'second','新問題',context=locator,dsn=dsn);state=voice.claim(dsn=dsn)
    podcasts.delete_podcast(owner,view['podcast_id'],dsn=dsn)
    with pytest.raises(SourceError,match='VOICE_PODCAST_CONTEXT_INVALID'):
        voice.finish(state,{'text':'刪除後不可發布','supported':True,'citations':[0]},dsn=dsn)
    voice.finish(state,error='VOICE_PODCAST_CONTEXT_INVALID',dsn=dsn)
    assert voice.read(owner,cid,dsn=dsn)['turns'][1]['answer'] is None


def test_additive_migration_preserves_legacy_artifacts(closed_loop):
    owner,source,_,_,dsn,_=closed_loop;saved=create(closed_loop);complete(dsn)
    state=videos.claim(dsn=dsn);videos.finish(state,bundle=bundle(state),dsn=dsn)
    cid=voice.create(owner.learner_id,source.material_id,'legacy-conversation',dsn=dsn)['conversation_id']
    voice.add_turn(owner.learner_id,cid,'old-question','舊問題',dsn=dsn)
    with database_session(dsn) as db:
        row=db.get(PodcastVideo,state['video_id']);manifest=deepcopy(row.manifest)
        manifest['policy']='flat-report/v3';manifest['source_check']={'supported':True,'reason':'legacy'};row.manifest=manifest
    before=podcasts.read_podcast(owner.learner_id,saved['podcast_id'],dsn=dsn)
    video_before=videos.ready_manifest(owner.learner_id,saved['podcast_id'],0,dsn=dsn)
    with database_session(dsn) as db:
        db.execute(text('ALTER TABLE voice_turns DROP COLUMN context'))
        db.execute(text('DELETE FROM schema_migrations WHERE version=16'))
    assert run_migrations(dsn)==(16,)
    assert run_migrations(dsn)==()
    assert podcasts.read_podcast(owner.learner_id,saved['podcast_id'],dsn=dsn)==before
    assert videos.ready_manifest(owner.learner_id,saved['podcast_id'],0,dsn=dsn)==video_before
    assert voice.read(owner.learner_id,cid,dsn=dsn)['turns'][0]['context'] is None
    with database_session(dsn) as db:assert not db.scalars(select(PodcastScenes)).all()


def test_voice_context_recording_api_validates_owner_revision_and_ranges(closed_loop,monkeypatch):
    import json
    import runtime.api.app as api
    learner,source,settings,document,dsn,token=closed_loop;view=beats(closed_loop);locator=context(view)
    new_head(closed_loop);monkeypatch.setattr(api,'runtime_binding',lambda _: {})
    origin='http://127.0.0.1:4183';app=create_app(ApiSettings(profile='local',public_origin=origin,secure_cookie=False,local_config=settings,dsn=dsn))
    client=TestClient(app,base_url=origin);headers={'Origin':origin,'Idempotency-Key':'api-context'}
    url=f'/v1/materials/{source.material_id}/voice-conversations'
    assert client.post(url,headers=headers,json={'knowledge_structure_revision':document['revision']}).status_code==401
    client.cookies.set('studydy_session',token)
    assert client.post(url,headers=headers,json={'knowledge_structure_revision':''}).status_code==400
    created=client.post(url,headers=headers,json={'knowledge_structure_revision':document['revision']})
    assert created.status_code==201,created.text
    assert created.json()['knowledge_structure_revision']==document['revision']
    cid=created.json()['conversation_id'];recording_url=f'/v1/voice-conversations/{cid}/recordings'
    malformed={**locator,'episode_index':False}
    assert client.post(recording_url,headers={**headers,'X-Studydy-Podcast-Context':json.dumps(malformed)},content=b'synthetic').status_code==400
    wrong={**locator,'script_sha256':'f'*64}
    rejected=client.post(recording_url,headers={**headers,'X-Studydy-Podcast-Context':json.dumps(wrong)},content=b'synthetic')
    assert rejected.status_code==409 and rejected.json()['reason_code']=='VOICE_PODCAST_CONTEXT_INVALID'
    recorded=client.post(recording_url,headers={**headers,'X-Studydy-Podcast-Context':json.dumps(locator)},content=b'synthetic')
    assert recorded.status_code==202,recorded.text
    voice.finish(voice.claim(dsn=dsn),{'text':'錄音辨識的問題'},dsn=dsn)
    stored=client.get(f'/v1/voice-conversations/{cid}').json()
    assert stored['turns'][0]['context']['source_refs']==locator['source_refs']
    assert stored['source_resolver']==view['source_resolver']
    assert client.post(f"/v1/voice-conversations/{cid}/turns/{recorded.json()['turn_id']}/actions",
        headers={'Origin':origin},json={'action':'send','question':'確認後的問題'}).status_code==200
    state=voice.claim(dsn=dsn)
    assert state['question']=='確認後的問題' and state['revision']==document['revision']
