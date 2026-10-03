from copy import deepcopy
from uuid import uuid4
import pytest
from sqlalchemy import select
from product_fixtures import closed_loop
from materials.test_podcasts import create,complete
from runtime import podcast_scenes as scenes,podcasts
from runtime.scene_alignment import normalized
from runtime.source_normalization import SourceError
from runtime.storage.tables import PodcastScenes,Podcast,database_session


def ready(fixture):
    p=create(fixture);complete(fixture[4]);return p['podcast_id']


def alignment(state):
    episode=state['episode'];texts=[normalized(' '.join(t['text'] for t in s['turns'])) for s in episode['script']['segments']]
    duration=episode['audio']['duration_seconds'];starts=[i*duration/len(texts) for i in range(len(texts))]
    return {'starts':starts,'anchors':[{'script_offset':0,'text':t[:min(16,len(t))],'audio_start':starts[i],'boundary_script_offset':0,'boundary_text':t[:min(16,len(t))],'boundary_audio_start':starts[i]} for i,t in enumerate(texts)],
        'duration':duration,'method':'synthetic-test','producer':'synthetic-test'}


def test_prepare_publish_reopen_and_preserve_audio(closed_loop):
    owner,_,_,_,dsn,_=closed_loop;identity=ready(closed_loop)
    before=podcasts.read_podcast(owner.learner_id,identity,dsn=dsn)
    first=scenes.prepare(owner.learner_id,identity,dsn=dsn)
    assert scenes.prepare(owner.learner_id,identity,dsn=dsn)==first
    state=scenes.claim(dsn=dsn);value=alignment(state)
    assert scenes.save_alignment(state,value,dsn=dsn)
    scenes.finish(state,value,dsn=dsn)
    result=scenes.read(owner.learner_id,identity,dsn=dsn)
    manifest=result['episodes'][0]['manifest']
    assert result['episodes'][0]['status']=='ready'
    assert manifest['alignment_producer']=='synthetic-test'
    assert [s['claim_id'] for s in manifest['scenes']]==[c['claim_id'] for c in before['episodes'][0]['claims']]
    assert podcasts.read_podcast(owner.learner_id,identity,dsn=dsn)==before
    with pytest.raises(podcasts.PodcastError,match='RESOURCE_NOT_FOUND'):scenes.read(uuid4(),identity,dsn=dsn)


def test_cancel_and_delete_reject_late_scene_results(closed_loop):
    owner,_,_,_,dsn,_=closed_loop;identity=ready(closed_loop)
    scenes.prepare(owner.learner_id,identity,dsn=dsn);state=scenes.claim(dsn=dsn)
    scenes.action(owner.learner_id,identity,0,'cancel',1,dsn=dsn)
    scenes.finish(state,alignment(state),dsn=dsn)
    assert scenes.read(owner.learner_id,identity,dsn=dsn)['episodes'][0]['status']=='cancelled'
    scenes.action(owner.learner_id,identity,0,'retry',2,dsn=dsn);state=scenes.claim(dsn=dsn)
    podcasts.delete_podcast(owner.learner_id,identity,dsn=dsn)
    with pytest.raises(podcasts.PodcastError,match='RESOURCE_NOT_FOUND'):scenes.finish(state,alignment(state),dsn=dsn)
    with database_session(dsn) as db:assert not db.scalars(select(PodcastScenes)).all()


def test_malformed_timing_and_changed_audio_are_not_published(closed_loop):
    owner,_,_,_,dsn,_=closed_loop;identity=ready(closed_loop)
    scenes.prepare(owner.learner_id,identity,dsn=dsn);state=scenes.claim(dsn=dsn)
    invalid=alignment(state);invalid['duration']+=3
    with pytest.raises(SourceError,match='SCENE_ALIGNMENT_INVALID'):scenes.save_alignment(state,invalid,dsn=dsn)
    valid=alignment(state)
    with database_session(dsn) as db:
        row=db.get(Podcast,identity);episodes=deepcopy(row.episodes);episodes[0]['audio']['sha256']='0'*64;row.episodes=episodes
    scenes.finish(state,valid,dsn=dsn)
    assert scenes.read(owner.learner_id,identity,dsn=dsn)['episodes'][0]['status']=='unprepared'
    with database_session(dsn) as db:assert db.get(PodcastScenes,state['scene_id']).manifest is None


def test_worker_consumes_audio_and_saves_actual_provider_identity(closed_loop,monkeypatch):
    owner,_,_,_,dsn,_=closed_loop;identity=ready(closed_loop)
    scenes.prepare(owner.learner_id,identity,dsn=dsn)
    calls=[]
    def provider(path,body):
        assert path=='/align' and body['audio'] and body['texts']
        calls.append(path)
        n=len(body['texts']);starts=[i/n for i in range(n)]
        return {'starts':starts,'anchors':[{'script_offset':0,'text':normalized(t)[:12],'audio_start':starts[i],'boundary_script_offset':0,'boundary_text':normalized(t)[:12],'boundary_audio_start':starts[i]} for i,t in enumerate(body['texts'])],
            'duration':1,'method':'synthetic-test','producer':'synthetic-provider'}
    monkeypatch.setattr(scenes,'provider',provider)
    assert scenes.step(dsn=dsn)
    view=scenes.read(owner.learner_id,identity,dsn=dsn)
    assert view['episodes'][0]['status']=='ready'
    assert view['episodes'][0]['manifest']['alignment_producer']=='synthetic-provider'
    assert calls==['/align']
