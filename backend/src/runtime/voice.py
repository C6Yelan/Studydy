"""教材內的持久問答；錄音只是輸入，引用始終回到固定 KS。"""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import base64
import json
import os
from uuid import UUID, uuid4
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from sqlalchemy import delete, or_, select
from .card_sets import _material
from .source_normalization import SourceError
from .storage.artifacts import _key_digest
from .storage.knowledge_structures import _read_verified_document, _view, _prune_unreferenced_structures
from .storage.tables import VoiceConversation as Conversation, VoiceTurn as Turn, Material, database_session
from .podcasts import validate_audio


ACTIVE = ('transcribing', 'pending', 'answering', 'speaking')


def provider(path, body):
    base = os.environ.get('STUDYDY_PODCAST_PROVIDER_URL', '').rstrip('/')
    token = os.environ.get('STUDYDY_PODCAST_PROVIDER_TOKEN', '')
    if not base or not token:
        raise SourceError('VOICE_PROVIDER_UNAVAILABLE')
    request = Request(base + path, data=json.dumps(body, ensure_ascii=False).encode(),
        headers={'Content-Type':'application/json','Authorization': 'Bearer ' + token})
    try:
        with urlopen(request, timeout=1800 if path=='/align' else 1200) as response:
            data = response.read(20 * 1024 * 1024 + 1)
    except HTTPError as error:
        try: code=json.loads(error.read(4096)).get('error_code')
        except Exception: code=None
        if code in {'VOICE_TRANSCRIPT_INVALID','PODCAST_AUDIO_INVALID','LUNA_GENERATION_FAILED','LUNA_GENERATION_TIMEOUT','SCENE_ALIGNMENT_FAILED'}:
            raise SourceError(code) from None
        raise SourceError('VOICE_PROVIDER_UNAVAILABLE' if error.code==503 else 'VOICE_PROVIDER_FAILED') from None
    if len(data) > 20 * 1024 * 1024:
        raise SourceError('VOICE_PROVIDER_FAILED')
    return data if path == '/audio' else json.loads(data)


def _conversation(db, owner, identity):
    row = db.get(Conversation, identity)
    if row is None or row.learner_id != owner:
        raise SourceError('RESOURCE_NOT_FOUND')
    material = _material(db, owner, row.material_id)
    db.refresh(row)
    if row.deleted_at is not None:
        raise SourceError('RESOURCE_NOT_FOUND')
    return row, material


def _summary(row):
    return {k:getattr(row,k) for k in ('conversation_id','material_id','knowledge_structure_revision','title','created_at')}


def create(owner, material_id, key, *, revision=None, dsn=None):
    with database_session(dsn) as db:
        material = _material(db, owner, material_id)
        digest = _key_digest(key)
        row = db.scalar(select(Conversation).where(Conversation.material_id == material_id,
            Conversation.idempotency_key_sha256 == digest))
        if row:
            if row.deleted_at: raise SourceError('RESOURCE_NOT_FOUND')
            if revision is not None and row.knowledge_structure_revision != revision:raise SourceError('IDEMPOTENCY_CONFLICT')
            return _summary(row)
        selected_revision = material.head_revision if revision is None else revision
        if not selected_revision: raise SourceError('SOURCE_NOT_READY')
        _read_verified_document(db, owner, material_id, revision=selected_revision)
        row = Conversation(conversation_id=uuid4(),learner_id=owner,material_id=material_id,
            knowledge_structure_revision=selected_revision,title='新的教材問答',created_at=datetime.now(UTC),
            idempotency_key_sha256=digest)
        db.add(row); db.flush()
        return _summary(row)


def listing(owner, material_id, *, dsn=None):
    with database_session(dsn) as db:
        _material(db, owner, material_id)
        return {'conversations':[_summary(r) for r in db.scalars(select(Conversation).where(
            Conversation.material_id == material_id,Conversation.deleted_at.is_(None)).order_by(Conversation.created_at.desc()))]}


def read(owner, identity, *, dsn=None):
    with database_session(dsn) as db:
        row, material = _conversation(db, owner, identity)
        turns = db.scalars(select(Turn).where(Turn.conversation_id == identity).order_by(Turn.created_at,Turn.turn_id)).all()
        return {**_summary(row), 'is_current_revision': row.knowledge_structure_revision == material.head_revision,
            'source_resolver':f'/v1/materials/{row.material_id}/knowledge-structures/{row.knowledge_structure_revision}/evidence',
            'turns':[{**{k:getattr(t,k) for k in ('turn_id','question','answer','status','error_code','context')},
                'audio_url':f'/v1/voice-conversations/{identity}/turns/{t.turn_id}/audio' if t.audio else None} for t in turns]}


def podcast_context(db, conversation, locator):
    """每次使用都回查活著的 Podcast；locator 不延長 script retention。"""
    from . import podcasts
    from .podcast_script import digest, references
    try:
        if set(locator) not in ({'podcast_id','episode_index','script_sha256','source_refs'},
                               {'podcast_id','episode_index','script_sha256','source_refs','references'}):raise ValueError()
        try:podcast,_=podcasts._locked(db,conversation.learner_id,UUID(locator['podcast_id']))
        except RuntimeError as error:
            if str(error)=='RESOURCE_NOT_FOUND':raise ValueError() from None
            raise
        if podcast.material_id != conversation.material_id or podcast.knowledge_structure_revision != conversation.knowledge_structure_revision:raise ValueError()
        index=locator['episode_index']
        if type(index) is not int or not 0<=index<len(podcast.episodes):raise ValueError()
        episode=podcast.episodes[index]
        if not episode['script'] or digest(episode['script']) != locator['script_sha256']:raise ValueError()
        refs=locator['source_refs']
        if not isinstance(refs,list) or not 1<=len(refs)<=24:raise ValueError()
        text=[]; bound=[]; previous=None
        for ref in refs:
            if set(ref)!={'segment_index','turn_index','start','end'} or any(type(v) is not int for v in ref.values()):raise ValueError()
            i,j,start,end=(ref[k] for k in ('segment_index','turn_index','start','end'))
            if i<0 or j<0:raise ValueError()
            turn=episode['script']['segments'][i]['turns'][j]
            if not 0<=start<end<=len(turn['text']):raise ValueError()
            position=(i,j,start)
            if previous and position<previous:raise ValueError()
            previous=(i,j,end)
            text.append({'speaker':turn['speaker'],'text':turn['text'][start:end]})
            for item in references(episode,i,j,start,end):
                if item not in bound:bound.append(item)
        validated={k:deepcopy(locator[k]) for k in ('podcast_id','episode_index','script_sha256','source_refs')}
        validated['references']=bound
        return validated, {'turns':text,'instruction':'僅用來理解問題指涉，知識依據仍是同 revision 的 sources。'}
    except (KeyError,IndexError,TypeError,ValueError):
        raise SourceError('VOICE_PODCAST_CONTEXT_INVALID') from None


def add_turn(owner, identity, key, question='', recording=None, *, context=None, dsn=None):
    if not recording and (not question.strip() or len(question)>4000): raise SourceError('REQUEST_INVALID')
    if recording and len(recording)>12*1024*1024: raise SourceError('MATERIAL_TOO_LARGE')
    _key_digest(key)
    fingerprint = sha256(recording if recording else question.encode()).hexdigest()
    if context is not None:
        fingerprint=sha256(json.dumps({'input':fingerprint,'context':context},sort_keys=True,separators=(',',':')).encode()).hexdigest()
    with database_session(dsn) as db:
        row, _ = _conversation(db,owner,identity)
        old = db.scalar(select(Turn).where(Turn.conversation_id==identity,Turn.request_key==key))
        if old:
            if old.fingerprint != fingerprint: raise SourceError('IDEMPOTENCY_CONFLICT')
            return {'turn_id':old.turn_id}
        if db.scalar(select(Turn.turn_id).where(Turn.conversation_id==identity,Turn.status.in_(ACTIVE+('draft',)))):
            raise SourceError('VOICE_TURN_IN_PROGRESS')
        verified_context = podcast_context(db, row, context)[0] if context is not None else None
        turn = Turn(context=verified_context,turn_id=uuid4(),conversation_id=identity,request_key=key,fingerprint=fingerprint,
            question=question.strip(),recording=recording,status='transcribing' if recording else 'pending',created_at=datetime.now(UTC))
        db.add(turn)
        if question: row.title=question.strip()[:80]
        db.flush()
        return {'turn_id':turn.turn_id}


def action(owner, identity, turn_id, action, question='', *, dsn=None):
    with database_session(dsn) as db:
        row,_ = _conversation(db,owner,identity)
        turn=db.get(Turn,turn_id)
        if turn is None or turn.conversation_id!=identity: raise SourceError('RESOURCE_NOT_FOUND')
        if action in ('send','retry') and turn.context:podcast_context(db,row,turn.context)
        if action=='cancel':
            if turn.status in ACTIVE+('draft',):
                turn.status='cancelled'; turn.recording=None; turn.lease_token=turn.lease_expires_at=None
        elif action=='send':
            if turn.status!='draft' or not question.strip() or len(question)>4000: raise SourceError('REQUEST_INVALID')
            turn.question=question.strip(); turn.status='pending'; row.title=question.strip()[:80]
        elif action=='retry':
            if turn.status!='failed': raise SourceError('REQUEST_INVALID')
            if db.scalar(select(Turn.turn_id).where(Turn.conversation_id==identity,Turn.status.in_(ACTIVE+('draft',)))):
                raise SourceError('VOICE_TURN_IN_PROGRESS')
            if not turn.question: raise SourceError('REQUEST_INVALID')
            turn.status='speaking' if turn.answer else 'pending';turn.error_code=None
        else: raise SourceError('REQUEST_INVALID')
    return {'ok':True}


def remove(owner, identity, *, dsn=None):
    with database_session(dsn) as db:
        row,material=_conversation(db,owner,identity)
        db.execute(delete(Turn).where(Turn.conversation_id==identity))
        row.deleted_at=datetime.now(UTC);row.knowledge_structure_revision=None;row.title=''
        db.flush()
        _prune_unreferenced_structures(db,owner,row.material_id,material.head_revision)
    return {'ok':True}


def audio(owner, identity, turn_id, *, dsn=None):
    with database_session(dsn) as db:
        _conversation(db,owner,identity)
        turn=db.get(Turn,turn_id)
        if not turn or turn.conversation_id!=identity or not turn.audio: raise SourceError('RESOURCE_NOT_FOUND')
        return bytes(turn.audio)


def claim(*, dsn=None):
    with database_session(dsn) as db:
        now=datetime.now(UTC)
        turn=db.scalar(select(Turn).join(Conversation,Conversation.conversation_id==Turn.conversation_id)
            .join(Material,Material.material_id==Conversation.material_id).where(Turn.status.in_(ACTIVE),
            Conversation.deleted_at.is_(None),Material.discard_requested_at.is_(None),
            or_(Turn.lease_expires_at.is_(None),Turn.lease_expires_at<now))
            .order_by(Turn.created_at).with_for_update(of=Turn,skip_locked=True).limit(1))
        if not turn: return None
        row=db.get(Conversation,turn.conversation_id)
        turn.lease_token=uuid4();turn.lease_expires_at=now+timedelta(minutes=30)
        if turn.status=='pending':turn.status='answering'
        state={k:deepcopy(getattr(turn,k)) for k in ('turn_id','conversation_id','question','answer','status','recording','lease_token','context')}
        state.update(owner=row.learner_id,material_id=row.material_id,revision=row.knowledge_structure_revision)
        if turn.status=='answering':
            document=_read_verified_document(db,row.learner_id,row.material_id,revision=row.knowledge_structure_revision)
            view=_view(document,row.material_id)
            state['claims']=[{'label':c['label'],**a} for c in view['concepts'] for a in c['claims']]
            state['history']=[{'question':t.question,'answer':t.answer['text']} for t in db.scalars(
                select(Turn).where(Turn.conversation_id==row.conversation_id,Turn.answer.is_not(None))
                .order_by(Turn.created_at.desc()).limit(8))][::-1]
        return state


def finish(state, result=None, error=None, *, dsn=None):
    with database_session(dsn) as db:
        material=db.scalar(select(Material).where(Material.material_id==state['material_id']).with_for_update())
        if not material or material.discard_requested_at:return
        turn=db.get(Turn,state['turn_id'])
        if (not turn or turn.lease_token!=state['lease_token'] or turn.status!=state['status']
                or turn.lease_expires_at is None or turn.lease_expires_at<=datetime.now(UTC)):return
        turn.lease_token=turn.lease_expires_at=None
        if error:
            turn.status='failed';turn.error_code=error;turn.recording=None;return
        if state['status']=='transcribing':
            text=result.get('text')
            if not isinstance(text,str) or not text.strip() or len(text)>4000:raise SourceError('VOICE_TRANSCRIPT_INVALID')
            turn.question=text.strip();turn.recording=None;turn.status='draft'
        elif state['status']=='answering':
            if turn.context:podcast_context(db,db.get(Conversation,turn.conversation_id),turn.context)
            text=result.get('text');indices=result.get('citations');supported=result.get('supported')
            if (not isinstance(text,str) or not 1<=len(text)<=2400 or not isinstance(indices,list)
                or any(type(i)is not int or not 0<=i<len(state['claims']) for i in indices)
                or type(supported)is not bool or (supported and not indices)):
                raise SourceError('VOICE_ANSWER_INVALID')
            turn.answer={'text':text,'supported':supported,'citations':[state['claims'][i] for i in dict.fromkeys(indices)],
                'provider':'codex-exec:gpt-5.6-luna'}
            turn.status='speaking'
        else:
            validate_audio(result);turn.audio=result;turn.status='ready'


def step(*, dsn=None):
    state=claim(dsn=dsn)
    if not state:return False
    try:
        if state['status']=='transcribing':result=provider('/transcribe',{'audio':base64.b64encode(state['recording']).decode()})
        elif state['status']=='answering':
            context=None
            if state['context']:
                with database_session(dsn) as db:
                    row,_=_conversation(db,state['owner'],state['conversation_id'])
                    _,context=podcast_context(db,row,state['context'])
            result=provider('/answer',{**{k:state[k] for k in ('question','claims','history')},'context':context})
        else:result=provider('/audio',{'script':{'segments':[{'turns':[{'speaker':'host','text':state['answer']['text']}]}]}})
        finish(state,result,dsn=dsn)
    except Exception as error:
        code=str(error) if isinstance(error,SourceError) else 'VOICE_PROVIDER_FAILED'
        finish(state,error=code,dsn=dsn)
    return True
