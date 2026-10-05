"""由原稿產生教學單位與字幕讀句；兩者共用原文，沒有共用切分粒度。"""
import re


def split_text(text, limit, preferred):
    """保留每個字元；優先在標點／空白切開，時間仍須另由音訊對齊。"""
    while len(text) > limit:
        stops = [m.end() for m in re.finditer(r'[。！？!?；;，,：:、]\s*', text[:limit])]
        eligible = [i for i in stops if i >= preferred]
        if eligible:
            end = eligible[0]
        elif stops:
            end = stops[-1]
        else:
            spaces = [m.end() for m in re.finditer(r'\s+', text[:limit])]
            end = spaces[-1] if spaces else limit
            # 不把英文字內部當作句界；過長識別符交給 ASR 合併，不能猜詞內時間。
            if text[end-1:end].isascii() and text[end-1:end].isalnum() and text[end:end+1].isascii() and text[end:end+1].isalnum():
                before = re.search(r'[A-Za-z0-9_]+$', text[:end])
                if before and before.start():
                    end = before.start()
        yield text[:end]
        text = text[end:]
    if text:
        yield text


def teaching_cues(episode):
    """beat 是首要邊界；較長 beat 在既有 180 字容量內依原 turn／part 分組。"""
    from .podcast_video_plan import validate_cues
    result = []; turn_index = 0
    for i, beat in enumerate(episode['script']['segments']):
        title = beat.get('title') or episode['claims'][i].get('label', f'重點 {i+1}')
        parts = []; size = 0
        for turn in beat['turns']:
            originals = [p['text'] for p in turn.get('parts', [])] or [turn['text']]
            for original in originals:
                for text in split_text(original, 180, 100):
                    if parts and (size + len(text) > 180 or len(parts) >= 12):
                        result.append({'title': title[:40], 'parts': parts})
                        parts = []; size = 0
                    if parts and parts[-1]['turn_index'] == turn_index:
                        parts[-1]['text'] += text
                    else:
                        parts.append({'turn_index': turn_index, 'text': text})
                    size += len(text)
            turn_index += 1
        if parts:
            result.append({'title': title[:40], 'parts': parts})
    validate_cues(episode, result)
    return result


def caption_units(turns):
    return [{'turn_index': i, 'text': text} for i, turn in enumerate(turns)
            for text in split_text(turn, 42, 20)]


def align_captions(turns, words, duration):
    from .scene_alignment import align_cue_groups
    units = caption_units(turns)
    alignment = align_cue_groups([u['text'] for u in units], words, duration,
                                 [[u['turn_index']] for u in units])
    groups = alignment.pop('groups')
    cues = []
    for group in groups:
        parts = []
        for index in group:
            unit = units[index]
            if parts and parts[-1]['turn_index'] == unit['turn_index']:
                parts[-1]['text'] += unit['text']
            else:
                parts.append(dict(unit))
        cues.append({'parts': parts})
    return {'cues': cues, 'alignment': alignment}


def captions_for_episode(episode, data):
    """字幕只能逐字覆蓋同一原稿，且每次換字都有實際 ASR 錨點；不產生新來源。"""
    import math
    from .scene_alignment import normalized
    try:
        turns = [t['text'] for b in episode['script']['segments'] for t in b['turns']]
        cues = data['cues']; alignment = data['alignment']
        starts, anchors = alignment['starts'], alignment['anchors']
        duration = episode['audio']['duration_seconds']
        if (not isinstance(cues, list) or not 1 <= len(cues) <= 2000
                or len(starts) != len(cues) or len(anchors) != len(cues)
                or alignment['duration'] != duration or starts[0] != 0
                or any(type(t) not in (int, float) or not math.isfinite(t) or not 0 <= t < duration for t in starts)
                or any(a >= b for a, b in zip(starts, starts[1:]))):
            raise ValueError()
        used = [''] * len(turns); previous = -1; result = []
        for i, (cue, anchor) in enumerate(zip(cues, anchors)):
            if not cue['parts']:
                raise ValueError()
            for part in cue['parts']:
                index, text = part['turn_index'], part['text']
                if type(index) is not int or not 0 <= index < len(turns) or index < previous or not isinstance(text, str) or not text:
                    raise ValueError()
                previous = index; used[index] += text
            text = '\n'.join(p['text'] for p in cue['parts']); reference = normalized(text)
            offset, quote = anchor['script_offset'], anchor['text']
            first, beginning = anchor['boundary_script_offset'], anchor['boundary_text']
            end = starts[i+1] if i+1 < len(starts) else duration
            if (type(offset) is not int or type(first) is not int or not 0 <= first <= offset
                    or not isinstance(quote, str) or len(quote) < 8 or reference[offset:offset+len(quote)] != quote
                    or not isinstance(beginning, str) or not beginning or reference[first:first+len(beginning)] != beginning
                    or not all(type(anchor[k]) in (int, float) and math.isfinite(anchor[k]) for k in ('audio_start', 'boundary_audio_start'))
                    or not 0 <= anchor['boundary_audio_start'] <= anchor['audio_start'] < end
                    or (i > 0 and starts[i] != anchor['boundary_audio_start'])):
                raise ValueError()
            result.append({'id': f'caption-{i}', 'start': starts[i], 'end': end, 'text': text})
        if used != turns:
            raise ValueError()
        return result
    except (KeyError, IndexError, TypeError, ValueError):
        raise ValueError('PODCAST_CAPTION_ALIGNMENT_INVALID') from None
