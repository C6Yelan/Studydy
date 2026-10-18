"""教材內的持久問答；錄音只是輸入，引用始終回到固定 KS。"""
from .storage.materials import require_material

from .provider_http import provider
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import base64
import json
from itertools import zip_longest
from uuid import UUID, uuid4
from sqlalchemy import delete, or_, select, tuple_
from .errors import SourceError
from .storage.artifacts import _key_digest
from .storage.knowledge_structures import _prune_unreferenced_structures
from .storage.tables import VoiceConversation as Conversation, VoiceTurn as Turn, Material, database_session
from .podcasts import validate_audio
from .generation_jobs import cancellable_job
from . import retrieval, material_qa


ACTIVE = ('transcribing', 'pending', 'answering', 'speaking')




def _conversation(db, owner, identity, *, read=False):
    row = db.get(Conversation, identity)
    if row is None or row.learner_id != owner:
        raise SourceError('RESOURCE_NOT_FOUND')
    material = require_material(db, owner, row.material_id, read=read)
    db.refresh(row)
    if row.deleted_at is not None:
        raise SourceError('RESOURCE_NOT_FOUND')
    return row, material


def _summary(row):
    return {k:getattr(row,k) for k in ('conversation_id','material_id','knowledge_structure_revision','title','created_at')}


def create(owner, material_id, key, *, revision=None, dsn=None):
    with database_session(dsn) as db:
        material = require_material(db, owner, material_id)
        digest = _key_digest(key)
        row = db.scalar(select(Conversation).where(Conversation.material_id == material_id,
            Conversation.idempotency_key_sha256 == digest))
        if row:
            if row.deleted_at: raise SourceError('RESOURCE_NOT_FOUND')
            if revision is not None and row.knowledge_structure_revision != revision:raise SourceError('IDEMPOTENCY_CONFLICT')
            return _summary(row)
        selected_revision = material.head_revision if revision is None else revision
        if not selected_revision: raise SourceError('SOURCE_NOT_READY')
        retrieval._scope(db, owner, material_id, selected_revision)
        row = Conversation(conversation_id=uuid4(),learner_id=owner,material_id=material_id,
            knowledge_structure_revision=selected_revision,title='新的教材問答',created_at=datetime.now(UTC),
            idempotency_key_sha256=digest)
        db.add(row); db.flush()
        return _summary(row)


def listing(owner, material_id, *, dsn=None):
    with database_session(dsn) as db:
        require_material(db, owner, material_id, read=True)
        return {'conversations':[_summary(r) for r in db.scalars(select(Conversation).where(
            Conversation.material_id == material_id,Conversation.deleted_at.is_(None)).order_by(Conversation.created_at.desc()))]}


def read(owner, identity, *, dsn=None):
    with database_session(dsn) as db:
        row, material = _conversation(db, owner, identity, read=True)
        turns = db.scalars(select(Turn).where(Turn.conversation_id == identity).order_by(Turn.created_at,Turn.turn_id)).all()
        retrieval._scope(db, owner, row.material_id, row.knowledge_structure_revision)
        answers = {}
        for turn in turns:
            if turn.mode not in ('text','voice') or turn.status=='draft':
                raise SourceError('MATERIAL_FORMAT_UNSUPPORTED')
            answer = deepcopy(turn.answer)
            if answer and answer.get('schema') != 'material-answer/v2':
                raise SourceError('MATERIAL_FORMAT_UNSUPPORTED')
            answers[turn.turn_id] = answer
        return {**_summary(row), 'is_current_revision': row.knowledge_structure_revision == material.head_revision,
            'source_resolver':f'/v1/materials/{row.material_id}/knowledge-structures/{row.knowledge_structure_revision}/evidence',
            'turns':[{**{k:getattr(t,k) for k in ('turn_id','mode','question','answer','status','error_code','context','transcription')},
                'answer':answers[t.turn_id],
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


def add_turn(owner, identity, key, question='', recording=None, *, mode="text", context=None, dsn=None):
    if not recording and (not question.strip() or len(question)>4000): raise SourceError('REQUEST_INVALID')
    if recording and len(recording)>12*1024*1024: raise SourceError('MATERIAL_TOO_LARGE')
    _key_digest(key)
    fingerprint = sha256(recording if recording else question.encode()).hexdigest()
    if context is not None:
        fingerprint=sha256(json.dumps({'input':fingerprint,'context':context},sort_keys=True,separators=(',',':')).encode()).hexdigest()
    if mode not in ('text', 'voice'): raise SourceError('REQUEST_INVALID')
    mode = 'voice' if recording else mode
    fingerprint = sha256(json.dumps({'input': fingerprint, 'mode': mode}, sort_keys=True).encode()).hexdigest()
    with database_session(dsn) as db:
        row, _ = _conversation(db,owner,identity)
        retrieval._scope(db,owner,row.material_id,row.knowledge_structure_revision)
        old = db.scalar(select(Turn).where(Turn.conversation_id==identity,Turn.request_key==key))
        if old:
            if old.fingerprint != fingerprint: raise SourceError('IDEMPOTENCY_CONFLICT')
            return {'turn_id':old.turn_id}
        if db.scalar(select(Turn.turn_id).where(Turn.conversation_id==identity,Turn.status.in_(ACTIVE))):
            raise SourceError('VOICE_TURN_IN_PROGRESS')
        verified_context = podcast_context(db, row, context)[0] if context is not None else None
        turn = Turn(context=verified_context,mode=mode,turn_id=uuid4(),conversation_id=identity,request_key=key,fingerprint=fingerprint,
            question=question.strip(),recording=recording,status='transcribing' if recording else 'pending',created_at=datetime.now(UTC))
        db.add(turn)
        if question: row.title=question.strip()[:80]
        db.flush()
        return {'turn_id':turn.turn_id}


def action(owner, identity, turn_id, action, *, dsn=None):
    with database_session(dsn) as db:
        row,_ = _conversation(db,owner,identity)
        turn=db.get(Turn,turn_id)
        if turn is None or turn.conversation_id!=identity: raise SourceError('RESOURCE_NOT_FOUND')
        if action=='retry' and turn.context:podcast_context(db,row,turn.context)
        if action=='cancel':
            if turn.status in ACTIVE:
                turn.status='cancelled'; turn.recording=None; turn.lease_token=turn.lease_expires_at=None
        elif action=='retry':
            retrieval._scope(db,owner,row.material_id,row.knowledge_structure_revision)
            if turn.status!='failed': raise SourceError('REQUEST_INVALID')
            if db.scalar(select(Turn.turn_id).where(Turn.conversation_id==identity,Turn.status.in_(ACTIVE))):
                raise SourceError('VOICE_TURN_IN_PROGRESS')
            if not turn.question: raise SourceError('REQUEST_INVALID')
            turn.status=('ready' if turn.mode=='text' else 'speaking') if turn.answer else 'pending';turn.error_code=None
        else: raise SourceError('REQUEST_INVALID')
        status=turn.status
    return {'ok':True,'status':status}


def remove(owner, identity, *, dsn=None):
    with database_session(dsn) as db:
        row,material=_conversation(db,owner,identity)
        db.execute(delete(Turn).where(Turn.conversation_id==identity))
        row.deleted_at=datetime.now(UTC);row.knowledge_structure_revision=None;row.title='';row.memory=None
        db.flush()
        _prune_unreferenced_structures(db,owner,row.material_id,material.head_revision)
    return {'ok':True}


def audio(owner, identity, turn_id, *, dsn=None):
    with database_session(dsn) as db:
        _conversation(db,owner,identity,read=True)
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
        state={k:deepcopy(getattr(turn,k)) for k in ('turn_id','conversation_id','mode','question','answer','status','recording','lease_token','context','transcription')}
        state.update(owner=row.learner_id,material_id=row.material_id,revision=row.knowledge_structure_revision)
        if turn.status=='transcribing':
            recent=db.scalar(select(Turn).where(Turn.conversation_id==row.conversation_id,Turn.answer.is_not(None),
                tuple_(Turn.created_at,Turn.turn_id)<tuple_(turn.created_at,turn.turn_id)).order_by(Turn.created_at.desc(),Turn.turn_id.desc()).limit(1))
            # 話題切換後仍保留教材主題，避免辨識提示只剩上一輪的局部詞彙。
            words=[db.get(Material,row.material_id).display_name[:80]]
            if recent:
                evidence_ids=list(dict.fromkeys(reference['evidence_id'] for citation in recent.answer.get('citations',[])
                    for reference in citation['evidence']))
                passages=retrieval.evidence_context(db,row.learner_id,row.material_id,row.knowledge_structure_revision,evidence_ids) if evidence_ids else retrieval.search(
                    db,row.learner_id,row.material_id,row.knowledge_structure_revision,[recent.question],limit=4,neighbors=False)
                words.extend(recent.answer.get('speech_terms',[]))
                groups={}
                for passage in passages:
                    hints=groups.setdefault(passage['source_id'],[])
                    hints.extend(hint for hint in passage['hints'] if 1<len(hint)<=30 and hint not in hints)
                words.extend(word for group in zip_longest(*groups.values()) for word in group if word)
            chosen=[]
            for word in dict.fromkeys(words):
                word=' '.join(word.split())
                if len('、'.join([*chosen,word]))<=320 and len(chosen)<12:chosen.append(word)
            state['stt_hints']='、'.join(chosen)
        if turn.status=='answering':
            try:
                retrieval._scope(db, row.learner_id, row.material_id, row.knowledge_structure_revision)
            except SourceError as error:
                turn.status='failed';turn.error_code=str(error)
                turn.lease_token=turn.lease_expires_at=None
                return None
            state['retrieved'] = {}
            history = select(Turn).where(Turn.conversation_id==row.conversation_id,Turn.answer.is_not(None),
                tuple_(Turn.created_at,Turn.turn_id)<tuple_(turn.created_at,turn.turn_id))
            state['user_history'] = [{'question': question, 'created_at': created_at.isoformat()}
                for question, created_at in db.execute(history.with_only_columns(Turn.question, Turn.created_at)
                    .order_by(Turn.created_at, Turn.turn_id))]
            state['memory'] = None
            if row.memory:
                cursor = (datetime.fromisoformat(row.memory['through_created_at']), UUID(row.memory['through_turn_id']))
                # 重試較早失敗的問題不能讀到未來摘要，也不能覆蓋較新的摘要。
                if cursor < (turn.created_at, turn.turn_id):
                    state['memory'] = deepcopy(row.memory)
                    history = history.where(tuple_(Turn.created_at,Turn.turn_id)>tuple_(*cursor))
            state['history']=[{'turn_id':str(t.turn_id),'created_at':t.created_at.isoformat(),
                'question':t.question,'answer':t.answer['text'],
                'sources_for_search_only':list(dict.fromkeys(reference['source_name']
                    for citation in t.answer.get('citations',[]) for reference in citation['evidence']))}
                for t in db.scalars(
                    history.order_by(Turn.created_at,Turn.turn_id))]
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
            turn.question=text.strip();turn.recording=None
            turn.transcription={key:deepcopy(result[key]) for key in ('provider','segments','hints') if key in result}
            turn.status='pending'
            db.get(Conversation,turn.conversation_id).title=turn.question[:80]
        elif state['status']=='answering':
            if turn.context:podcast_context(db,db.get(Conversation,turn.conversation_id),turn.context)
            text=result.get('text');citations=result.get('citations');supported=result.get('supported')
            if (not isinstance(text,str) or not 1<=len(text)<=4500 or not isinstance(citations,list)
                or type(supported)is not bool or (supported and not citations)):
                raise SourceError('VOICE_ANSWER_INVALID')
            for citation in citations:
                if not isinstance(citation, dict): raise SourceError('VOICE_ANSWER_INVALID')
                passage=state['retrieved'].get(citation.get('passage_id'))
                if (passage is None or citation.get('text') != passage['text']
                    or not isinstance(citation.get('evidence'),list) or not citation['evidence']
                    or any(reference not in passage.get('references',[passage['evidence']]) for reference in citation['evidence'])):
                    raise SourceError('VOICE_ANSWER_INVALID')
            turn.answer={**result,'schema':'material-answer/v2'}
            memory = state.get('memory_update')
            if memory:
                conversation = db.get(Conversation,turn.conversation_id)
                current = conversation.memory
                if (not current or (memory['through_created_at'],memory['through_turn_id']) >
                        (current['through_created_at'],current['through_turn_id'])):
                    conversation.memory = memory
            turn.status='ready' if turn.mode=='text' else 'speaking'
        else:
            validate_audio(result);turn.audio=result;turn.status='ready'


def step(*, dsn=None):
    state=claim(dsn=dsn)
    if not state:return False
    try:
        with cancellable_job(Turn, state['turn_id'], state['lease_token'], dsn=dsn, statuses=(state['status'],)) as job:
            if state['status']=='transcribing':result=provider('/transcribe',{**job,'audio':base64.b64encode(state['recording']).decode(),
                'hints':state['stt_hints']})
            elif state['status']=='answering':
                context=None
                if state['context']:
                    with database_session(dsn) as db:
                        row,_=_conversation(db,state['owner'],state['conversation_id'])
                        _,context=podcast_context(db,row,state['context'])
                def retrieve(queries):
                    with database_session(dsn) as db:
                        passages=retrieval.search(db,state['owner'],state['material_id'],state['revision'],queries)
                    state['retrieved'].update({p['passage_id']:p for p in passages})
                    return passages
                with database_session(dsn) as db:
                    catalog = retrieval.catalog(db,state['owner'],state['material_id'],state['revision'])
                result=material_qa.answer(state['question'],state['history'],context,retrieve=retrieve,
                    call=lambda path,body:provider(path,{**job,**body}),catalog=catalog,memory=state['memory'],
                    user_history=state['user_history'],spoken=state['mode']=='voice',recognition=state.get('transcription'))
                state['memory_update'] = result.pop('_memory_update')
            else:result=provider('/audio',{**job,'script':{'segments':[
                {'turns':[{'speaker':'host','text':paragraph}]} for paragraph in state['answer']['text'].split('\n\n') if paragraph.strip()]}})
            finish(state,result,dsn=dsn)
    except Exception as error:
        code=str(error) if isinstance(error,SourceError) else 'VOICE_PROVIDER_FAILED'
        finish(state,error=code,dsn=dsn)
    return True
