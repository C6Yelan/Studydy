"""ASR 時間點與講稿的可核對字串錨點；不改顯示文字。"""
from difflib import SequenceMatcher
import math
import unicodedata
from pypinyin import lazy_pinyin, Style

def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFKC',text).casefold() if c.isalnum())


def align_texts(texts,words,duration):
    if not texts:raise ValueError('SCENE_ALIGNMENT_FAILED')
    reference='';ranges=[]
    for text in texts:
        value=normalized(text)
        if not value:raise ValueError('SCENE_ALIGNMENT_FAILED')
        ranges.append((len(reference),len(reference)+len(value)))
        reference+=value
    observed='';times=[]
    for word in words:
        start,end=word['start'],word['end'];value=normalized(word['word'])
        if not value:continue
        if not all(type(t) in (int,float) and math.isfinite(t) for t in (start,end)) or not 0<=start<=end<=duration+0.1:
            raise ValueError('SCENE_ALIGNMENT_FAILED')
        observed+=value;times.extend([(start,end)]*len(value))
    if not observed:raise ValueError('SCENE_ALIGNMENT_FAILED')
    # ASR 的同音字不代表音訊與原稿不同；僅以讀音對齊，顯示及引用仍用原稿。
    phonetic=lambda text:lazy_pinyin(text,style=Style.NORMAL,errors=lambda value:list(value))
    ref_sounds,obs_sounds=phonetic(reference),phonetic(observed)
    if len(ref_sounds)!=len(reference) or len(obs_sounds)!=len(observed):raise ValueError('SCENE_ALIGNMENT_FAILED')
    matches=SequenceMatcher(None,ref_sounds,obs_sounds,autojunk=False).get_matching_blocks()
    starts=[];anchors=[]
    for left,right in ranges:
        candidates=[];boundary=[]
        for block in matches:
            a=max(left,block.a);b=min(right,block.a+block.size)
            if b>a:
                offset=block.b+a-block.a
                boundary.append((a,times[offset][0],reference[a:min(b,a+48)]))
            # 可核對的連續語句，不把孤立數字或單一常見字當段落時間依據。
            if b-a>=8 and any(c.isalpha() for c in reference[a:b]):
                offset=block.b+a-block.a
                candidates.append((a,times[offset][0],reference[a:min(b,a+48)]))
        if not candidates:raise ValueError('SCENE_ALIGNMENT_FAILED')
        a,time,anchor=min(candidates)
        beginning,begin_time,begin_text=min(boundary)
        # 長語句負責確認段落身分；邊界用該段最早對上的實際詞時間，不按字數猜測。
        starts.append(round(begin_time,3));anchors.append({'script_offset':a-left,'text':anchor,'audio_start':round(time,3),
            'boundary_script_offset':beginning-left,'boundary_text':begin_text,'boundary_audio_start':round(begin_time,3)})
    if any(a>=b for a,b in zip(starts,starts[1:])) or starts[-1]>=duration:
        raise ValueError('SCENE_ALIGNMENT_FAILED')
    starts[0]=0.0
    return {'starts':starts,'anchors':anchors,'duration':duration,'method':'whisper-phonetic-boundaries/v2'}
