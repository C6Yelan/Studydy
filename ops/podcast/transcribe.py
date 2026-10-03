"""獨立辨識程序：PyAV 有界解碼，完成即釋放記憶體與暫存錄音。"""
import io
import json
import os
import sys
import av
import numpy as np
from faster_whisper import WhisperModel


def transcribe(data):
    chunks=[];total=0
    with av.open(io.BytesIO(data)) as container:
        if not container.streams.audio:raise ValueError('VOICE_TRANSCRIPT_INVALID')
        resampler=av.AudioResampler(format='s16',layout='mono',rate=16000)
        for frame in container.decode(audio=0):
            for sample in resampler.resample(frame):
                value=sample.to_ndarray().reshape(-1)
                total+=len(value)
                if total>16000*121:raise ValueError('VOICE_RECORDING_TOO_LONG')
                chunks.append(value)
    if total<1600:raise ValueError('VOICE_TRANSCRIPT_INVALID')
    wave=np.concatenate(chunks).astype(np.float32)/32768
    model=WhisperModel(os.environ['STUDYDY_STT_MODEL_DIR'],device='cpu',compute_type='int8',cpu_threads=6,local_files_only=True)
    segments,_=model.transcribe(wave,language='zh',beam_size=3,vad_filter=True,
        initial_prompt='繁體中文教材問答。保留英文專有名詞、數字與否定詞。',condition_on_previous_text=False)
    text=''.join(s.text for s in segments).strip()
    if not text:raise ValueError('VOICE_TRANSCRIPT_INVALID')
    return {'text':text,'provider':'faster-whisper:large-v3-turbo'}


if __name__=='__main__':
    try:
        result=transcribe(sys.stdin.buffer.read(12*1024*1024+1))
        with open(sys.argv[1],'w') as output:json.dump(result,output,ensure_ascii=False)
    except Exception:raise SystemExit(1)
