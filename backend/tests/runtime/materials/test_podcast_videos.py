from copy import deepcopy
from datetime import UTC,datetime,timedelta
from hashlib import sha256
import base64
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from product_fixtures import closed_loop
from materials.test_podcasts import create,complete
from runtime import podcasts,podcast_videos as videos
from runtime.api.app import ApiSettings,create_app
from runtime.learner_session import register_account
from runtime.material_discard import request_material_discard
from runtime.podcast_video_plan import POLICY
from runtime.scene_alignment import normalized
from runtime.source_normalization import SourceError
from runtime.storage.tables import Artifact,Podcast,PodcastVideo,database_session


def bundle(state):
    episode=state['episode'];turns=[t for s in episode['script']['segments'] for t in s['turns']]
    cues=[{'title':f'重點{i+1}','parts':[{'turn_index':i,'text':turn['text']}]} for i,turn in enumerate(turns)]
    duration=episode['audio']['duration_seconds'];starts=[i*duration/len(cues) for i in range(len(cues))]
    anchors=[{'script_offset':0,'text':normalized(t['text'])[:24],'audio_start':starts[i],
              'boundary_script_offset':0,'boundary_text':normalized(t['text'])[:24],'boundary_audio_start':starts[i]} for i,t in enumerate(turns)]
    raw=b'\x00\x00\x00\x18ftyp'+b'synthetic-test-video'*10
    plan={'pages':[{'title':'合成影片','start_cue':0,'end_cue':len(cues)-1,'elements':[
        {'kind':'box','cue_index':0,'text':'合成測試','x':100,'y':250,'w':600,'h':200,'size':42,'color':'teal','filled':False}]}]}
    return {'policy':POLICY,'model':'synthetic-test','cues':cues,'plan':plan,
            'alignment':{'starts':starts,'anchors':anchors,'duration':duration,'method':'synthetic','producer':'synthetic'},
            'review':{'supported':True,'reason':'synthetic fixture, not a quality evaluation'},
            'render':{'width':1920,'height':1080,'fps':60,'duration':duration,'sha256':sha256(raw).hexdigest()},
            'video':base64.b64encode(raw).decode()}


def ready(fixture):
    identity=create(fixture)['podcast_id'];complete(fixture[4]);return identity


def test_audio_automatically_queues_one_video_and_published_video_survives_reopen(closed_loop):
    owner,_,_,_,dsn,_=closed_loop;identity=ready(closed_loop)
    original=podcasts.read_podcast(owner.learner_id,identity,dsn=dsn)['episodes']
    state=videos.read(owner.learner_id,identity,0,dsn=dsn)
    assert state['status']=='pending'
    assert videos.prepare(owner.learner_id,identity,0,dsn=dsn)['version']==state['version']
    claim=videos.claim(dsn=dsn);assert videos.claim(dsn=dsn) is None
    assert videos.finish(claim,bundle=bundle(claim),dsn=dsn)
    published=videos.read(owner.learner_id,identity,0,dsn=dsn)
    assert published['status']=='ready' and published['video']['fps']==60
    assert videos.ready_manifest(owner.learner_id,identity,0,dsn=dsn)['timeline']['granularity']=='script_cue'
    assert podcasts.read_podcast(owner.learner_id,identity,dsn=dsn)['episodes']==original


def test_cancel_and_delete_fence_late_video_without_changing_audio(closed_loop):
    owner,_,_,_,dsn,_=closed_loop;identity=ready(closed_loop);claim=videos.claim(dsn=dsn)
    before=podcasts.read_podcast(owner.learner_id,identity,dsn=dsn)['episodes']
    view=videos.read(owner.learner_id,identity,0,dsn=dsn)
    videos.action(owner.learner_id,identity,0,'cancel',view['version'],dsn=dsn)
    assert not videos.finish(claim,bundle=bundle(claim),dsn=dsn)
    view=videos.read(owner.learner_id,identity,0,dsn=dsn)
    videos.action(owner.learner_id,identity,0,'retry',view['version'],dsn=dsn)
    claim=videos.claim(dsn=dsn);videos.finish(claim,bundle=bundle(claim),dsn=dsn)
    assert podcasts.read_podcast(owner.learner_id,identity,dsn=dsn)['episodes']==before
    manifest=videos.ready_manifest(owner.learner_id,identity,0,dsn=dsn)
    podcasts.delete_podcast(owner.learner_id,identity,dsn=dsn)
    with database_session(dsn) as db:
        assert db.get(Artifact,manifest['artifact_id']) is None
        assert db.scalar(select(PodcastVideo)) is None


def test_expired_request_is_failed_without_replaying_provider(closed_loop):
    owner,_,_,_,dsn,_=closed_loop;identity=ready(closed_loop);claim=videos.claim(dsn=dsn)
    with database_session(dsn) as db:db.get(PodcastVideo,claim['video_id']).lease_expires_at=datetime.now(UTC)-timedelta(seconds=1)
    assert videos.claim(dsn=dsn) is None
    assert videos.read(owner.learner_id,identity,0,dsn=dsn)['error_code']=='VIDEO_INTERRUPTED'
    assert not videos.finish(claim,bundle=bundle(claim),dsn=dsn)


def test_stale_source_and_source_review_failure_do_not_publish(closed_loop):
    owner,_,_,_,dsn,_=closed_loop;identity=ready(closed_loop);claim=videos.claim(dsn=dsn)
    value=bundle(claim);value['review']['supported']=False
    with pytest.raises(SourceError,match='VIDEO_RESULT_INVALID'):videos.finish(claim,bundle=value,dsn=dsn)
    with database_session(dsn) as db:
        p=db.get(Podcast,identity);episodes=deepcopy(p.episodes);episodes[0]['audio']['sha256']='f'*64;p.episodes=episodes
    assert videos.finish(claim,bundle=bundle(claim),dsn=dsn)
    assert videos.read(owner.learner_id,identity,0,dsn=dsn)['video'] is None
    with database_session(dsn) as db:assert db.scalar(select(Artifact).where(Artifact.kind=='podcast_video')) is None


def test_video_worker_reports_provider_failure_but_audio_remains_ready(closed_loop,monkeypatch):
    owner,_,_,_,dsn,_=closed_loop;identity=ready(closed_loop)
    def failed(*args):raise SourceError('VIDEO_DISK_SPACE_LOW')
    monkeypatch.setattr(videos,'provider',failed)
    assert videos.step(dsn=dsn)
    assert videos.read(owner.learner_id,identity,0,dsn=dsn)['error_code']=='VIDEO_DISK_SPACE_LOW'
    assert podcasts.read_podcast(owner.learner_id,identity,dsn=dsn)['status']=='ready'


def test_video_api_owner_ranges_and_timeline(closed_loop,monkeypatch):
    import runtime.api.app as api
    monkeypatch.setattr(api,'runtime_binding',lambda _: {})
    owner,_,settings,_,dsn,token=closed_loop;identity=ready(closed_loop)
    claim=videos.claim(dsn=dsn);videos.finish(claim,bundle=bundle(claim),dsn=dsn)
    origin='http://127.0.0.1:4173';client=TestClient(create_app(ApiSettings(profile='local',public_origin=origin,secure_cookie=False,local_config=settings,dsn=dsn)),base_url=origin)
    root=f'/v1/podcasts/{identity}/episodes/0'
    assert client.get(root+'/video/media').status_code==401
    client.cookies.set('studydy_session',token)
    assert client.get(root+'/video').json()['status']=='ready'
    response=client.get(root+'/video/media',headers={'Range':'bytes=4-7'})
    assert response.status_code==206 and response.content==b'ftyp'
    assert response.headers['cache-control']=='private, no-store'
    assert client.get(root+'/timeline').json()['granularity']=='script_cue'
    assert client.get(root+'/subtitles').text.startswith('WEBVTT')
    stranger=register_account('video-other@example.com','Valid-password-12',dsn=dsn)
    client.cookies.set('studydy_session',stranger.raw_token)
    assert client.get(root+'/video/media').status_code==404


def test_material_removal_cascades_video_and_quarantines_media(closed_loop):
    owner,source,_,_,dsn,_=closed_loop;identity=ready(closed_loop)
    claim=videos.claim(dsn=dsn);videos.finish(claim,bundle=bundle(claim),dsn=dsn)
    assert request_material_discard(owner.learner_id,source.material_id,dsn=dsn)=='removed'
    with database_session(dsn) as db:
        assert db.scalar(select(PodcastVideo)) is None
        assert db.scalar(select(Artifact)) is None


@pytest.mark.parametrize('mode,delivery',[('full','solo'),('quick','dialogue'),('full','dialogue')])
def test_modes_automatically_queue_video_and_keep_original_roles(closed_loop,mode,delivery):
    owner,source,_,document,dsn,_=closed_loop
    saved=podcasts.create_podcast(owner.learner_id,source.material_id,document['revision'],'合成模式驗證',
        [c['concept_id'] for c in document['concepts']],mode,'video-modes-'+mode+'-'+delivery,delivery=delivery,dsn=dsn)
    state=podcasts.claim_step(dsn=dsn)
    script={'provider':'synthetic','segments':[{'claim_id':c['claim_id'],'turns':
        [{'speaker':'host','text':'這個觀念要如何解釋？'},{'speaker':'guest','text':c['text']}]
        if delivery=='dialogue' else [{'speaker':'host','text':c['text']}]}
        for c in state['episode']['claims']]}
    assert podcasts.finish_step(state,script=script,dsn=dsn)
    from materials.test_podcasts import wav
    state=podcasts.claim_step(dsn=dsn)
    assert podcasts.finish_step(state,audio=wav(),audio_provider='synthetic',dsn=dsn)
    assert videos.read(owner.learner_id,saved['podcast_id'],0,dsn=dsn)['status']=='pending'
    state=videos.claim(dsn=dsn);assert videos.finish(state,bundle=bundle(state),dsn=dsn)
    manifest=videos.ready_manifest(owner.learner_id,saved['podcast_id'],0,dsn=dsn)
    assert [t for cue in manifest['timeline']['segments'] for t in cue['turns']]==[t for s in script['segments'] for t in s['turns']]
    assert podcasts.read_podcast(owner.learner_id,saved['podcast_id'],dsn=dsn)['mode']==mode
