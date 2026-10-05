"""影片分鏡只接受有限圖形與逐字可回查的講稿片段，不執行模型程式碼。"""
from copy import deepcopy
from .podcast_script import references, sources
from hashlib import sha256
import json
import math
import unicodedata


POLICY = 'flat-report/v4'
KINDS = ('text', 'box', 'circle', 'line', 'arrow')
COLORS = ('ink', 'teal', 'blue', 'orange', 'muted')


def script_digest(episode):
    return sha256(json.dumps(episode['script'], ensure_ascii=False, sort_keys=True,
                             separators=(',', ':')).encode()).hexdigest()


def source_turns(episode):
    return [{'index': n, 'segment_index': i, 'turn_index': j,
             'speaker': turn['speaker'], 'text': turn['text']}
            for n, (i, j, turn) in enumerate((i, j, t)
                for i, segment in enumerate(episode['script']['segments']) for j, t in enumerate(segment['turns']))]


def object_schema(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def validate_cues(episode, cues):
    turns = source_turns(episode)
    used = [''] * len(turns)
    previous = -1
    if not isinstance(cues, list) or not 1 <= len(cues) <= 80:
        raise ValueError('VIDEO_TRANSCRIPT_INVALID')
    for cue in cues:
        if (set(cue) != {'title', 'parts'} or not isinstance(cue['title'], str)
                or not 0 < len(cue['title']) <= 40 or not isinstance(cue['parts'], list)
                or not 1 <= len(cue['parts']) <= 12):
            raise ValueError('VIDEO_TRANSCRIPT_INVALID')
        for part in cue['parts']:
            if set(part) != {'turn_index', 'text'}:
                raise ValueError('VIDEO_TRANSCRIPT_INVALID')
            index, text = part['turn_index'], part['text']
            if (type(index) is not int or not 0 <= index < len(turns) or index < previous
                    or not isinstance(text, str) or not text):
                raise ValueError('VIDEO_TRANSCRIPT_INVALID')
            used[index] += text
            previous = index
        if sum(len(p['text']) for p in cue['parts']) > 180:
            raise ValueError('VIDEO_TRANSCRIPT_INVALID')
    if used != [turn['text'] for turn in turns]:
        raise ValueError('VIDEO_TRANSCRIPT_INVALID')
    return ['\n'.join(part['text'] for part in cue['parts']) for cue in cues]


def merge_cue_groups(episode,cues,groups):
    if (not isinstance(groups,list) or any(not isinstance(g,list) or not g for g in groups)
            or [i for g in groups for i in g]!=list(range(len(cues)))
            or any(type(i) is not int for g in groups for i in g)):
        raise ValueError('VIDEO_ALIGNMENT_INVALID')
    merged=[]
    for group in groups:
        parts=[]
        for index in group:
            for part in cues[index]['parts']:
                if parts and parts[-1]['turn_index']==part['turn_index']:parts[-1]['text']+=part['text']
                else:parts.append(deepcopy(part))
        merged.append({'title':cues[group[0]]['title'],'parts':parts})
    validate_cues(episode,merged)
    return merged


def validate_plan(plan, cues):
    if not isinstance(plan, dict) or (set(plan) not in ({'pages'}, {'schema','pages'}) or ('schema' in plan and plan['schema']!='podcast-storyboard/v2')) or not isinstance(plan['pages'], list) or not 1 <= len(plan['pages']) <= 12:
        raise ValueError('VIDEO_STORYBOARD_INVALID:pages must contain 1..12 pages')
    next_cue = 0
    for page_index,page in enumerate(plan['pages']):
        where=f'VIDEO_STORYBOARD_INVALID:page={page_index}'
        if (not isinstance(page,dict) or set(page) not in ({'title', 'start_cue', 'end_cue', 'elements'}, {'title', 'start_cue', 'end_cue', 'elements', 'emphasis'}, {'title', 'start_cue', 'end_cue', 'elements', 'emphasis', 'reveal'})
                or not isinstance(page['title'], str) or not 0 < len(page['title']) <= 28
                or not isinstance(page['elements'], list) or not 1 <= len(page['elements']) <= 60):
            raise ValueError(where+', title must be 1..28 characters and elements must contain 1..60 items')
        if (type(page['start_cue']) is not int or type(page['end_cue']) is not int
                or page['start_cue'] != next_cue or not next_cue <= page['end_cue'] < len(cues)):
            raise ValueError(where+f', start_cue must be {next_cue}; end_cue must be {next_cue}..{len(cues)-1}')
        for element_index,e in enumerate(page['elements']):
            where=f'VIDEO_STORYBOARD_INVALID:page={page_index},element={element_index}'
            if (not isinstance(e, dict) or set(e) != {'kind','cue_index','text','x','y','w','h','size','color','filled'}
                    or e['kind'] not in KINDS or e['color'] not in COLORS or type(e['filled']) is not bool
                    or not isinstance(e['text'], str) or len(e['text']) > 160
                    or any(type(e[k]) is not int for k in ('cue_index','x','y','w','h','size'))
                    or e['size'] not in (28,34,42,52,64)):
                raise ValueError(where+', invalid element fields; follow the supplied element schema')
            if not page['start_cue'] <= e['cue_index'] <= page['end_cue']:
                raise ValueError(where+f", cue_index must be {page['start_cue']}..{page['end_cue']}")
            if not (100 <= e['x'] <= 1820 and 100 <= e['x']+e['w'] <= 1820
                    and 220 <= e['y'] <= 810 and 220 <= e['y']+e['h'] <= 810):
                raise ValueError(where+f", bounds ({e['x']},{e['y']})..({e['x']+e['w']},{e['y']+e['h']}); both endpoints must fit x=100..1820,y=220..810")
            if e['kind'] in ('text','box','circle') and (e['w'] <= 0 or e['h'] <= 0):
                raise ValueError(where+', text/box/circle width and height must be positive')
            if e['kind'] in ('line','arrow') and (e['text'] or (e['w']==0 and e['h']==0)):
                raise ValueError(where+', line/arrow needs empty text and a nonzero length')
            if e['kind']=='text' and not e['text']:
                raise ValueError(where+', text element needs nonempty text')
        groups=page.get('reveal',[])
        if not isinstance(groups,list) or len(groups)>60:raise ValueError(where+', reveal groups cannot exceed the element capacity')
        used=set()
        for group_index,group in enumerate(groups):
            detail=f'VIDEO_STORYBOARD_INVALID:page={page_index},reveal={group_index}'
            if (not isinstance(group,dict) or set(group) not in ({'start_cue','elements'}, {'start_cue','elements','caption_index'})
                or type(group['start_cue']) is not int
                or ('caption_index' in group and (type(group['caption_index']) is not int or group['caption_index'] < 0))
                or not isinstance(group['elements'],list) or not group['elements']):raise ValueError(detail+', expected integer start_cue and nonempty elements')
            # 繪製器按各組 cue 決定可見性；頁首、同時或未排序的群組都能正確呈現。
            if not page['start_cue']<=group['start_cue']<=page['end_cue']:
                raise ValueError(detail+f", start_cue={group['start_cue']} must be within page cues={page['start_cue']}..{page['end_cue']}")
            for target in group['elements']:
                if type(target) is not int or not 0<=target<len(page['elements']) or target in used:raise ValueError(detail+', duplicate or invalid reveal target')
                if group['start_cue']>page['elements'][target]['cue_index']:raise ValueError(detail+', reveal must precede explanation')
                used.add(target)
        # 已知幾何連線的端點不得先於其節點出現；不靠模型承諾避免懸空箭頭。
        visible_at={i:page['start_cue'] for i in range(len(page['elements']))}
        for group in groups:
            for target in group['elements']:visible_at[target]=group['start_cue']
        validate_connections(page, visible_at, where)
        validate_emphasis(page,page_index)
        next_cue = page['end_cue']+1
    if next_cue != len(cues):
        raise ValueError(f'VIDEO_STORYBOARD_INVALID:uncovered cues {next_cue}..{len(cues)-1}')
    return deepcopy(plan)


def validate_emphasis(page,page_index):
    # 舊影片保留原分鏡；標記是覆蓋在既有元素上的暫時強調，不增加教材內容。
    marks=page.get('emphasis',[])
    where=f'VIDEO_STORYBOARD_INVALID:page={page_index},emphasis'
    if not isinstance(marks,list) or len(marks)>12:raise ValueError(where+', at most 12 marks')
    occupied={}
    for index,m in enumerate(marks):
        detail=where+f'={index}'
        if (not isinstance(m,dict) or set(m) not in ({'element_index','start_cue','end_cue','kind','quote'}, {'element_index','start_cue','end_cue','kind','quote','caption_index'})
                or any(type(m[k]) is not int for k in ('element_index','start_cue','end_cue'))
                or ('caption_index' in m and (type(m['caption_index']) is not int or m['caption_index'] < 0))
                or m['kind'] not in ('underline','outline','trace')
                or not isinstance(m['quote'],str) or len(m['quote'])>80):
            raise ValueError(detail+', invalid mark fields')
        if not 0<=m['element_index']<len(page['elements']):raise ValueError(detail+f", target element does not exist: index={m['element_index']}; valid 0-based indices are 0..{len(page['elements'])-1}")
        e=page['elements'][m['element_index']]
        first=max(page['start_cue'],e['cue_index'])
        if not first<=m['start_cue']<=m['end_cue']<=page['end_cue']:
            raise ValueError(detail+f", requested cues={m['start_cue']}..{m['end_cue']}; target element={m['element_index']} may be emphasized only in cues={first}..{page['end_cue']}; the full page is visible earlier, but emphasis must wait for its explanation")
        if m['kind']=='trace':
            if e['kind'] not in ('line','arrow') or m['quote']:
                targets=[i for i,target in enumerate(page['elements']) if target['kind'] in ('line','arrow')]
                raise ValueError(detail+f", trace requires a line/arrow and empty quote; current target kind={e['kind']}, eligible indices={targets}; use outline for labelled shapes or omit the mark")
        elif e['kind'] not in ('text','box','circle') or not e['text']:
            raise ValueError(detail+', text emphasis requires a labelled text/box/circle')
        elif m['quote']:
            if '\n' in m['quote'] or '\r' in m['quote']:
                raise ValueError(detail+', quote must be single-line; choose a short exact phrase, or use outline with empty quote for the whole labelled shape')
            if not m['quote'].strip() or e['text'].count(m['quote'])!=1:
                raise ValueError(detail+f", quote occurs {e['text'].count(m['quote'])} times in target text; use a unique exact phrase including spaces and letter case, or use outline with empty quote for the whole element")
        elif m['kind']=='underline':raise ValueError(detail+', underline needs a short exact quote')
        for cue in range(m['start_cue'],m['end_cue']+1):
            active=occupied.setdefault(cue,[])
            for other in active:
                if other['element_index']!=m['element_index']:continue
                if 'caption_index' in m and 'caption_index' in other and m['caption_index']!=other['caption_index']:continue
                left,right=m['quote'],other['quote']
                if not left or not right:raise ValueError(detail+', overlapping emphasis on the same target')
                left_start=e['text'].index(left);right_start=e['text'].index(right)
                if max(left_start,right_start)<min(left_start+len(left),right_start+len(right)):
                    raise ValueError(detail+', overlapping emphasis on the same phrase')
            active.append(m)


def validate_connections(page, visible_at, where):
    for i, element in enumerate(page['elements']):
        if element['kind'] not in ('line', 'arrow'): continue
        for j, node in enumerate(page['elements']):
            if node['kind'] not in ('box', 'circle'): continue
            left, top = node['x'], node['y']; right, bottom = left+node['w'], top+node['h']
            for x, y in ((element['x'], element['y']), (element['x']+element['w'], element['y']+element['h'])):
                distance = math.hypot(max(left-x, 0, x-right), max(top-y, 0, y-bottom))
                if distance <= 24 and visible_at[i] < visible_at[j]:
                    raise ValueError(where + ', connection must not precede its node')


def visual_window(timeline, start_cue, end_cue, caption_index=None):
    start = timeline['segments'][start_cue]['start']; end = timeline['segments'][end_cue]['end']
    if caption_index is not None:
        caption = timeline['captions'][caption_index]
        start, end = caption['start'], min(end, caption['end'])
    return start, end


def validate_visual_timing(plan, timeline):
    """細揭示只引用已驗證字幕的實測邊界，不改 teaching cue 或自行補秒數。"""
    captions = timeline.get('captions', [])
    for i, page in enumerate(plan['pages']):
        entries = page.get('reveal', []) + page.get('emphasis', [])
        if not any('caption_index' in item for item in entries): continue
        where = f'VIDEO_STORYBOARD_INVALID:page={i}'
        for item in entries:
            if 'caption_index' not in item: continue
            index = item['caption_index']
            if type(index) is not int or not 0 <= index < len(captions):
                raise ValueError(where + ', caption_index has no measured anchor')
            cue = timeline['segments'][item['start_cue']]
            if not cue['start'] <= captions[index]['start'] < cue['end']:
                raise ValueError(where + ', caption_index must start inside its teaching cue')
        visible = {j: timeline['segments'][page['start_cue']]['start'] for j in range(len(page['elements']))}
        for group in page.get('reveal', []):
            when = visual_window(timeline, group['start_cue'], group['start_cue'], group.get('caption_index'))[0]
            for target in group['elements']: visible[target] = when
        validate_connections(page, visible, where)
        for mark in page.get('emphasis', []):
            when = visual_window(timeline, mark['start_cue'], mark['end_cue'], mark.get('caption_index'))[0]
            if when < visible[mark['element_index']]:
                raise ValueError(where + ', emphasis must not precede its revealed target')



def timeline_for_video(podcast_id, episode_index, episode, cues, alignment, source_resolver):
    texts = validate_cues(episode, cues)
    duration = episode['audio']['duration_seconds']
    starts, anchors = alignment.get('starts'), alignment.get('anchors')
    if (not isinstance(starts,list) or not isinstance(anchors,list) or len(starts)!=len(cues)
            or len(anchors)!=len(cues) or starts[0]!=0 or alignment.get('duration')!=duration
            or not isinstance(alignment.get('method'),str) or not isinstance(alignment.get('producer'),str)
            or any(type(t) not in (int,float) or not math.isfinite(t) or not 0<=t<duration for t in starts)
            or any(a>=b for a,b in zip(starts,starts[1:]))):
        raise ValueError('VIDEO_ALIGNMENT_INVALID')
    normalized = lambda value: ''.join(c for c in unicodedata.normalize('NFKC',value).casefold() if c.isalnum())
    turns = source_turns(episode)
    segments=[]
    consumed = [0] * len(turns)
    for i,(cue,text,anchor) in enumerate(zip(cues,texts,anchors)):
        reference=normalized(text)
        try:
            offset, quote = anchor['script_offset'], anchor['text']
            first, first_quote = anchor['boundary_script_offset'], anchor['boundary_text']
            if (type(offset) is not int or type(first) is not int or not 0<=first<=offset
                    or not isinstance(quote,str) or len(quote)<8 or reference[offset:offset+len(quote)]!=quote
                    or not isinstance(first_quote,str) or not first_quote or reference[first:first+len(first_quote)]!=first_quote
                    or not all(type(t) in (int,float) and math.isfinite(t) for t in (anchor['audio_start'],anchor['boundary_audio_start']))):
                raise ValueError()
            end=starts[i+1] if i+1<len(starts) else duration
            if (not 0<=anchor['boundary_audio_start']<=anchor['audio_start']<end
                    or (i>0 and anchor['boundary_audio_start']!=starts[i])):
                raise ValueError()
        except (KeyError,TypeError,ValueError):
            raise ValueError('VIDEO_ALIGNMENT_INVALID') from None
        refs=[]; spoken=[]; claim_ids=[]; evidence={}; source_bindings=[]; beat_ids=[]
        for part in cue['parts']:
            turn=turns[part['turn_index']]; source_index=turn['segment_index']
            offset=consumed[part['turn_index']]; stop=offset+len(part['text'])
            consumed[part['turn_index']]=stop
            bindings=references(episode,source_index,turn['turn_index'],offset,stop)
            bound_claims,bound_evidence=sources(episode,bindings)
            beat=episode['script']['segments'][source_index]
            beat_id=beat.get('beat_id',f'legacy-{source_index}')
            if beat_id not in beat_ids:beat_ids.append(beat_id)
            refs.append({'segment_index':source_index,'turn_index':turn['turn_index'],
                         'start':offset,'end':stop})
            spoken.append({'speaker':turn['speaker'],'text':part['text']})
            for claim in bound_claims:
                if claim not in source_bindings:source_bindings.append(claim)
                if claim['claim_id'] not in claim_ids:claim_ids.append(claim['claim_id'])
            for e in bound_evidence:evidence[e['evidence_id']]=e
        segments.append({'id':f'episode-{episode_index}-cue-{i}','index':i,'start':starts[i],'end':end,
                         'title':cue['title'],'text':text,'turns':spoken,'source_refs':refs,
                         'beat_ids':beat_ids,'source_bindings':source_bindings,
                         'claim_ids':claim_ids,'evidence':list(evidence.values())})
    result = {'schema':'podcast-transcript-timeline/v1','podcast_id':str(podcast_id),'episode_index':episode_index,
            'time_unit':'seconds','granularity':'script_cue','duration':duration,
            'audio_sha256':episode['audio']['sha256'],'script_sha256':script_digest(episode),
            'alignment_method':alignment['method'],'alignment_producer':alignment['producer'],
            'anchors':deepcopy(anchors),'source_resolver':source_resolver,'segments':segments}
    if 'caption_alignment' in alignment:
        from .podcast_cues import captions_for_episode
        try: result['captions'] = captions_for_episode(episode, alignment['caption_alignment'])
        except ValueError: raise ValueError('VIDEO_ALIGNMENT_INVALID:captions do not match the script or measured boundaries') from None
    return result
