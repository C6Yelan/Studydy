"""原稿切分、實測字幕時鐘與既有教學範圍分離。"""
from copy import deepcopy
import pytest
from runtime.podcast_cues import teaching_cues, caption_units, align_captions, captions_for_episode
from runtime.podcast_video_plan import timeline_for_video
from runtime.podcast_timeline import webvtt
from runtime.scene_alignment import align_cue_groups


def sample():
    text = ('先辨認發起端與另一台主機，這兩個方向可以分別結束。'
            '發起端送出結束訊息之後，對方仍然可以傳送尚未完成的資料。'
            '最後另一台主機也送出結束訊息，原本的發起端再回傳確認。')
    episode = {'delivery': 'solo', 'claims': [{'claim_id': 'c', 'concept_id': 'concept', 'label': '雙向關閉',
               'text': text, 'evidence': [{'evidence_id': 'e', 'page_ref': 'p'}]}],
               'script': {'provider': 'synthetic', 'segments': [{'claim_id': 'c', 'turns': [{'speaker': 'host', 'text': text}]}]},
               'audio': {'sha256': 'a'*64, 'duration_seconds': 30}}
    units = caption_units([text])
    # 故意不等距；所有換字時間只能取這些 ASR 詞時間，不按字數插值。
    times = [0.4, 7.3, 15.2, 22.1]
    words = [{'word': unit['text'], 'start': times[i], 'end': times[i]+2} for i, unit in enumerate(units)]
    return episode, words


def test_captions_are_finer_while_teaching_scope_and_source_ranges_stay_whole():
    episode, words = sample(); before = deepcopy(episode)
    cues = teaching_cues(episode)
    assert len(cues) == 1
    alignment = align_cue_groups([episode['script']['segments'][0]['turns'][0]['text']], words, 30, [[0]])
    alignment.pop('groups'); alignment['producer'] = 'synthetic'
    teaching = timeline_for_video('p', 0, episode, cues, alignment, '/exact-revision')
    alignment['caption_alignment'] = align_captions([episode['script']['segments'][0]['turns'][0]['text']], words, 30)
    with_captions = timeline_for_video('p', 0, episode, cues, alignment, '/exact-revision')
    assert with_captions['segments'] == teaching['segments']
    assert len(with_captions['captions']) > len(teaching['segments'])
    assert [c['start'] for c in with_captions['captions']] == [0] + [w['start'] for w in words[1:]]
    assert ''.join(c['text'] for c in with_captions['captions']) == episode['script']['segments'][0]['turns'][0]['text']
    assert webvtt(with_captions).count(' --> ') == len(with_captions['captions'])
    assert webvtt(teaching).count(' --> ') == 1
    assert episode == before


@pytest.mark.parametrize('change', ['text', 'time', 'nan', 'anchor', 'missing'])
def test_caption_projection_rejects_foreign_text_and_unverified_timing(change):
    episode, words = sample()
    text = episode['script']['segments'][0]['turns'][0]['text']
    data = align_captions([text], words, 30)
    if change == 'text': data['cues'][0]['parts'][0]['text'] += '新增'
    elif change == 'time': data['alignment']['starts'][1] += .2
    elif change == 'nan': data['alignment']['starts'][1] = float('nan')
    elif change == 'anchor': data['alignment']['anchors'][0]['text'] = '不是這份講稿的語句'
    else: data['cues'].pop()
    with pytest.raises(ValueError, match='PODCAST_CAPTION_ALIGNMENT_INVALID'):
        captions_for_episode(episode, data)


def test_shared_word_timestamp_merges_captions_without_inventing_internal_times():
    episode, _ = sample(); text = episode['script']['segments'][0]['turns'][0]['text']
    data = align_captions([text], [{'word': text, 'start': .4, 'end': 28}], 30)
    result = captions_for_episode(episode, data)
    assert len(result) == 1 and result[0]['start'] == 0 and result[0]['end'] == 30


def test_teaching_units_preserve_dialogue_parts_and_do_not_cross_beat_boundaries():
    from test_podcast_beats import sample as beat_sample
    episode, _, _ = beat_sample(); original = deepcopy(episode)
    cues = teaching_cues(episode)
    assert len(cues) == 1 and cues[0]['parts'][0]['text'] == episode['script']['segments'][0]['turns'][0]['text']
    second = deepcopy(episode['script']['segments'][0]); second['beat_id'] = 'beat-1'
    episode['script']['segments'].append(second)
    assert len(teaching_cues(episode)) == 2
    assert original['script']['segments'][0] == episode['script']['segments'][0]
