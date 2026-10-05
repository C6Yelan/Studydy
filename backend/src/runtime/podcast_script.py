"""Teaching beats 與舊 segment 共用的來源投影；不改寫已保存文件。"""
from copy import deepcopy
from hashlib import sha256
import json

SCHEMA = 'podcast-script/v2'


def digest(script):
    return sha256(json.dumps(script, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def validate(script, episode):
    try:
        if not (set(script) == {'schema', 'segments', 'provider', 'review'} and script['schema'] == SCHEMA):raise ValueError()
        if not (isinstance(script['provider'], str) and script['provider']):raise ValueError()
        beats = script['segments']; claims = episode['claims']
        if not (isinstance(beats, list) and 1 <= len(beats) <= 12):raise ValueError()
        covered = set(); speakers = set(); total_size = 0
        for i, beat in enumerate(beats):
            if not (set(beat) == {'beat_id', 'title', 'turns'} and beat['beat_id'] == f'beat-{i}'):raise ValueError()
            if not (isinstance(beat['title'], str) and 1 <= len(beat['title']) <= 80):raise ValueError()
            if not (isinstance(beat['turns'], list) and 1 <= len(beat['turns']) <= 12):raise ValueError()
            size = 0
            for turn in beat['turns']:
                if not (set(turn) == {'speaker', 'text', 'parts'}):raise ValueError()
                if not (turn['speaker'] in ({'host', 'guest'} if episode['delivery'] == 'dialogue' else {'host'})):raise ValueError()
                speakers.add(turn['speaker'])
                if not (isinstance(turn['parts'], list) and 1 <= len(turn['parts']) <= 24):raise ValueError()
                for part in turn['parts']:
                    if not (set(part) == {'text', 'source_refs'} and isinstance(part['text'], str) and part['text']):raise ValueError()
                    if not (isinstance(part['source_refs'], list)):raise ValueError()
                    seen = set()
                    for ref in part['source_refs']:
                        if not (set(ref) == {'source_index', 'evidence_ids'}):raise ValueError()
                        index = ref['source_index']
                        if not (type(index) is int and 0 <= index < len(claims) and index not in seen):raise ValueError()
                        seen.add(index); covered.add(index)
                        ids = ref['evidence_ids']
                        if not (isinstance(ids, list) and ids and all(isinstance(e, str) for e in ids)):raise ValueError()
                        if not (len(set(ids)) == len(ids) and set(ids) <= {e['evidence_id'] for e in claims[index]['evidence']}):raise ValueError()
                if not (turn['text'] == ''.join(p['text'] for p in turn['parts'])):raise ValueError()
                if not (1 <= len(turn['text'].strip()) <= 1600):raise ValueError()
                size += len(turn['text'])
            if not (size <= 3200):raise ValueError()
            total_size += size
        # 沿用原六來源 × 1600 字的全集預算，分段自由不擴大 TTS／cue 容量。
        if total_size > 9600:raise ValueError()
        if not (covered == set(range(len(claims)))):raise ValueError()
        if not (episode['delivery'] != 'dialogue' or speakers == {'host', 'guest'}):raise ValueError()
        review = script['review']
        if not (set(review) == {'correctness', 'teaching_quality'}):raise ValueError()
        for verdict in review.values():
            if not (set(verdict) == {'passed', 'reason'} and verdict['passed'] is True and isinstance(verdict['reason'], str)):raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise ValueError('PODCAST_SCRIPT_INVALID') from None
    return deepcopy(script)


def references(episode, segment_index, turn_index=None, start=0, end=None):
    """字元範圍只取真正交集的引用；同 claim ID 不合併不同來源位置。"""
    segment = episode['script']['segments'][segment_index]
    if episode['script'].get('schema') != SCHEMA:
        claim = episode['claims'][segment_index]
        if segment.get('claim_id') != claim['claim_id']:raise ValueError('VIDEO_SOURCE_CHANGED')
        return [{'source_index': segment_index, 'evidence_ids': [e['evidence_id'] for e in claim['evidence']]}]
    turns = segment['turns'] if turn_index is None else [segment['turns'][turn_index]]
    refs = {}
    for turn in turns:
        offset = 0
        for part in turn['parts']:
            stop = offset + len(part['text'])
            if stop > start and (end is None or offset < end):
                for ref in part['source_refs']:
                    values = refs.setdefault(ref['source_index'], [])
                    values.extend(e for e in ref['evidence_ids'] if e not in values)
            offset = stop
    return [{'source_index': i, 'evidence_ids': ids} for i, ids in refs.items()]


def sources(episode, refs):
    claims = []; evidence = {}
    for ref in refs:
        claim = episode['claims'][ref['source_index']]
        claims.append({'source_index': ref['source_index'], 'concept_id': claim.get('concept_id'), 'claim_id': claim['claim_id']})
        for e in claim['evidence']:
            if e['evidence_id'] in ref['evidence_ids']:
                evidence[e['evidence_id']] = deepcopy(e)
    return claims, list(evidence.values())
