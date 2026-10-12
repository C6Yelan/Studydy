"""ASR 時間點與講稿的可核對字串錨點；不改顯示文字。"""
from difflib import SequenceMatcher
import math
import unicodedata

class AlignmentError(ValueError):
    def __init__(self,indices=(),fully_matched=False,can_group=False):
        super().__init__('SCENE_ALIGNMENT_FAILED')
        self.indices=tuple(indices)
        self.fully_matched=fully_matched
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
    ref_search='\0'+'\0'.join(ref_sounds)+'\0'
    obs_search='\0'+'\0'.join(obs_sounds)+'\0'
    def unique(sound_slice):
        needle='\0'+'\0'.join(sound_slice)+'\0'
        return all((first:=haystack.find(needle))>=0 and haystack.find(needle,first+1)<0
                   for haystack in (ref_search,obs_search))
    starts=[];anchors=[];complete=[]
    for index,(left,right) in enumerate(ranges):
        pieces=[];matched=0
        for block in matches:
            a=max(left,block.a);b=min(right,block.a+block.size)
            if b>a:
                matched+=b-a
                offset=block.b+a-block.a
                pieces.append((a,b,offset))
        fully_matched=matched==right-left
        # 完整匹配可沿用整體順序；部分匹配需能區分來源與辨識中的位置。
        # 不以固定字數排除短句、數字或被 ASR 插詞分開的片段。
        candidates=[piece for piece in pieces if fully_matched or unique(ref_sounds[piece[0]:piece[1]])]
        if not candidates:raise AlignmentError((index,),fully_matched=fully_matched,can_group=matched>0)
        a,b,offset=candidates[0]
        time=times[offset][0];anchor=reference[a:min(b,a+48)]
        beginning,boundary_end,boundary_offset=pieces[0]
        begin_time=times[boundary_offset][0];begin_text=reference[beginning:min(boundary_end,beginning+48)]
        complete.append(matched==right-left)
        # 錨點及邊界都取實際匹配的詞時間，不按字數猜測。
        starts.append(round(begin_time,3));anchors.append({'script_offset':a-left,'text':anchor,'audio_start':round(time,3),
            'boundary_script_offset':beginning-left,'boundary_text':begin_text,'boundary_audio_start':round(begin_time,3)})
    for index,(a,b) in enumerate(zip(starts,starts[1:])):
        if a>=b:raise AlignmentError((index,index+1),fully_matched=a==b and complete[index] and complete[index+1],can_group=a==b and complete[index] and complete[index+1])
    if starts[-1]>=duration:raise AlignmentError()
    starts[0]=0.0
    return {'starts':starts,'anchors':anchors,'duration':duration,'method':'whisper-phonetic-boundaries/v3'}


def align_cue_groups(texts,words,duration,turn_indices):
    """細切失去可靠錨點時，可合併同一原發言片段；跨發言只允許完整辨識的片段。"""
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
                # 完整辨識仍可能被 ASR 插詞拆成多個短比對；合併後重新核對匹配位置與時間。
                # 跨發言不允許部分／完全漏辨識，不能用鄰句掩蓋遺漏。
                if not a.intersection(b) and not error.fully_matched:continue
                merged=groups[left]+groups[right]
                if sum(len(texts[i]) for i in merged)>180 or len(a|b)>12:continue
                groups[left:right+1]=[merged]
                break
            else:raise
