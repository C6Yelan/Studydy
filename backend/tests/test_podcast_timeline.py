from copy import deepcopy
import pytest
from runtime.podcast_timeline import build_timeline, align_script_timeline, webvtt


def example():
    episode = {'audio': {'sha256': 'audio', 'duration_seconds': 2.5},
               'script': {'segments': [{'claim_id': 'claim', 'turns': [{'speaker': 'host', 'text': '<b>原稿</b>'},
                                                                          {'speaker': 'guest', 'text': '補充'}]}]}}
    manifest = {'audio_sha256': 'audio', 'duration': 2.5, 'input_sha256': 'input',
                'alignment_method': 'real-asr', 'alignment_producer': 'whisper', 'source_resolver': '/source',
                'anchors': [], 'scenes': [{'index': 0, 'claim_id': 'claim', 'start': 0, 'end': 2.5,
                                          'evidence': [{'page_ref': 1}], 'title': '標題'}]}
    return episode, manifest


def test_preserves_transcript_speakers_and_evidence_without_inventing_turn_times():
    episode, manifest = example()
    result = build_timeline('podcast', 0, episode, manifest)
    segment = result['segments'][0]
    assert segment['turns'] == episode['script']['segments'][0]['turns']
    assert segment['evidence'] == manifest['scenes'][0]['evidence']
    assert result['granularity'] == 'segment'
    assert '00:00:00.000 --> 00:00:02.500' in webvtt(result)
    assert '&lt;b&gt;原稿&lt;/b&gt;' in webvtt(result)
    original = deepcopy(episode)
    segment['turns'][0]['text'] = 'changed'
    assert episode == original


@pytest.mark.parametrize('change', ['audio', 'claim', 'gap', 'nonfinite'])
def test_rejects_stale_sources_or_invalid_times(change):
    episode, manifest = example()
    if change == 'audio': manifest['audio_sha256'] = 'another-audio'
    if change == 'claim': manifest['scenes'][0]['claim_id'] = 'another-claim'
    if change == 'gap': manifest['scenes'][0]['start'] = 0.1
    if change == 'nonfinite': manifest['scenes'][0]['end'] = float('nan')
    with pytest.raises(ValueError): build_timeline('podcast', 0, episode, manifest)


def test_timeline_api_authorization_and_unprepared_state(monkeypatch):
    from types import SimpleNamespace
    from uuid import uuid4
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from runtime.api import scene_routes
    episode, manifest = example()
    state = {'index': 0, 'status': 'ready', 'manifest': manifest}

    def trusted(request, settings):
        value = request.cookies.get('owner')
        if value is None: raise HTTPException(401)
        return SimpleNamespace(learner_id=value)

    def read(owner, *args, **kwargs):
        if owner != 'source-owner': raise HTTPException(404)
        return {'episodes': [episode]}

    monkeypatch.setattr(scene_routes.podcasts, 'read_podcast', read)
    monkeypatch.setattr(scene_routes.podcast_videos, 'ready_manifest', lambda *a, **kw: None)
    monkeypatch.setattr(scene_routes.podcast_scenes, 'read', lambda *a, **kw: {'episodes': [state]})
    app = FastAPI()
    scene_routes.install(app, SimpleNamespace(dsn=None), trusted, lambda *a: None)
    url = f'/v1/podcasts/{uuid4()}/episodes/0'
    with TestClient(app) as client:
        assert client.get(url + '/timeline').status_code == 401
        client.cookies.set('owner', 'other')
        assert client.get(url + '/subtitles').status_code == 404
        client.cookies.set('owner', 'source-owner')
        assert client.get(url + '/timeline').json()['segments'][0]['end'] == 2.5
        assert client.get(url + '/subtitles').text.startswith('WEBVTT')
        state['status'] = 'unprepared'
        assert client.get(url + '/timeline').status_code == 409


def refined_example():
    episode, manifest = example()
    texts = ['規格寫上的資料率，', '和應用程式實際量到的速率。']
    episode['script']['segments'][0]['turns'] = [{'speaker': 'host', 'text': ''.join(texts)}]
    cues = [{'turn_index': 0, 'text': text, 'title': '原稿片段'} for text in texts]
    starts = [0, 1.2]
    anchors = []
    for start, text in zip(starts, texts):
        quote = ''.join(c for c in text if c.isalnum())
        anchors.append({'script_offset': 0, 'text': quote, 'audio_start': start,
                        'boundary_script_offset': 0, 'boundary_text': quote, 'boundary_audio_start': start})
    alignment = {'starts': starts, 'anchors': anchors, 'duration': 2.5,
                 'method': 'whisper-phonetic-boundaries/v2', 'producer': 'whisper'}
    base = build_timeline('podcast', 0, episode, manifest)
    manifest['script_alignment'] = {'cues': cues, 'alignment': alignment,
                                    'audio_sha256': base['audio_sha256'], 'script_sha256': base['script_sha256']}
    return episode, manifest


def test_refined_timeline_follows_script_cues_and_retains_original_source():
    episode, manifest = refined_example()
    timeline = build_timeline('podcast', 0, episode, manifest)
    assert timeline['granularity'] == 'script_cue'
    assert [s['start'] for s in timeline['segments']] == [0, 1.2]
    assert [s['source_segment_index'] for s in timeline['segments']] == [0, 0]
    assert ''.join(s['text'] for s in timeline['segments']) == episode['script']['segments'][0]['turns'][0]['text']
    assert all(s['claim_id'] == 'claim' and s['turns'][0]['speaker'] == 'host' for s in timeline['segments'])


@pytest.mark.parametrize('change', ['script', 'text', 'time'])
def test_refined_timeline_rejects_stale_or_estimated_alignment(change):
    episode, manifest = refined_example()
    refinement = manifest['script_alignment']
    if change == 'script': refinement['script_sha256'] = 'old-script'
    if change == 'text': refinement['cues'][0]['text'] += '新增'
    if change == 'time': refinement['alignment']['starts'][1] = 1.3
    with pytest.raises(ValueError): build_timeline('podcast', 0, episode, manifest)
