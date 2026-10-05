"""將已驗證的音訊對齊投影為原稿時間軸；不以字數推估句子或發言時間。"""
from copy import deepcopy
from hashlib import sha256
import json
import math
import unicodedata


def build_timeline(podcast_id, episode_index, episode, manifest):
    if not manifest or manifest['audio_sha256'] != episode['audio']['sha256']:
        raise ValueError('PODCAST_TIMELINE_SOURCE_MISMATCH')
    segments = episode['script']['segments']
    scenes = manifest['scenes']
    duration = episode['audio']['duration_seconds']
    if len(segments) != len(scenes) or not scenes or manifest['duration'] != duration:
        raise ValueError('PODCAST_TIMELINE_SOURCE_MISMATCH')
    result = []
    previous_end = 0
    for index, (segment, scene) in enumerate(zip(segments, scenes)):
        start, end = scene['start'], scene['end']
        if (scene.get('beat_id', scene.get('claim_id')) != segment.get('beat_id', segment.get('claim_id')) or scene['index'] != index
                or not all(type(t) in (int, float) and math.isfinite(t) for t in (start, end))
                or start != previous_end or not 0 <= start < end <= duration):
            raise ValueError('PODCAST_TIMELINE_SOURCE_MISMATCH')
        result.append({'id': f'episode-{episode_index}-segment-{index}', 'index': index,
                       'start': start, 'end': end, 'text': '\n'.join(t['text'] for t in segment['turns']),
                       'turns': deepcopy(segment['turns']),
                       **({'claim_id':scene['claim_id']} if 'claim_id' in scene else {'claim_ids':deepcopy(scene['claim_ids'])}),
                       'beat_ids': [segment.get('beat_id',f'legacy-{index}')],
                       'source_refs': [{'segment_index':index,'turn_index':j,'start':0,'end':len(t['text'])} for j,t in enumerate(segment['turns'])],
                       'source_bindings': deepcopy(scene.get('source_bindings',[])),
                       'evidence': deepcopy(scene['evidence']), 'title': scene['title']})
        previous_end = end
    if previous_end != duration:
        raise ValueError('PODCAST_TIMELINE_SOURCE_MISMATCH')
    timeline = {'schema': 'podcast-transcript-timeline/v1', 'podcast_id': str(podcast_id),
            'episode_index': episode_index, 'time_unit': 'seconds', 'granularity': 'segment',
            'duration': duration, 'audio_sha256': manifest['audio_sha256'],
            'script_sha256': sha256(json.dumps(episode['script'], ensure_ascii=False,
                                              sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
            'alignment_method': manifest['alignment_method'], 'alignment_producer': manifest['alignment_producer'],
            'input_sha256': manifest['input_sha256'], 'source_resolver': manifest['source_resolver'],
            'anchors': deepcopy(manifest['anchors']), 'segments': result}
    refined = manifest.get('script_alignment')
    if refined:
        if any(refined[key] != timeline[key] for key in ('audio_sha256', 'script_sha256')):
            raise ValueError('PODCAST_TIMELINE_SOURCE_MISMATCH')
        return align_script_timeline(timeline, episode, refined['cues'], refined['alignment'])
    return timeline


def align_script_timeline(base, episode, cues, alignment):
    """解說片段必須逐字覆蓋原發言；時間只接受可回查原文的 ASR 錨點。"""
    from .podcast_script import references, sources
    turns = [(i, j, turn) for i, segment in enumerate(episode['script']['segments'])
             for j, turn in enumerate(segment['turns'])]
    used = [''] * len(turns)
    starts, anchors = alignment['starts'], alignment['anchors']
    duration = base['duration']
    if (not cues or len(starts) != len(cues) or len(anchors) != len(cues)
            or alignment['duration'] != duration or starts[0] != 0
            or any(type(t) not in (int, float) or not math.isfinite(t) or not 0 <= t < duration for t in starts)
            or any(a >= b for a, b in zip(starts, starts[1:]))):
        raise ValueError('PODCAST_CUE_ALIGNMENT_INVALID')
    previous = -1
    result = []
    normalize = lambda text: ''.join(c for c in unicodedata.normalize('NFKC', text).casefold() if c.isalnum())
    for i, cue in enumerate(cues):
        turn_index = cue['turn_index']
        if type(turn_index) is not int or not previous <= turn_index < len(turns) or turn_index < 0:
            raise ValueError('PODCAST_CUE_SOURCE_MISMATCH')
        previous = turn_index
        offset_in_turn=len(used[turn_index])
        used[turn_index] += cue['text']
        source_index, source_turn, original = turns[turn_index]
        anchor = anchors[i]
        text = normalize(cue['text'])
        offset, quote = anchor['script_offset'], anchor['text']
        boundary, boundary_text = anchor['boundary_script_offset'], anchor['boundary_text']
        if (type(offset) is not int or type(boundary) is not int or not 0 <= boundary <= offset
                or len(quote) < 8 or text[offset:offset+len(quote)] != quote
                or not boundary_text or text[boundary:boundary+len(boundary_text)] != boundary_text
                or not all(type(t) in (int, float) and math.isfinite(t) for t in
                           (anchor['boundary_audio_start'], anchor['audio_start']))
                or not 0 <= anchor['boundary_audio_start'] <= anchor['audio_start'] < duration
                or (i > 0 and anchor['boundary_audio_start'] != starts[i])):
            raise ValueError('PODCAST_CUE_ALIGNMENT_INVALID')
        parent = base['segments'][source_index]
        bound,evidence=sources(episode,references(episode,source_index,source_turn,offset_in_turn,len(used[turn_index])))
        result.append({'id': f"episode-{base['episode_index']}-cue-{i}", 'index': i,
                       'source_segment_index': source_index, 'source_turn_index': source_turn,
                       'start': starts[i], 'end': starts[i+1] if i+1 < len(starts) else duration,
                       'title': cue['title'], 'text': cue['text'],
                       **({'claim_id':parent['claim_id']} if 'claim_id' in parent else {'claim_ids':list(dict.fromkeys(c['claim_id'] for c in bound))}),
                       'source_refs':[{'segment_index':source_index,'turn_index':source_turn,'start':offset_in_turn,'end':len(used[turn_index])}],
                       'source_bindings':bound,'beat_ids':parent['beat_ids'], 'evidence':evidence,
                       'turns': [{'speaker': original['speaker'], 'text': cue['text']}]})
    if used != [turn['text'] for _, _, turn in turns]:
        raise ValueError('PODCAST_CUE_SOURCE_MISMATCH')
    return {**base, 'granularity': 'script_cue', 'segments': result, 'anchors': deepcopy(anchors),
            'alignment_method': alignment['method'], 'alignment_producer': alignment['producer']}


def webvtt(timeline):
    def timestamp(seconds):
        ms = round(seconds * 1000)
        return f'{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02}.{ms % 1000:03}'

    # VTT 文字不解讀教材中的 HTML；換行保留在同一個段落 cue。
    def plain(text):
        return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('\n\n', '\n')

    return 'WEBVTT\n\n' + '\n\n'.join(
        f"{s['id']}\n{timestamp(s['start'])} --> {timestamp(s['end'])}\n{plain(s['text'])}"
        for s in timeline['segments']) + '\n'


def with_source_ranges(timeline, episode):
    """舊影片只保存 turn 索引；唯讀補出逐字範圍，不重寫 manifest／hash。"""
    from .podcast_script import digest
    if timeline.get('script_sha256') != digest(episode['script']):
        raise ValueError('PODCAST_TIMELINE_SOURCE_MISMATCH')
    result=deepcopy(timeline);used={};previous=(-1,-1)
    try:
        for cue in result['segments']:
            refs=cue.get('source_refs')
            if refs is None and 'source_segment_index' in cue:
                refs=[{'segment_index':cue['source_segment_index'],'turn_index':cue['source_turn_index']}]
            if not isinstance(refs,list) or len(refs)!=len(cue['turns']):raise ValueError()
            projected=[]
            for ref,spoken in zip(refs,cue['turns']):
                i,j=ref['segment_index'],ref['turn_index']
                if type(i) is not int or type(j) is not int or min(i,j)<0 or (i,j)<previous:raise ValueError()
                previous=(i,j)
                original=episode['script']['segments'][i]['turns'][j]
                start=used.get((i,j),0);end=start+len(spoken['text'])
                if (original['speaker']!=spoken['speaker'] or original['text'][start:end]!=spoken['text']
                        or ('start' in ref and ref['start']!=start) or ('end' in ref and ref['end']!=end)):raise ValueError()
                projected.append({'segment_index':i,'turn_index':j,'start':start,'end':end})
                used[(i,j)]=end
            cue['source_refs']=projected
        expected={(i,j):len(t['text']) for i,s in enumerate(episode['script']['segments']) for j,t in enumerate(s['turns'])}
        if used!=expected:raise ValueError()
    except (KeyError,IndexError,TypeError,ValueError):
        raise ValueError('PODCAST_TIMELINE_SOURCE_MISMATCH') from None
    return result
