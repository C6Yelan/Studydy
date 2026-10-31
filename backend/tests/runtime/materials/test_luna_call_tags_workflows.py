"""真 worker、隔離 DB、真 provider 與假 CLI 的工作標籤／終態驗收。"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from product_fixtures import closed_loop
from qa_fixtures import qa_model
from research_fixtures import create_confirmed_research, review_provider, search_to_selection
from test_card_generation import create as create_cards, response as card_response, finish as finish_cards
from test_luna_call_log import fake_codex
from test_podcast_process import provider_server
from luna_call_log import call_records
from runtime import card_generation, card_sets, luna_calls, provider_http, research, topics, voice
from runtime.storage.database import database_session
from runtime.storage.tables import MaterialResearch, Podcast, PodcastScenes, PodcastVideo
from sqlalchemy import select

PLAN = {'title': '佇列入門', 'goals': ['理解 FIFO'], 'topics': ['佇列的資料順序']}


@pytest.fixture
def cli_workflow(closed_loop, provider_server, fake_codex, monkeypatch, tmp_path):
    monkeypatch.setenv('STUDYDY_TEXT_TEST_PROVIDER', 'codex-exec-luna')
    # Luna 文字請求一律經 request_semantics → provider 本機傳輸 _host。
    actual = provider_http._host

    def answer(model):
        def call(path, body):
            if path != '/semantics':
                return actual(path, body)
            value = model(path, body)
            if value is None:
                monkeypatch.delenv('SYNTHETIC_RESPONSE_JSON', raising=False)
            else:
                monkeypatch.setenv('SYNTHETIC_RESPONSE_JSON', json.dumps(value, ensure_ascii=False))
            return actual(path, body)
        return call

    return answer


def records(tmp_path, feature):
    return [r for r in call_records(tmp_path / 'luna-calls.jsonl') if r['tags']['feature'] == feature]


def assert_tags(rows, fixture, feature, work, *, material=True):
    owner, source, *_ = fixture
    assert rows
    for row in rows:
        assert row['tags']['feature'] == feature
        assert row['tags']['learner_id'] == str(owner.learner_id)
        assert row['tags']['material_id'] == (str(source.material_id) if material else None)
        assert row['tags']['work_id'] == str(work)
        assert row['tags']['attempt_id']


def failures(tmp_path):
    return [json.loads(p.read_text()) for p in (tmp_path / 'artifacts' / 'failures').glob('*.json')]


def test_cards_real_caller_keeps_attempt_across_steps_and_reuses_cache(closed_loop, cli_workflow, monkeypatch, tmp_path, fake_codex):
    owner, _, _, _, dsn, _ = closed_loop
    actual = cli_workflow(card_response)
    monkeypatch.setattr(provider_http, '_host', actual)
    identity = create_cards(closed_loop)
    finish_cards(dsn, None)
    assert card_sets.generation_status(owner.learner_id, identity, dsn=dsn)['status'] == 'ready'
    rows = records(tmp_path, 'cards')
    assert_tags(rows, closed_loop, 'cards', identity)
    assert len(rows) == 2
    assert len({r['tags']['attempt_id'] for r in rows}) == 1
    cached = create_cards(closed_loop, 'cached')
    finish_cards(dsn, None)
    assert card_sets.generation_status(owner.learner_id, cached, dsn=dsn)['cached'] == 1
    assert len(records(tmp_path, 'cards')) == 2
    assert len(fake_codex.read_text().splitlines()) == 2
    assert failures(tmp_path) == []


def test_cards_only_terminal_rejection_saves_private_candidate(closed_loop, cli_workflow, monkeypatch, tmp_path, caplog):
    owner, _, _, _, dsn, _ = closed_loop
    secret = '合成私密候選不可寫一般日誌'
    def reject(path, body):
        assert failures(tmp_path) == []
        result = card_response(path, body)
        if 'pages' in result:
            result['pages'][0]['takeaway']['text'] = secret
        else:
            result = {'supported': False, 'core_preserved': False, 'issues': ['合成私密退回理由']}
        return result
    monkeypatch.setattr(provider_http, '_host', cli_workflow(reject))
    identity = create_cards(closed_loop)
    finish_cards(dsn, None)
    assert card_sets.generation_status(owner.learner_id, identity, dsn=dsn)['status'] == 'failed'
    paths = list((tmp_path / 'artifacts' / 'failures').glob('*.json'))
    assert len(paths) == 1
    text = paths[0].read_text()
    assert secret in text and '合成私密退回理由' in text
    assert paths[0].stat().st_mode & 0o777 == 0o600
    assert paths[0].parent.stat().st_mode & 0o777 == 0o700
    assert secret not in caplog.text
    assert secret not in (tmp_path / 'luna-calls.jsonl').read_text()


def test_voice_real_caller_tags_every_reasoning_step(closed_loop, cli_workflow, monkeypatch, tmp_path):
    owner, source, _, _, dsn, _ = closed_loop
    monkeypatch.setattr(voice, 'provider', cli_workflow(lambda path, body: qa_model(body)))
    conversation = voice.create(owner.learner_id, source.material_id, 'voice-tags', dsn=dsn)
    turn = voice.add_turn(owner.learner_id, conversation['conversation_id'], 'turn', 'Stack 是什麼？', dsn=dsn)
    assert voice.step(dsn=dsn)
    saved = voice.read(owner.learner_id, conversation['conversation_id'], dsn=dsn)['turns'][0]
    assert saved['status'] == 'ready', saved['error_code']
    rows = records(tmp_path, 'voice')
    assert_tags(rows, closed_loop, 'voice', turn['turn_id'])
    assert len(rows) == 2 and len({r['tags']['attempt_id'] for r in rows}) == 1
    assert failures(tmp_path) == []


@pytest.mark.parametrize('supplement', [False, True])
def test_topic_real_caller_nullable_material_tags(closed_loop, cli_workflow, monkeypatch, tmp_path, supplement):
    owner, source, _, _, dsn, _ = closed_loop
    monkeypatch.setattr(topics, 'provider', cli_workflow(lambda *_: PLAN))
    topic = topics.create(owner.learner_id, '理解佇列', 'topic-tags', material_id=source.material_id if supplement else None, dsn=dsn)
    assert topics.step(dsn=dsn)
    assert topics.read(owner.learner_id, topic['topic_id'], dsn=dsn)['status'] == 'ready'
    rows = records(tmp_path, 'topic')
    assert_tags(rows, closed_loop, 'topic', topic['topic_id'], material=supplement)
    assert len(rows) == 1


def test_research_real_caller_preserves_attempt_across_worker_steps(closed_loop, cli_workflow, monkeypatch, tmp_path):
    owner, source, _, _, dsn, _ = closed_loop
    monkeypatch.setattr(research, 'provider', cli_workflow(review_provider))
    monkeypatch.setattr(research.research_sources, 'search', lambda *a, **k: ([], None, []))
    work = create_confirmed_research(owner.learner_id, source.material_id, '佇列', 'research-tags', dsn=dsn)
    search_to_selection(owner.learner_id, work['research_id'], dsn)
    rows = records(tmp_path, 'research')
    assert_tags(rows, closed_loop, 'research', work['research_id'])
    assert len(rows) >= 2 and len({r['tags']['attempt_id'] for r in rows}) == 1
    with database_session(dsn) as db:
        assert db.get(MaterialResearch, work['research_id']).search_state['luna_attempt'] == rows[0]['tags']['attempt_id']



def test_cards_intermediate_rejection_then_success_does_not_save_candidates(closed_loop, cli_workflow, monkeypatch, tmp_path, caplog):
    owner, _, _, _, dsn, _ = closed_loop
    reviews = []
    def repaired(path, body):
        result = card_response(path, body)
        if 'pages' not in result:
            reviews.append(result)
            if len(reviews) == 1:
                return {'supported': False, 'core_preserved': False, 'issues': ['合成私密補正理由']}
        return result
    monkeypatch.setattr(provider_http, '_host', cli_workflow(repaired))
    identity = create_cards(closed_loop)
    finish_cards(dsn, None)
    assert card_sets.generation_status(owner.learner_id, identity, dsn=dsn)['status'] == 'ready'
    rows = records(tmp_path, 'cards')
    assert len(rows) == 4 and len({r['tags']['attempt_id'] for r in rows}) == 1
    assert failures(tmp_path) == []
    assert list((tmp_path / 'failures').glob('*.json')) == []
    assert '合成私密補正理由' not in caplog.text
    assert '合成私密補正理由' not in (tmp_path / 'luna-calls.jsonl').read_text()


def test_podcast_workers_real_calls_for_script_title_and_video(closed_loop, cli_workflow, monkeypatch, tmp_path, fake_codex):
    import base64
    import subprocess
    import sys
    import video_service
    from test_podcast_provider import host as provider
    from test_podcasts import create, wav
    from test_podcast_videos import bundle
    from runtime import podcast_worker, podcasts, podcast_scenes, podcast_videos
    from runtime.scene_alignment import normalized

    owner, _, _, _, dsn, _ = closed_loop
    actual = provider_http._host
    def title(path, body):
        # 講稿與核對也走 /semantics；只有集名請求帶 concepts。
        if path != '/semantics' or 'concepts' not in body['request']:
            return actual(path, body)
        monkeypatch.setenv('SYNTHETIC_RESPONSE_JSON', json.dumps({'title': '堆疊的取出順序'}))
        try:
            return actual(path, body)
        finally:
            monkeypatch.delenv('SYNTHETIC_RESPONSE_JSON')
    monkeypatch.setattr(provider_http, '_host', title)
    identity = create(closed_loop, video=True)['podcast_id']
    podcast_worker.step(dsn=dsn)
    saved = podcasts.read_podcast(owner.learner_id, identity, dsn=dsn)
    assert saved['episodes'][0]['script'], saved
    assert saved['episodes'][0]['title'] == '堆疊的取出順序'
    composite = luna_calls.identity((identity, 0))
    scripts = records(tmp_path, 'podcast_script')
    titles = records(tmp_path, 'podcast_title')
    assert_tags(scripts, closed_loop, 'podcast_script', composite)
    assert_tags(titles, closed_loop, 'podcast_title', composite)
    assert len(scripts) == 2 and len(titles) == 1
    assert podcasts.finish_step(podcasts.claim_step(dsn=dsn), audio=wav(), audio_provider='synthetic', dsn=dsn)
    audio_before = podcasts.read_podcast(owner.learner_id, identity, dsn=dsn)['episodes'][0]['audio']
    episode = podcasts.read_podcast(owner.learner_id, identity, dsn=dsn)['episodes'][0]
    expected = bundle({'episode': episode})

    def alignment(body, **kwargs):
        texts = body['texts']
        starts = [i / len(texts) for i in range(len(texts))]
        return {'starts': starts, 'anchors': [
            {'script_offset': 0, 'text': normalized(t)[:12], 'audio_start': starts[i],
             'boundary_script_offset': 0, 'boundary_text': normalized(t)[:12], 'boundary_audio_start': starts[i]}
            for i, t in enumerate(texts)], 'duration': 1, 'method': 'synthetic', 'producer': 'synthetic'}
    monkeypatch.setattr(provider, 'align_audio', alignment)
    monkeypatch.setenv('STUDYDY_VIDEO_PYTHON', sys.executable)
    monkeypatch.setenv('STUDYDY_VIDEO_WORK_DIR', str(tmp_path / 'video-work'))
    monkeypatch.setattr(video_service, 'ensure_space', lambda _: None)
    def render(args, env, timeout, cancelled):
        output = Path(args[5])
        if args[-1] == '--validate':
            output.write_text(json.dumps({'plan': expected['plan']}))
        else:
            output.write_text(json.dumps(expected['render']))
            output.with_suffix('.mp4').write_bytes(base64.b64decode(expected['video']))
        return 0
    monkeypatch.setattr(video_service, '_process', render)
    assert podcast_videos.step(dsn=dsn)
    videos = podcast_videos.read(owner.learner_id, identity, 0, dsn=dsn)
    assert videos['status'] == 'ready', videos
    video_rows = records(tmp_path, 'podcast_video')
    assert len(video_rows) == 1
    with database_session(dsn) as db:
        video_id = db.scalar(select(PodcastVideo).where(PodcastVideo.podcast_id == identity)).video_id
    assert_tags(video_rows, closed_loop, 'podcast_video', video_id)
    assert podcasts.read_podcast(owner.learner_id, identity, dsn=dsn)['episodes'][0]['audio'] == audio_before
    all_rows = call_records(tmp_path / 'luna-calls.jsonl')
    cli_calls = [json.loads(line) for line in fake_codex.read_text().splitlines()]
    assert len(all_rows) == len(cli_calls) == 4
    report_path = Path(__file__).parents[4] / 'ops' / 'podcast' / 'luna_report.py'
    output = subprocess.check_output([sys.executable, str(report_path), '--log', str(tmp_path / 'luna-calls.jsonl'),
        '--material', str(closed_loop[1].material_id)], text=True)
    reported = json.loads(output)
    assert {row['feature']: row['calls'] for row in reported} == {
        'podcast_script': 2, 'podcast_title': 1, 'podcast_video': 1}
    assert all(row['failures'] == 0 for row in reported)
    assert sum(row['elapsed_seconds'] for row in reported) == pytest.approx(sum(row['elapsed_seconds'] for row in all_rows), abs=0.000003)
    for call in cli_calls:
        assert '"tags"' not in call['stdin']
        assert str(owner.learner_id) not in call['stdin']
    assert failures(tmp_path) == []


@pytest.mark.parametrize('malformed', [False, True])
def test_legacy_flow_scene_worker_uses_real_tagged_source_check(closed_loop, cli_workflow, monkeypatch, tmp_path, caplog, malformed):
    from test_podcast_provider import host as provider
    from test_podcasts import create, complete
    from runtime import podcasts, podcast_scenes
    from runtime.scene_alignment import normalized

    owner, _, _, _, dsn, _ = closed_loop
    identity = create(closed_loop)['podcast_id']
    with database_session(dsn) as db:
        row = db.get(Podcast, identity)
        episodes = deepcopy(row.episodes)
        episodes[0]['claims'][0]['text'] = '流程：1.放入元素 2.取出元素'
        row.episodes = episodes
    complete(dsn)
    podcast_scenes.prepare(owner.learner_id, identity, dsn=dsn)
    audio_before = podcasts.read_podcast(owner.learner_id, identity, dsn=dsn)['episodes'][0]['audio']
    def alignment(body, **kwargs):
        text = normalized(body['texts'][0])
        return {'starts': [0], 'anchors': [{'script_offset': 0, 'text': text[:12], 'audio_start': 0,
            'boundary_script_offset': 0, 'boundary_text': text[:12], 'boundary_audio_start': 0}],
            'duration': 1, 'method': 'synthetic', 'producer': 'synthetic'}
    monkeypatch.setattr(provider, 'align_audio', alignment)
    secret = '合成私密分鏡錯誤候選'
    if malformed:
        monkeypatch.setenv('SYNTHETIC_RESPONSE_JSON', json.dumps({'items': [], 'private_candidate': secret}))
    assert podcast_scenes.step(dsn=dsn)
    saved = podcast_scenes.read(owner.learner_id, identity, dsn=dsn)['episodes'][0]
    assert saved['status'] == ('failed' if malformed else 'ready'), saved
    if malformed:
        assert saved['error_code'] == 'SCENE_SOURCE_CHECK_INVALID'
        documents = failures(tmp_path)
        assert len(documents) == 1 and secret in json.dumps(documents[0], ensure_ascii=False)
        assert documents[0]['reason'] == saved['error_code']
        assert secret not in caplog.text and secret not in (tmp_path / 'luna-calls.jsonl').read_text()
    rows = records(tmp_path, 'podcast_scenes')
    assert len(rows) == 1
    with database_session(dsn) as db:
        scene_id = db.scalar(select(PodcastScenes).where(PodcastScenes.podcast_id == identity)).scene_id
    assert_tags(rows, closed_loop, 'podcast_scenes', scene_id)
    assert podcasts.read_podcast(owner.learner_id, identity, dsn=dsn)['episodes'][0]['audio'] == audio_before


@pytest.mark.parametrize('feature', ['voice', 'topic', 'research'])
def test_terminal_worker_failure_saves_rejected_candidate_privately(closed_loop, cli_workflow, monkeypatch, tmp_path, caplog, feature):
    owner, source, _, _, dsn, _ = closed_loop
    secret = '合成私密錯誤候選-' + feature
    model = cli_workflow(lambda *_: {'private_candidate': secret})
    if feature == 'topic':
        module = topics
        item = topics.create(owner.learner_id, '佇列', 'failed-topic', dsn=dsn)
        read = lambda: topics.read(owner.learner_id, item['topic_id'], dsn=dsn)
        identity = item['topic_id']
    elif feature == 'voice':
        module = voice
        item = voice.create(owner.learner_id, source.material_id, 'failed-voice', dsn=dsn)
        turn = voice.add_turn(owner.learner_id, item['conversation_id'], 'failed-turn', '佇列', dsn=dsn)
        read = lambda: voice.read(owner.learner_id, item['conversation_id'], dsn=dsn)['turns'][0]
        identity = turn['turn_id']
    else:
        module = research
        item = create_confirmed_research(owner.learner_id, source.material_id, '佇列', 'failed-research', dsn=dsn)
        read = lambda: research.read(owner.learner_id, item['research_id'], dsn=dsn)
        identity = item['research_id']
    monkeypatch.setattr(module, 'provider', model)
    assert module.step(dsn=dsn)
    saved = read()
    assert saved['status'] == 'failed' and saved['error_code']
    assert not module.step(dsn=dsn)
    documents = failures(tmp_path)
    assert len(documents) == (2 if feature == 'voice' else 1)
    if feature == 'voice':
        attempts = [document['tags']['attempt_id'].rsplit(':', 1) for document in documents]
        assert {suffix for _, suffix in attempts} == {'1', '2'}
        assert len({lease for lease, _ in attempts}) == 1
    for document in documents:
        assert document['tags']['work_id'] == str(identity)
        assert document['reason'] == saved['error_code']
        assert document['rejections'] and document['candidate_available']
        assert secret in json.dumps(document, ensure_ascii=False)
    assert secret not in caplog.text
    assert secret not in (tmp_path / 'luna-calls.jsonl').read_text()


def test_podcast_terminal_rejection_persists_provider_candidate_and_reason(closed_loop, cli_workflow, monkeypatch, tmp_path, caplog):
    from test_podcasts import create
    from runtime import podcast_worker, podcasts

    owner, _, _, _, dsn, _ = closed_loop
    monkeypatch.setenv('SYNTHETIC_REJECT', '1')
    identity = create(closed_loop)['podcast_id']
    podcast_worker.step(dsn=dsn)
    saved = podcasts.read_podcast(owner.learner_id, identity, dsn=dsn)
    assert saved['status'] == 'failed'
    assert saved['error_code'] == 'PODCAST_SCRIPT_NEEDS_REVIEW'
    assert saved['episodes'][0]['script'] is None
    # 講稿在後端逐次呼叫；終態失敗的候選由後端失敗紀錄保存。
    paths = list((tmp_path / 'artifacts' / 'failures').glob('*.json'))
    assert len(paths) == 1
    private = paths[0].read_text()
    assert '空堆疊不可 pop' in private and '私密核對理由' in private
    assert paths[0].stat().st_mode & 0o777 == 0o600
    assert paths[0].parent.stat().st_mode & 0o777 == 0o700
    assert '私密核對理由' not in caplog.text
    assert '空堆疊不可 pop' not in caplog.text
    assert '私密核對理由' not in (tmp_path / 'luna-calls.jsonl').read_text()
    rows = records(tmp_path, 'podcast_script')
    assert len(rows) == 4
    assert_tags(rows, closed_loop, 'podcast_script', luna_calls.identity((identity, 0)))


def test_video_terminal_invalid_candidate_is_private_and_keeps_audio(closed_loop, cli_workflow, monkeypatch, tmp_path, caplog):
    import sys
    import video_service
    from test_podcast_provider import host as provider
    from test_podcast_videos import ready, bundle
    from runtime import podcasts, podcast_videos

    owner, _, _, _, dsn, _ = closed_loop
    identity = ready(closed_loop)
    episode = podcasts.read_podcast(owner.learner_id, identity, dsn=dsn)['episodes'][0]
    expected = bundle({'episode': episode})
    monkeypatch.setattr(provider, 'align_audio', lambda *a, **k: expected['alignment'])
    monkeypatch.setattr(video_service, 'ensure_space', lambda _: None)
    monkeypatch.setenv('STUDYDY_VIDEO_PYTHON', sys.executable)
    monkeypatch.setenv('STUDYDY_VIDEO_WORK_DIR', str(tmp_path / 'video-work'))
    secret = '合成私密影片分鏡錯誤候選'
    monkeypatch.setenv('SYNTHETIC_RESPONSE_JSON', json.dumps({'sections': [], 'private_candidate': secret}, ensure_ascii=False))
    assert podcast_videos.step(dsn=dsn)
    saved = podcast_videos.read(owner.learner_id, identity, 0, dsn=dsn)
    # 分鏡在後端生成與核對；候選由後端失敗紀錄保存。
    assert saved['status'] == 'failed' and saved['error_code'] == 'VIDEO_STORYBOARD_INVALID'
    assert podcasts.read_podcast(owner.learner_id, identity, dsn=dsn)['episodes'][0]['audio'] == episode['audio']
    documents = failures(tmp_path)
    assert len(documents) == 1
    private = json.dumps(documents[0], ensure_ascii=False)
    assert secret in private and 'VIDEO_STORYBOARD_INVALID:section count does not match narration' in private
    assert secret not in caplog.text and secret not in (tmp_path / 'luna-calls.jsonl').read_text()
    assert len(records(tmp_path, 'podcast_video')) == 1
    assert not podcast_videos.step(dsn=dsn)


def test_title_invalid_candidate_is_private_and_preserves_successful_script(closed_loop, cli_workflow, monkeypatch, tmp_path, caplog):
    from test_podcasts import create
    from runtime import podcast_worker, podcasts

    owner, _, _, _, dsn, _ = closed_loop
    secret = '合成私密集名錯誤候選，包含過長但不重複的主題描述。'
    monkeypatch.setattr(provider_http, '_host', cli_workflow(
        lambda path, body: {'title': secret} if 'concepts' in body['request'] else None))
    identity = create(closed_loop)['podcast_id']
    podcast_worker.step(dsn=dsn)
    saved = podcasts.read_podcast(owner.learner_id, identity, dsn=dsn)
    assert saved['episodes'][0]['script'] and saved['status'] != 'failed'
    assert saved['episodes'][0].get('title') != secret
    documents = failures(tmp_path)
    assert len(documents) == 1
    assert documents[0]['tags']['feature'] == 'podcast_title'
    assert documents[0]['reason'] == 'PODCAST_TITLE_INVALID'
    assert secret in json.dumps(documents[0], ensure_ascii=False)
    assert secret not in caplog.text and secret not in (tmp_path / 'luna-calls.jsonl').read_text()
