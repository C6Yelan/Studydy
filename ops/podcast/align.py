"""由實際音訊的 ASR 詞時間點對齊既有講稿；不以字數分配音訊時間。"""
import json
import os
from pathlib import Path
import sys
import wave


sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'backend/src'))
from runtime.scene_alignment import align_texts,align_cue_groups,normalized


def render(audio_path,texts,turn_indices=None,caption_turns=None):
    from faster_whisper import WhisperModel
    sys.path.insert(0,str(Path(__file__).resolve().parent))
    from transcribe import decode_audio
    with wave.open(str(audio_path),'rb') as wav:
        duration=wav.getnframes()/wav.getframerate()
        if wav.getnchannels()!=1 or wav.getframerate()!=24000 or not 0.5<=duration<=1800:
            raise ValueError('SCENE_ALIGNMENT_FAILED')
    model=WhisperModel(os.environ['STUDYDY_STT_MODEL_DIR'],device='cpu',compute_type='int8',cpu_threads=6,local_files_only=True)
    segments,_=model.transcribe(decode_audio(audio_path.read_bytes(),1801),language='zh',beam_size=3,word_timestamps=True,vad_filter=True,
        initial_prompt='繁體中文教材講解。保留英文專有名詞、數字與否定詞。',condition_on_previous_text=False)
    words=[{'word':word.word,'start':float(word.start),'end':float(word.end)} for s in segments for word in (s.words or [])]
    result=align_cue_groups(texts,words,duration,turn_indices) if turn_indices is not None else align_texts(texts,words,duration)
    metadata=Path(os.environ['STUDYDY_STT_MODEL_DIR'])/'.cache/huggingface/download/model.bin.metadata'
    revision=metadata.read_text().splitlines()[0] if metadata.is_file() else 'local-checkpoint'
    result['producer']='faster-whisper:large-v3-turbo@'+revision
    if caption_turns is not None:
        from runtime.podcast_cues import align_captions
        if ''.join(map(normalized, caption_turns)) != ''.join(map(normalized, texts)):
            raise ValueError('SCENE_ALIGNMENT_FAILED')
        captions = align_captions(caption_turns, words, duration)
        captions['alignment']['producer'] = result['producer']
        result['caption_alignment'] = captions
    return result


if __name__=='__main__':
    try:
        request=json.load(sys.stdin)
        result=render(Path(sys.argv[1]),request['texts'],request.get('turn_indices'),request.get('caption_turns'))
        Path(sys.argv[2]).write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
    except Exception:raise SystemExit(1)
