"""已核對回答的逐句音訊；句序與整份下載共用同一批結果。"""
from datetime import UTC, datetime
import io
import re
import wave

from sqlalchemy import select, update

from .audio import validate_audio
from .errors import SourceError
from .storage.tables import VoiceSentence

TIMING_KEYS = ('retrieval_ms', 'answer_ms', 'verification_ms', 'tts_queue_ms', 'tts_synthesis_ms')


def enqueue(db, turn):
    # 不在小數點切句；保留句末標點與緊接的引號，不改畫面原文。
    sentences = [part.strip() for part in re.findall(r'.+?(?:[。！？!?；;]+[」』”’"]*|(?<!\d)\.(?!\d)|\n+|$)', turn.answer['text'], re.DOTALL) if part.strip()]
    now=datetime.now(UTC)
    db.add_all([VoiceSentence(turn_id=turn.turn_id,index=i,text=text,status='pending',queued_at=now if i==0 else None)
                for i,text in enumerate(sentences)])


def claim(db, turn, state):
    sentence=db.scalar(select(VoiceSentence).where(VoiceSentence.turn_id==turn.turn_id,VoiceSentence.status!='ready').order_by(VoiceSentence.index).limit(1))
    sentence.status='running'
    state['sentence_index']=sentence.index
    state['sentence_text']=sentence.text
    state['tts_wait_ms']=(datetime.now(UTC)-sentence.queued_at).total_seconds()*1000
    turn.stage='tts_queued'


def finish(db, turn, state, data):
    sentence=db.get(VoiceSentence,(turn.turn_id,state['sentence_index']))
    sentence.duration_seconds=validate_audio(data)
    sentence.audio=data
    sentence.status='ready'
    db.flush()
    remaining=db.scalar(select(VoiceSentence).where(VoiceSentence.turn_id==turn.turn_id,VoiceSentence.status!='ready').order_by(VoiceSentence.index).limit(1))
    if remaining:
        remaining.queued_at=datetime.now(UTC)
        turn.stage='tts_queued'
        return
    rows=list(db.scalars(select(VoiceSentence).where(VoiceSentence.turn_id==turn.turn_id).order_by(VoiceSentence.index)))
    if len(rows)==1:
        turn.audio=rows[0].audio
    else:
        output=io.BytesIO()
        with wave.open(output,'wb') as target:
            target.setnchannels(1);target.setsampwidth(2);target.setframerate(24000)
            for item in rows:
                with wave.open(io.BytesIO(item.audio),'rb') as source:
                    target.writeframes(source.readframes(source.getnframes()))
        turn.audio=output.getvalue()
    turn.status='ready'
    turn.stage='completed'


def stop(db, turn, status):
    db.execute(update(VoiceSentence).where(VoiceSentence.turn_id==turn.turn_id,VoiceSentence.status.in_(('pending','running'))).values(status=status))


def retry(db, turn):
    # 部署前已核對但配音失敗的回答沒有句列；重試時才由原回答建立，不重做問答。
    if db.scalar(select(VoiceSentence.index).where(VoiceSentence.turn_id==turn.turn_id).limit(1)) is None:
        enqueue(db, turn)
        return
    db.execute(update(VoiceSentence).where(VoiceSentence.turn_id==turn.turn_id,VoiceSentence.status=='failed').values(status='pending'))
    first=db.scalar(select(VoiceSentence).where(VoiceSentence.turn_id==turn.turn_id,VoiceSentence.status=='pending').order_by(VoiceSentence.index).limit(1))
    if first:
        first.queued_at=datetime.now(UTC)


def audio(db, turn_id, index):
    sentence=db.get(VoiceSentence,(turn_id,index))
    if sentence is None:
        raise SourceError('RESOURCE_NOT_FOUND')
    if sentence.status!='ready':
        raise SourceError('VOICE_AUDIO_NOT_READY')
    return sentence.audio
