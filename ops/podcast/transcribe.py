"""獨立辨識程序：PyAV 有界解碼，完成即釋放記憶體與暫存錄音。"""
import io
import json
import os
import sys
import av
import numpy as np
from faster_whisper import WhisperModel


def decode_audio(data,max_seconds=121):
    chunks=[];total=0
    with av.open(io.BytesIO(data)) as container:
        if not container.streams.audio:raise ValueError('VOICE_TRANSCRIPT_INVALID')
        resampler=av.AudioResampler(format='s16',layout='mono',rate=16000)
        for frame in container.decode(audio=0):
            for sample in resampler.resample(frame):
                value=sample.to_ndarray().reshape(-1)
                total+=len(value)
                if total>16000*max_seconds:raise ValueError('VOICE_RECORDING_TOO_LONG')
                chunks.append(value)
    if total<1600:raise ValueError('VOICE_TRANSCRIPT_INVALID')
    return np.concatenate(chunks).astype(np.float32)/32768


def transcribe(data, hints=''):
    wave=decode_audio(data)
    model=WhisperModel(os.environ['STUDYDY_STT_MODEL_DIR'],device='cpu',compute_type='int8',cpu_threads=6,local_files_only=True)
    segments,_=model.transcribe(wave,language='zh',beam_size=3,vad_filter=True,
        initial_prompt='繁體中文教材問答。保留英文專有名詞、數字與否定詞。',
        hotwords=hints or None,condition_on_previous_text=False)
    parts=[{'text':s.text,'start':s.start,'end':s.end,'avg_logprob':s.avg_logprob,
            'no_speech_prob':s.no_speech_prob,'compression_ratio':s.compression_ratio} for s in segments]
    text=''.join(s['text'] for s in parts).strip()
    if not text:raise ValueError('VOICE_TRANSCRIPT_INVALID')
    return {'text':text,'provider':'faster-whisper:large-v3-turbo','segments':parts,'hints':hints}


if __name__=='__main__':
    try:
        result=transcribe(sys.stdin.buffer.read(12*1024*1024+1),os.environ.get('STUDYDY_STT_HINTS',''))
        with open(sys.argv[1],'w') as output:json.dump(result,output,ensure_ascii=False)
    except Exception:raise SystemExit(1)
