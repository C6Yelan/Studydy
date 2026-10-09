"""每集音訊完成後自動排隊影片；來源失效、取消或刪除後，晚到結果不得發布。"""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4
import base64
import json
import logging
import math
import os
from threading import Event, Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from sqlalchemy import delete, select, text
from pdf_evidence.ocr_page_evidence import canonical_sha256
from . import podcasts
from .podcast_video_plan import POLICY, script_digest, timeline_for_video, validate_plan, validate_visual_timing
from .source_normalization import SourceError
from .storage.artifacts import quarantine_source_pdf
from .storage.knowledge_structures import _read_verified_document
from .storage.source_artifacts import open_verified_artifact, write_blob
from .storage.tables import Artifact, Material, Podcast, PodcastVideo as Video, database_session

WORK_LOCK = 851_048_188_100_421_213


def identity(podcast, episode):
    return canonical_sha256({'revision':podcast.knowledge_structure_revision,'audio':episode['audio'],
                             'script':episode['script'],'claims':episode['claims']})


def _episode(podcast, index):
    if not 0 <= index < len(podcast.episodes):raise SourceError('RESOURCE_NOT_FOUND')
    return podcast.episodes[index]


def _row(db, podcast_id, index):
    return db.scalar(select(Video).where(Video.podcast_id==podcast_id,Video.episode_index==index).with_for_update())


def enqueue(db, podcast, index):
    episode=_episode(podcast,index)
    if not episode['audio'] or not episode['script']:raise SourceError('SOURCE_NOT_READY')
    row=_row(db,podcast.podcast_id,index)
    if row:
        if row.source_sha256!=identity(podcast,episode):raise SourceError('VIDEO_SOURCE_CHANGED')
        return row
    row=Video(video_id=uuid4(),podcast_id=podcast.podcast_id,episode_index=index,
              source_sha256=identity(podcast,episode),status='pending',version=1,created_at=datetime.now(UTC))
    db.add(row);db.flush();return row


def _view(podcast,index,row):
    episode=_episode(podcast,index)
    current=bool(row and episode['audio'] and row.source_sha256==identity(podcast,episode))
    status=row.status if current else ('unprepared' if episode['audio'] else 'waiting_audio')
    result={'schema':'podcast-video/v1','podcast_id':podcast.podcast_id,'episode_index':index,
            'status':status,'version':row.version if row else 0,
            'error_code':row.error_code if current else ('VIDEO_SOURCE_CHANGED' if row else None),'video':None}
    if status=='ready':
        manifest=row.manifest
        result['video']={key:deepcopy(manifest[key]) for key in ('audio_sha256','script_sha256','width','height','fps','duration','pages')}
        result['video'].update(sha256=manifest['sha256'],artifact_id=row.artifact_id)
    return result


def read(owner,podcast_id,index,*,dsn=None):
    with database_session(dsn) as db:
        podcast,_=podcasts._locked(db,owner,podcast_id,read=True)
        return _view(podcast,index,db.scalar(select(Video).where(Video.podcast_id==podcast_id,Video.episode_index==index)))


def prepare(owner,podcast_id,index,*,dsn=None):
    with database_session(dsn) as db:
        podcast,_=podcasts._locked(db,owner,podcast_id)
        return _view(podcast,index,enqueue(db,podcast,index))


def action(owner,podcast_id,index,action,version,*,dsn=None):
    with database_session(dsn) as db:
        podcast,_=podcasts._locked(db,owner,podcast_id)
        episode=_episode(podcast,index);row=_row(db,podcast_id,index)
        if not row or row.version!=version:raise SourceError('VIDEO_CONFLICT')
        if action=='cancel' and row.status in ('pending','running'):row.status='cancelled'
        elif action=='retry' and row.status in ('failed','cancelled') and episode['audio'] and row.source_sha256==identity(podcast,episode):
            row.status='pending';row.error_code=None
        else:raise SourceError('VIDEO_CONFLICT')
        row.lease_token=row.lease_expires_at=None;row.version+=1
        return _view(podcast,index,row)


def ready_manifest(owner,podcast_id,index,*,dsn=None):
    with database_session(dsn) as db:
        podcast,_=podcasts._locked(db,owner,podcast_id,read=True)
        row=db.scalar(select(Video).where(Video.podcast_id==podcast_id,Video.episode_index==index))
        view=_view(podcast,index,row)
        if view['status']!='ready':return None
        return {'artifact_id':row.artifact_id,**deepcopy(row.manifest)}


def discard(db,podcast_id):
    rows=db.scalars(select(Video).where(Video.podcast_id==podcast_id).with_for_update()).all()
    artifacts=[row.artifact_id for row in rows if row.artifact_id]
    db.execute(delete(Video).where(Video.podcast_id==podcast_id));db.flush()
    for artifact_id in artifacts:
        quarantine_source_pdf(db,artifact_id)
        db.delete(db.get(Artifact,artifact_id))
    return artifacts


def claim(*,dsn=None):
    with database_session(dsn) as db:
        if not db.scalar(text('SELECT pg_try_advisory_xact_lock(:key)'),{'key':WORK_LOCK}):return None
        now=datetime.now(UTC)
        # 多個 API 程序共用同一條影片工作列，不同時占用 CPU 或重複呼叫模型。
        if db.scalar(select(Video.video_id).where(Video.status=='running',Video.lease_expires_at>now).limit(1)):return None
        candidate=db.execute(select(Video.video_id,Podcast.learner_id,Podcast.podcast_id).join(Podcast,Podcast.podcast_id==Video.podcast_id)
            .join(Material,Material.material_id==Podcast.material_id).where(Video.status.in_(('pending','running')),
            Podcast.status!='deleted',Material.discard_requested_at.is_(None))
            .order_by(Video.created_at,Video.episode_index).limit(1)).first()
        if not candidate:return None
        try:podcast,_=podcasts._locked(db,candidate.learner_id,candidate.podcast_id)
        except RuntimeError:return None
        row=db.get(Video,candidate.video_id,populate_existing=True)
        if not row or row.status not in ('pending','running'):return None
        if row.status=='running':
            # 回應不明的模型請求不自動重播；由使用者決定重試。
            row.status='failed';row.error_code='VIDEO_INTERRUPTED';row.lease_token=row.lease_expires_at=None;row.version+=1;return None
        episode=deepcopy(_episode(podcast,row.episode_index))
        if row.source_sha256!=identity(podcast,episode):
            row.status='failed';row.error_code='VIDEO_SOURCE_CHANGED';row.version+=1;return None
        row.status='running';row.lease_token=uuid4();row.lease_expires_at=now+timedelta(minutes=140);row.version+=1
        document=_read_verified_document(db,podcast.learner_id,podcast.material_id,revision=podcast.knowledge_structure_revision)
        return {'video_id':row.video_id,'podcast_id':row.podcast_id,'index':row.episode_index,'token':row.lease_token,
                'owner':podcast.learner_id,'episode':episode,'source_sha256':row.source_sha256,
                'source_context':podcasts.source_context(document,episode),
                'source_resolver':f'/v1/materials/{podcast.material_id}/knowledge-structures/{podcast.knowledge_structure_revision}/evidence'}


def validate_bundle(state,bundle):
    try:
        episode=state['episode'];metadata=bundle['render']
        if bundle['policy']!=POLICY or not isinstance(bundle['model'],str) or not bundle['model']:
            raise SourceError('VIDEO_RESULT_INVALID')
        raw=base64.b64decode(bundle['video'],validate=True)
        if (not 32<len(raw)<=100*1024*1024 or raw[4:8]!=b'ftyp' or sha256(raw).hexdigest()!=metadata['sha256']
                or (metadata['width'],metadata['height'],metadata['fps'])!=(1920,1080,60)
                or type(metadata['duration']) not in (int,float) or not math.isfinite(metadata['duration'])
                or abs(metadata['duration']-episode['audio']['duration_seconds'])>.05):
            raise SourceError('VIDEO_RESULT_INVALID')
        if not isinstance(bundle['plan'],dict) or bundle['plan'].get('schema') not in ('podcast-storyboard/v2','podcast-storyboard/v3'):raise SourceError('VIDEO_RESULT_INVALID')
        plan=validate_plan(bundle['plan'],bundle['cues'])
        timeline=timeline_for_video(state['podcast_id'],state['index'],episode,bundle['cues'],bundle['alignment'],state['source_resolver'])
        validate_visual_timing(plan,timeline)
        manifest={**metadata,'audio_sha256':episode['audio']['sha256'],'script_sha256':script_digest(episode),
                  'policy':bundle['policy'],'model':bundle['model'],
                  'timeline':timeline,'storyboard':plan,
                  'pages':[{'title':p['title'],'start':timeline['segments'][p['start_cue']]['start'],
                            'end':timeline['segments'][p['end_cue']]['end']} for p in plan['pages']]}
        if 'caption_alignment' in bundle['alignment']:
            manifest['caption_alignment'] = deepcopy(bundle['alignment']['caption_alignment'])
        return raw,manifest
    except (KeyError,TypeError,ValueError) as error:
        code=str(error).split(':',1)[0]
        raise SourceError(code if code.startswith('VIDEO_') else 'VIDEO_RESULT_INVALID') from None


def finish(state,*,bundle=None,error=None,dsn=None):
    with database_session(dsn) as db:
        try:podcast,_=podcasts._locked(db,state['owner'],state['podcast_id'])
        except RuntimeError:return False
        row=db.get(Video,state['video_id'],populate_existing=True)
        if (not row or row.status!='running' or row.lease_token!=state['token']
                or row.lease_expires_at<=datetime.now(UTC)):return False
        if row.source_sha256!=identity(podcast,_episode(podcast,state['index'])):error='VIDEO_SOURCE_CHANGED'
        if error:
            row.status='failed';row.error_code=error
        else:
            raw,manifest=validate_bundle(state,bundle)
            artifact=write_blob(db,podcast.learner_id,podcast.material_id,raw,'podcast_video','video/mp4')
            row.artifact_id=artifact.artifact_id;row.manifest=manifest;row.status='ready';row.error_code=None
        row.lease_token=row.lease_expires_at=None;row.version+=1
        return True


def provider(state,audio):
    base=os.environ.get('STUDYDY_PODCAST_PROVIDER_URL','').rstrip('/')
    token=os.environ.get('STUDYDY_PODCAST_PROVIDER_TOKEN','')
    if not base or not token:raise SourceError('VIDEO_PROVIDER_UNAVAILABLE')
    body={key:state[key] for key in ('episode','source_context','source_resolver')}
    body.update(podcast_id=str(state['podcast_id']),episode_index=state['index'],audio=base64.b64encode(audio).decode(),
                job_id=str(state['video_id']),job_token=str(state['token']))
    request=Request(base+'/video',data=json.dumps(body,ensure_ascii=False).encode(),
                    headers={'Content-Type':'application/json','Authorization':'Bearer '+token})
    try:
        with urlopen(request,timeout=7800) as response:raw=response.read(140*1024*1024+1)
    except HTTPError as error:
        try:code=json.loads(error.read(4096)).get('error_code','')
        except Exception:code=''
        allowed={'VIDEO_AUDIO_INVALID','VIDEO_CANCELLED','VIDEO_SOURCE_CHANGED','VIDEO_DISK_SPACE_LOW','VIDEO_TRANSCRIPT_INVALID','VIDEO_STORYBOARD_INVALID',
                 'VIDEO_LAYOUT_INVALID','VIDEO_STORYBOARD_NEEDS_REVIEW','VIDEO_RENDER_FAILED','VIDEO_TOO_LARGE',
                 'VIDEO_ALIGNMENT_INVALID','VIDEO_PROVIDER_UNAVAILABLE','SCENE_ALIGNMENT_FAILED','LUNA_GENERATION_FAILED','LUNA_GENERATION_TIMEOUT'}
        raise SourceError(code if code in allowed else 'VIDEO_PROVIDER_FAILED') from None
    if len(raw)>140*1024*1024:raise SourceError('VIDEO_RESULT_INVALID')
    return json.loads(raw)


def watch_cancellation(state,done,dsn):
    """取消或刪除後通知 provider：不再送後續模型請求，並停止該工作的渲染子程序。"""
    while not done.wait(2):
        try:
            with database_session(dsn) as db:
                active=db.scalar(select(Video.video_id).where(Video.video_id==state['video_id'],
                    Video.status=='running',Video.lease_token==state['token']))
            if active:continue
            base=os.environ.get('STUDYDY_PODCAST_PROVIDER_URL','').rstrip('/')
            token=os.environ.get('STUDYDY_PODCAST_PROVIDER_TOKEN','')
            if base and token:
                request=Request(base+'/video/cancel',data=json.dumps({'job_id':str(state['video_id']),
                    'job_token':str(state['token'])}).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+token})
                with urlopen(request,timeout=5) as response:response.read(4096)
            return
        except Exception:logging.getLogger(__name__).warning('VIDEO_CANCEL_CHECK_FAILED')


def step(*,dsn=None):
    state=claim(dsn=dsn)
    if state is None:return False
    saving=False
    done=Event();watcher=Thread(target=watch_cancellation,args=(state,done,dsn),daemon=True)
    watcher.start()
    try:
        from uuid import UUID
        with open_verified_artifact(state['owner'],UUID(state['episode']['audio']['artifact_id']),dsn=dsn) as source:
            audio=source.file.read(100*1024*1024+1)
        podcasts.validate_audio(audio)
        bundle=provider(state,audio)
        saving=True
        finish(state,bundle=bundle,dsn=dsn)
    except Exception as error:
        if isinstance(error,(SourceError,ValueError)) and str(error).startswith(('VIDEO_','SCENE_','LUNA_')):
            code=str(error).split(':',1)[0]
        else:code='VIDEO_STORAGE_FAILED' if saving else 'VIDEO_PROVIDER_FAILED'
        try:finish(state,error=code,dsn=dsn)
        except Exception:logging.getLogger(__name__).warning('VIDEO_RESULT_SAVE_FAILED')
    finally:
        done.set();watcher.join(timeout=2)
    return True


class VideoWorker:
    def __init__(self,dsn):
        self.dsn=dsn;self.event=Event();self.thread=Thread(target=self.loop,name='podcast-video-worker',daemon=True)
    def start(self):self.thread.start();return self
    def stop(self):self.event.set();self.thread.join(timeout=2)
    def loop(self):
        while not self.event.wait(1):
            try:step(dsn=self.dsn)
            except Exception:logging.getLogger(__name__).warning('VIDEO_WORKER_FAILED')
