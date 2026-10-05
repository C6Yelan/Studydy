"""音訊時間是唯一時鐘；2D 內容只投影既有 claim／明確步驟與 contrast 關係。"""
from copy import deepcopy
from datetime import UTC,datetime,timedelta
import base64
import math
import re
from uuid import UUID,uuid4
from sqlalchemy import or_,select
from pdf_evidence.ocr_page_evidence import canonical_sha256
from . import podcasts
from .source_normalization import SourceError
from .storage.tables import PodcastScenes as Scenes,Podcast,Material,database_session
from .storage.knowledge_structures import _read_verified_document
from .storage.source_artifacts import open_verified_artifact
from .voice import provider
from .scene_alignment import normalized
from .podcast_script import SCHEMA, references, sources

POLICY='source-scenes/v2'


def identity(podcast,episode):
    return canonical_sha256({'policy':POLICY,'revision':podcast.knowledge_structure_revision,
        'audio':episode['audio'],'script':episode['script'],'claims':episode['claims']})


def flow_steps(claim):
    text=claim['text']
    if not re.search(r'步驟|流程|過程|傳送順序|\bsteps?\b|\bsequence\b',text,re.I):return []
    if re.search(r'如果|否則|若|\bif\b|\belse\b',text,re.I):return []
    matches=list(re.finditer(r'(?<![\d.])([1-9]\d*)[.、．](?!\d)\s*',text))
    if 2<=len(matches)<=8 and [int(m[1]) for m in matches]==list(range(1,len(matches)+1)):
        steps=[text[m.end():matches[i+1].start() if i+1<len(matches) else len(text)].strip(' ；;。\n') for i,m in enumerate(matches)]
        if all(steps):return steps
    if '→' in text and not re.search(r'如果|否則|若|\bif\b|\belse\b',text,re.I):
        parts=text.split('→')
        if 2<=len(parts)<=8 and all(p.strip() for p in parts):return [p.strip() for p in parts]
    return []


def validate_alignment(episode,alignment):
    if not isinstance(alignment,dict) or set(alignment) not in ({'starts','anchors','duration','method','producer'}, {'starts','anchors','duration','method','producer','caption_alignment'}):
        raise SourceError('SCENE_ALIGNMENT_INVALID')
    segments=episode['script']['segments'];duration=episode['audio']['duration_seconds']
    starts=alignment.get('starts');anchors=alignment.get('anchors')
    if (not isinstance(starts,list) or len(starts)!=len(segments) or not isinstance(anchors,list) or len(anchors)!=len(segments)
        or not starts or starts[0]!=0 or any(type(t)not in (float,int) or not math.isfinite(t) or not 0<=t<duration for t in starts)
        or any(a>=b for a,b in zip(starts,starts[1:])) or type(alignment.get('duration'))not in (float,int)
        or not math.isfinite(alignment['duration']) or abs(alignment['duration']-duration)>0.05
        or not isinstance(alignment.get('method'),str) or not isinstance(alignment.get('producer'),str)):
        raise SourceError('SCENE_ALIGNMENT_INVALID')
    for i,anchor in enumerate(anchors):
        if not isinstance(anchor,dict) or set(anchor)!={'script_offset','text','audio_start','boundary_script_offset','boundary_text','boundary_audio_start'}:
            raise SourceError('SCENE_ALIGNMENT_INVALID')
        offset,quote,time=anchor['script_offset'],anchor['text'],anchor['audio_start']
        text=normalized(' '.join(t['text'] for t in segments[i]['turns']))
        if (type(offset)is not int or offset<0 or not isinstance(quote,str) or len(quote)<8
            or text[offset:offset+len(quote)]!=quote or type(time)not in (int,float) or not math.isfinite(time)
            or not 0<=time<duration):
            raise SourceError('SCENE_ALIGNMENT_INVALID')
        begin,begin_text,begin_time=anchor['boundary_script_offset'],anchor['boundary_text'],anchor['boundary_audio_start']
        if (type(begin)is not int or not 0<=begin<=offset or not isinstance(begin_text,str) or not begin_text
            or text[begin:begin+len(begin_text)]!=begin_text or type(begin_time)not in (int,float)
            or not math.isfinite(begin_time) or not 0<=begin_time<=time or (i>0 and abs(begin_time-starts[i])>0.01)):
            raise SourceError('SCENE_ALIGNMENT_INVALID')

    if 'caption_alignment' in alignment:
        from .podcast_cues import captions_for_episode
        try: captions_for_episode(episode, alignment['caption_alignment'])
        except ValueError: raise SourceError('SCENE_ALIGNMENT_INVALID') from None


def scene_content(episode,document):
    segments=episode['script']['segments']
    claims=episode['claims'];concepts={c['concept_id'] for c in claims}
    if episode['script'].get('schema') == SCHEMA:
        result=[]
        for i,segment in enumerate(segments):
            refs=references(episode,i); bound,evidence=sources(episode,refs)
            result.append({'index':i,'beat_id':segment['beat_id'],'claim_ids':list(dict.fromkeys(c['claim_id'] for c in bound)),
                'source_bindings':bound,'source_refs':[{'segment_index':i,'turn_index':j,'start':0,'end':len(t['text'])} for j,t in enumerate(segment['turns'])],
                'title':segment['title'],'text':'\n'.join(claims[r['source_index']]['text'] for r in refs),
                'evidence':evidence,'kind':'concept','steps':[],'columns':[],'relation_id':None})
        return result
    if len(segments)!=len(claims) or any(s['claim_id']!=c['claim_id'] for s,c in zip(segments,claims)):
        raise SourceError('SCENE_SOURCE_CHANGED')
    scenes=[]
    for i,segment in enumerate(segments):
        claim=claims[i]
        scene={'index':i,
            'claim_id':claim['claim_id'],'title':claim['label'],'text':claim['text'],'evidence':claim['evidence'],
            'kind':'concept','steps':[],'columns':[],'relation_id':None}
        steps=flow_steps(claim)
        if steps:scene.update(kind='flow',steps=steps)
        else:
            relation=next((r for r in document['relations'] if r['type']=='contrast'
                and claim['concept_id'] in (r['source_concept_id'],r['target_concept_id'])
                and {r['source_concept_id'],r['target_concept_id']}<=concepts),None)
            if relation:
                columns=[]
                for c_id in (relation['source_concept_id'],relation['target_concept_id']):
                    selected=[c for c in claims if c['concept_id']==c_id]
                    columns.append({'label':selected[0]['label'],'claims':selected})
                scene.update(kind='comparison',columns=columns,relation_id=relation['relation_id'])
        scenes.append(scene)
    return scenes


def review_items(content):
    items={}
    for scene in content:
        if scene['kind']=='concept':continue
        key=scene['relation_id'] or scene['claim_id']
        items[key]={'key':key,'kind':scene['kind'],'title':scene['title'],'text':scene['text'],'steps':scene['steps'],
            'columns':[{'label':c['label'],'points':[q['text'] for q in c['claims']]} for c in scene['columns']]}
    return list(items.values())


def checked_verdicts(content,result):
    items=review_items(content)
    if not items:return {'provider':'not-needed;verbatim-source','verdicts':{}}
    if not isinstance(result,dict) or not isinstance(result.get('provider'),str) or not isinstance(result.get('items'),list) or len(result['items'])!=len(items):
        raise SourceError('SCENE_SOURCE_CHECK_INVALID')
    verdicts={}
    for i,(item,check) in enumerate(zip(items,result['items'])):
        if not isinstance(check,dict) or type(check.get('index'))is not int or check['index']!=i or type(check.get('supported'))is not bool or not isinstance(check.get('reason'),str):
            raise SourceError('SCENE_SOURCE_CHECK_INVALID')
        verdicts[item['key']]={'supported':check['supported'],'reason':check['reason']}
    return {'provider':result['provider'],'verdicts':verdicts}


def build_manifest(podcast,episode,document,alignment,checks=None):
    validate_alignment(episode,alignment)
    duration=episode['audio']['duration_seconds'];starts=alignment['starts'];scenes=scene_content(episode,document)
    required={i['key'] for i in review_items(scenes)}
    if required and (not checks or set(checks.get('verdicts',{}))!=required):raise SourceError('SCENE_SOURCE_CHECK_INVALID')
    for i,scene in enumerate(scenes):
        scene.update(start=starts[i],end=starts[i+1] if i+1<len(starts) else duration)
        if scene['kind']!='concept':
            check=checks['verdicts'][scene['relation_id'] or scene['claim_id']]
            if check['supported'] is not True:
                scene.update(kind='concept',steps=[],columns=[],relation_id=None,review_note=check['reason'])
    return {'policy':POLICY,'input_sha256':identity(podcast,episode),'audio_sha256':episode['audio']['sha256'],
        'duration':duration,'scenes':scenes,'alignment_method':alignment['method'],'alignment_producer':alignment['producer'],
        'source_checks':checks or {'provider':'not-needed;verbatim-source','verdicts':{}},
        'anchors':alignment['anchors'],'source_resolver':f'/v1/materials/{podcast.material_id}/knowledge-structures/{podcast.knowledge_structure_revision}/evidence',
        **({'caption_alignment': deepcopy(alignment['caption_alignment'])} if 'caption_alignment' in alignment else {})}


def read(owner,podcast_id,*,dsn=None):
    with database_session(dsn) as db:
        podcast,_=podcasts._locked(db,owner,podcast_id)
        rows={r.episode_index:r for r in db.scalars(select(Scenes).where(Scenes.podcast_id==podcast_id))}
        result=[]
        for i,episode in enumerate(podcast.episodes):
            row=rows.get(i)
            current=bool(episode['audio'] and episode['script'] and row and row.input_sha256==identity(podcast,episode))
            result.append({'index':i,'status':row.status if current else 'unprepared','version':row.version if row else 0,
                'error_code':row.error_code if current else None,'manifest':deepcopy(row.manifest) if current and row.status=='ready' else None})
        return {'podcast_id':podcast_id,'episodes':result}


def enqueue(db,podcast,index):
    episode=podcast.episodes[index]
    if episode['script'].get('schema') != SCHEMA:return
    if db.scalar(select(Scenes.scene_id).where(Scenes.podcast_id==podcast.podcast_id,Scenes.episode_index==index)):return
    db.add(Scenes(scene_id=uuid4(),podcast_id=podcast.podcast_id,episode_index=index,
        input_sha256=identity(podcast,episode),status='pending',version=1,created_at=datetime.now(UTC)))


def prepare(owner,podcast_id,*,dsn=None):
    with database_session(dsn) as db:
        podcast,_=podcasts._locked(db,owner,podcast_id)
        if podcast.status!='ready':raise SourceError('SOURCE_NOT_READY')
        for index,episode in enumerate(podcast.episodes):
            digest=identity(podcast,episode)
            row=db.scalar(select(Scenes).where(Scenes.podcast_id==podcast_id,Scenes.episode_index==index).with_for_update())
            if row and row.input_sha256==digest:continue
            if row:
                row.input_sha256=digest;row.alignment=row.checks=row.manifest=None;row.status='pending';row.error_code=None
                row.lease_token=row.lease_expires_at=None;row.version+=1
            else:db.add(Scenes(scene_id=uuid4(),podcast_id=podcast_id,episode_index=index,input_sha256=digest,
                status='pending',version=1,created_at=datetime.now(UTC)))
    return read(owner,podcast_id,dsn=dsn)


def action(owner,podcast_id,index,action,version,*,dsn=None):
    with database_session(dsn) as db:
        podcast,_=podcasts._locked(db,owner,podcast_id)
        row=db.scalar(select(Scenes).where(Scenes.podcast_id==podcast_id,Scenes.episode_index==index).with_for_update())
        if not row or row.version!=version:raise SourceError('SCENE_CONFLICT')
        if action=='cancel' and row.status in ('pending','running'):row.status='cancelled'
        elif action=='retry' and row.status in ('failed','cancelled'):
            if podcast.status!='ready' or row.input_sha256!=identity(podcast,podcast.episodes[index]):raise SourceError('SCENE_CONFLICT')
            row.status='pending';row.error_code=None
        else:raise SourceError('SCENE_CONFLICT')
        row.version+=1;row.lease_token=row.lease_expires_at=None
    return read(owner,podcast_id,dsn=dsn)


def claim(*,dsn=None):
    with database_session(dsn) as db:
        now=datetime.now(UTC)
        row=db.scalar(select(Scenes).join(Podcast,Podcast.podcast_id==Scenes.podcast_id)
            .join(Material,Material.material_id==Podcast.material_id).where(Scenes.status.in_(('pending','running')),
            Podcast.status=='ready',Material.discard_requested_at.is_(None),
            or_(Scenes.lease_expires_at.is_(None),Scenes.lease_expires_at<now))
            .order_by(Scenes.created_at,Scenes.episode_index).with_for_update(of=Scenes,skip_locked=True).limit(1))
        if not row:return None
        podcast=db.get(Podcast,row.podcast_id);episode=deepcopy(podcast.episodes[row.episode_index])
        if row.input_sha256!=identity(podcast,episode):
            row.status='failed';row.error_code='SCENE_SOURCE_CHANGED';row.lease_token=row.lease_expires_at=None;return None
        row.status='running';row.lease_token=uuid4();row.lease_expires_at=now+timedelta(minutes=45)
        return {'scene_id':row.scene_id,'podcast_id':row.podcast_id,'index':row.episode_index,'lease_token':row.lease_token,
            'owner':podcast.learner_id,'episode':episode,'input_sha256':row.input_sha256,'alignment':deepcopy(row.alignment),'checks':deepcopy(row.checks),
            'document':_read_verified_document(db,podcast.learner_id,podcast.material_id,revision=podcast.knowledge_structure_revision)}


def save_alignment(state,alignment,*,dsn=None):
    with database_session(dsn) as db:
        podcast,_=podcasts._locked(db,state['owner'],state['podcast_id'])
        row=db.get(Scenes,state['scene_id'])
        if not row or row.status!='running' or row.lease_token!=state['lease_token']:return False
        if podcast.status!='ready' or row.input_sha256!=identity(podcast,podcast.episodes[state['index']]):return False
        validate_alignment(podcast.episodes[state['index']],alignment)
        row.alignment=alignment
        return True


def finish(state,alignment=None,error=None,checks=None,*,dsn=None):
    with database_session(dsn) as db:
        podcast,_=podcasts._locked(db,state['owner'],state['podcast_id'])
        row=db.get(Scenes,state['scene_id'])
        if not row or row.status!='running' or row.lease_token!=state['lease_token']:return
        row.lease_token=row.lease_expires_at=None
        if error:row.status='failed';row.error_code=error;return
        episode=podcast.episodes[state['index']]
        if podcast.status!='ready' or row.input_sha256!=identity(podcast,episode):
            row.status='failed';row.error_code='SCENE_SOURCE_CHANGED';return
        document=_read_verified_document(db,state['owner'],podcast.material_id,revision=podcast.knowledge_structure_revision)
        row.manifest=build_manifest(podcast,episode,document,alignment,checks)
        row.status='ready';row.error_code=None


def save_checks(state,checks,*,dsn=None):
    with database_session(dsn) as db:
        podcast,_=podcasts._locked(db,state['owner'],state['podcast_id'])
        row=db.get(Scenes,state['scene_id'])
        if not row or row.status!='running' or row.lease_token!=state['lease_token']:return False
        if podcast.status!='ready' or row.input_sha256!=identity(podcast,podcast.episodes[state['index']]):return False
        row.checks=checks
        return True


def step(*,dsn=None):
    state=claim(dsn=dsn)
    if not state:return False
    try:
        alignment=state['alignment']
        if not alignment:
            with open_verified_artifact(state['owner'],UUID(state['episode']['audio']['artifact_id']),dsn=dsn) as artifact:
                audio=artifact.file.read(100*1024*1024+1)
            podcasts.validate_audio(audio)
            texts=[' '.join(t['text'] for t in s['turns']) for s in state['episode']['script']['segments']]
            alignment=provider('/align',{'audio':base64.b64encode(audio).decode(),'texts':texts,
                'caption_turns':[t['text'] for s in state['episode']['script']['segments'] for t in s['turns']]})
        if not save_alignment(state,alignment,dsn=dsn):return True
        checks=state['checks']
        if not checks:
            content=scene_content(state['episode'],state['document']);items=review_items(content)
            result=provider('/scene-check',{'items':items,'source_context':podcasts.source_context(state['document'],state['episode'])}) if items else None
            checks=checked_verdicts(content,result)
            if not save_checks(state,checks,dsn=dsn):return True
        finish(state,alignment,checks=checks,dsn=dsn)
    except Exception as error:
        code=str(error) if isinstance(error,SourceError) else 'SCENE_PREPARATION_FAILED'
        try:finish(state,error=code,dsn=dsn)
        except Exception as failure:
            if str(failure)!='RESOURCE_NOT_FOUND':
                import logging
                logging.getLogger(__name__).warning('SCENE_RESULT_SAVE_FAILED')
    return True
