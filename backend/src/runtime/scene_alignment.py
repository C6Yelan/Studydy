"""ASR 時間點與講稿的可核對字串錨點；不改顯示文字。"""
from difflib import SequenceMatcher
import math
import unicodedata

class AlignmentError(ValueError):
    def __init__(self,indices=(),short_complete=False,can_group=False):
        super().__init__('SCENE_ALIGNMENT_FAILED')
        self.indices=tuple(indices)
        self.short_complete=short_complete
        self.can_group=can_group


def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFKC',text).casefold() if c.isalnum())


def align_texts(texts,words,duration):
    from pypinyin import lazy_pinyin, Style
    if not texts:raise AlignmentError()
    reference='';ranges=[]
    for text in texts:
        value=normalized(text)
        if not value:raise AlignmentError()
        ranges.append((len(reference),len(reference)+len(value)))
        reference+=value
    observed='';times=[];previous_word_start=-1
    for word in words:
        start,end=word['start'],word['end'];value=normalized(word['word'])
        if not value:continue
        if not all(type(t) in (int,float) and math.isfinite(t) for t in (start,end)) or not 0<=start<=end<=duration+0.1:
            raise AlignmentError()
        if start<previous_word_start:raise AlignmentError()
        previous_word_start=start
        observed+=value;times.extend([(start,end)]*len(value))
    if not observed:raise AlignmentError()
    # ASR 的同音字不代表音訊與原稿不同；僅以讀音對齊，顯示及引用仍用原稿。
    phonetic=lambda text:lazy_pinyin(text,style=Style.NORMAL,errors=lambda value:list(value))
    ref_sounds,obs_sounds=phonetic(reference),phonetic(observed)
    if len(ref_sounds)!=len(reference) or len(obs_sounds)!=len(observed):raise AlignmentError()
    matches=SequenceMatcher(None,ref_sounds,obs_sounds,autojunk=False).get_matching_blocks()
    starts=[];anchors=[];complete=[]
    for index,(left,right) in enumerate(ranges):
        candidates=[];boundary=[];matched=0
        for block in matches:
            a=max(left,block.a);b=min(right,block.a+block.size)
            if b>a:
                matched+=b-a
                offset=block.b+a-block.a
                boundary.append((a,times[offset][0],reference[a:min(b,a+48)]))
            # 可核對的連續語句，不把孤立數字或單一常見字當段落時間依據。
            if b-a>=8 and any(c.isalpha() for c in reference[a:b]):
                offset=block.b+a-block.a
                candidates.append((a,times[offset][0],reference[a:min(b,a+48)]))
        if not candidates:raise AlignmentError((index,),short_complete=matched==right-left and right-left<8,can_group=matched>0)
        complete.append(matched==right-left)
        a,time,anchor=min(candidates)
        beginning,begin_time,begin_text=min(boundary)
        # 長語句負責確認段落身分；邊界用該段最早對上的實際詞時間，不按字數猜測。
        starts.append(round(begin_time,3));anchors.append({'script_offset':a-left,'text':anchor,'audio_start':round(time,3),
            'boundary_script_offset':beginning-left,'boundary_text':begin_text,'boundary_audio_start':round(begin_time,3)})
    for index,(a,b) in enumerate(zip(starts,starts[1:])):
        if a>=b:raise AlignmentError((index,index+1),short_complete=a==b and complete[index] and complete[index+1],can_group=a==b and complete[index] and complete[index+1])
    if starts[-1]>=duration:raise AlignmentError()
    starts[0]=0.0
    return {'starts':starts,'anchors':anchors,'duration':duration,'method':'whisper-phonetic-boundaries/v2'}


def align_cue_groups(texts,words,duration,turn_indices):
    """細切失去可靠錨點時，只合併同一原發言的相鄰片段；完整辨識的短問句可併鄰句。"""
    if (len(texts)!=len(turn_indices) or any(not group or any(type(i) is not int or i<0 for i in group) for group in turn_indices)):
        raise AlignmentError()
    groups=[[i] for i in range(len(texts))]
    while True:
        grouped=['\n'.join(texts[i] for i in group) for group in groups]
        try:
            result=align_texts(grouped,words,duration)
            if len(groups)!=len(texts):result['method']+=';adjacent-cues/v1'
            return {**result,'groups':groups}
        except AlignmentError as error:
            if not error.indices or not error.can_group:raise
            index=error.indices[0]
            pairs=[(index,index+1)] if len(error.indices)==2 else [(index,index+1),(index-1,index)]
            for left,right in pairs:
                if left<0 or right>=len(groups):continue
                a={t for i in groups[left] for t in turn_indices[i]};b={t for i in groups[right] for t in turn_indices[i]}
                # 不拿相鄰的正常發言掩蓋完全辨識不到的另一輪發言。
                if not a.intersection(b) and not error.short_complete:continue
                merged=groups[left]+groups[right]
                if sum(len(texts[i]) for i in merged)>180 or len(a|b)>12:continue
                groups[left:right+1]=[merged]
                break
            else:raise
