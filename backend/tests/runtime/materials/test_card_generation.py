"""隔離 DB 驗證續接、快取與晚到結果；模型回應為合成測試資料。"""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json

from product_fixtures import closed_loop
from runtime import card_generation, card_sets
from runtime.storage.tables import CardSet, database_session


SOURCE = '只有當輸入符合指定条件時才會產生結果，輸入未符合條件時不能把輸出視為已經完成。'
SHORT = '輸入符合指定条件才產生結果；未符合時不能視為完成。'


def create(fixture, key='cards'):
    owner, material, _, document, dsn, _ = fixture
    return card_sets.create_card_set(owner.learner_id, material.material_id, document['revision'],
                                    '卡組', [document['concepts'][0]['concept_id']], key, dsn=dsn)['card_set_id']


def finish(dsn, call):
    for _ in range(12):
        if not card_generation.step(dsn=dsn, call=call):
            return
    raise AssertionError('generation did not settle')


def response(path, body):
    assert path == '/semantics'
    assert body['task'] == 'concept_card'
    if 'edits' in body['schema']['properties']:
        return {'edits': [{'index': 0, 'text': SHORT}]}
    return {'reviews': [{'index': 0, 'supported': True, 'reason': ''}]}


def test_durable_steps_cache_and_original_claims(closed_loop, monkeypatch):
    owner, _, _, _, dsn, _ = closed_loop
    monkeypatch.setattr(card_generation.compact, 'source_texts', lambda *_: [SOURCE])
    identity = create(closed_loop)
    original = deepcopy(card_sets.read_card_set(owner.learner_id, identity, dsn=dsn)['cards'])
    calls = []
    def call(path, body):
        calls.append(body['prompt'])
        return response(path, body)
    assert card_generation.step(dsn=dsn, call=call)
    assert card_sets.generation_status(owner.learner_id, identity, dsn=dsn)['stage'] == 'compacting'
    assert card_generation.step(dsn=dsn, call=call)
    assert card_sets.generation_status(owner.learner_id, identity, dsn=dsn)['stage'] == 'checking'
    # 模擬另一個 worker 接手已保存步驟。
    finish(dsn, call)
    view = card_sets.read_card_set(owner.learner_id, identity, dsn=dsn)
    assert view['cards'] == original and view['generation']['status'] == 'ready'
    assert list(view['display_edits'].values()) == [[{'source': SOURCE, 'text': SHORT}]]
    assert len(calls) == 2
    cached = create(closed_loop, 'cached')
    finish(dsn, call)
    assert len(calls) == 2
    assert card_sets.generation_status(owner.learner_id, cached, dsn=dsn)['cached'] == 1


def test_provider_failure_retry_and_semantic_rejection(closed_loop, monkeypatch):
    owner, _, _, _, dsn, _ = closed_loop
    monkeypatch.setattr(card_generation.compact, 'source_texts', lambda *_: [SOURCE])
    identity = create(closed_loop)
    def failed(*_):
        raise RuntimeError('synthetic provider failure')
    finish(dsn, failed)
    progress = card_sets.generation_status(owner.learner_id, identity, dsn=dsn)
    assert progress['status'] == 'failed' and progress['completed'] == 0
    card_sets.generation_status(owner.learner_id, identity, retry=True, dsn=dsn)
    def rejected(path, body):
        if 'reviews' in body['schema']['properties']:
            return {'reviews': [{'index': 0, 'supported': False, 'reason': '合成拒絕'}]}
        return response(path, body)
    finish(dsn, rejected)
    view = card_sets.read_card_set(owner.learner_id, identity, dsn=dsn)
    assert view['generation']['status'] == 'ready'
    assert view['generation']['retained_original'] == 1
    assert list(view['display_edits'].values()) == [[]]
    with database_session(dsn) as db:
        result = next(iter(db.get(CardSet, identity).generation['results'].values()))
        assert result['review_rejections'] == [{'source': SOURCE, 'text': SHORT, 'reason': '合成拒絕'}]


def test_model_review_is_final_decision_without_numeric_string_gate(closed_loop, monkeypatch):
    owner, _, _, _, dsn, _ = closed_loop
    sources = ['Latency is 10 ms with this configuration.', '在指定條件下傳送 100 bytes，完成後才回覆。']
    edits = ['Latency: 10ms.', '傳送 101 bytes 後回覆。']
    monkeypatch.setattr(card_generation.compact, 'source_texts', lambda *_: sources)
    identity = create(closed_loop)
    calls = []
    def call(path, body):
        calls.append(body)
        if 'edits' in body['schema']['properties']:
            return {'edits': [{'index': i, 'text': value} for i, value in enumerate(edits)]}
        assert [{k: item[k] for k in ('index', 'source', 'text')} for item in body['request']['items']] == [
            {'index': i, 'source': source, 'text': edits[i]} for i, source in enumerate(sources)]
        return {'reviews': [
            {'index': 0, 'supported': True, 'reason': '僅調整空白與措辭'},
            {'index': 1, 'supported': False, 'reason': '100 改為 101，且移除了指定條件'},
        ]}
    finish(dsn, call)
    view = card_sets.read_card_set(owner.learner_id, identity, dsn=dsn)
    assert len(calls) == 2
    assert view['generation']['status'] == 'ready'
    assert list(view['display_edits'].values()) == [[{'source': sources[0], 'text': edits[0]}]]


def test_batches_shared_sources_once_and_preserves_each_concept(closed_loop, monkeypatch):
    _, _, _, document, dsn, _ = closed_loop
    first = document['concepts'][0]['concept_id']
    identities = [first, 'synthetic-second', 'synthetic-third']
    texts = {first: [SOURCE], identities[1]: [SOURCE, '另一段有獨立條件與結果的完整合成測試說明。' * 3], identities[2]: [SOURCE]}
    # 合成多觀念輸入，驗證 worker 的來源分派；選材 API 另有來源驗證測試。
    monkeypatch.setattr(card_generation, '_view', lambda *_: {'concepts': [
        {'concept_id': identity, 'label': f'概念{i}'} for i, identity in enumerate(identities)]})
    monkeypatch.setattr(card_generation.compact, 'source_texts', lambda _, identity: texts[identity])
    identity = create(closed_loop)
    with database_session(dsn) as db:
        db.get(CardSet, identity).concept_ids = identities
    calls = []
    def call(path, body):
        items = body['request']['items'];calls.append(items)
        if 'edits' in body['schema']['properties']:
            assert len(items) == 2
            assert items[0]['labels'] == ['概念0', '概念1', '概念2']
            return {'edits': [{'index': i, 'text': SHORT if i == 0 else '另一段的簡短說明。'} for i in range(2)]}
        return {'reviews': [{'index': i, 'supported': i == 0, 'reason': '' if i == 0 else '未保留獨立條件'} for i in range(2)]}
    finish(dsn, call)
    with database_session(dsn) as db:
        row = db.get(CardSet, identity);results = row.generation['results']
        assert row.status == 'ready' and set(results) == set(identities)
        assert all(result['edits'] == [{'source': SOURCE, 'text': SHORT}] for result in results.values())
        assert [results[key]['rejected'] for key in identities] == [0, 1, 0]
    assert len(calls) == 2


def test_resumes_completed_results_and_reuses_reviewed_sources(closed_loop, monkeypatch):
    _, _, _, document, dsn, _ = closed_loop
    first = document['concepts'][0]['concept_id'];second = 'synthetic-second'
    rejected = {'source': '拒絕改寫的合成原文。' * 6, 'text': '不完整改寫', 'reason': '遺漏必要條件'}
    prior = {'edits': [{'source': SOURCE, 'text': SHORT}], 'rejected': 1,
             'cached': False, 'review_rejections': [rejected]}
    monkeypatch.setattr(card_generation, '_view', lambda *_: {'concepts': [
        {'concept_id': first, 'label': '甲'}, {'concept_id': second, 'label': '乙'}]})
    monkeypatch.setattr(card_generation.compact, 'source_texts', lambda *_: [SOURCE, rejected['source']])
    identity = create(closed_loop)
    with database_session(dsn) as db:
        row = db.get(CardSet, identity);row.concept_ids = [first, second]
        row.generation = {**card_generation.initial({first: prior}), 'calls': 2}
    def unexpected(*_):
        raise AssertionError('already reviewed source requested again')
    finish(dsn, unexpected)
    with database_session(dsn) as db:
        row = db.get(CardSet, identity)
        assert row.status == 'ready' and row.generation['calls'] == 2
        assert row.generation['results'][first] == prior
        assert row.generation['results'][second] == prior


def test_token_bounded_batches_do_not_repeat_shared_source(closed_loop, monkeypatch):
    _, _, _, document, dsn, _ = closed_loop
    first = document['concepts'][0]['concept_id'];second = 'synthetic-second'
    other = SOURCE.replace('輸入', '資料')
    monkeypatch.setattr(card_generation, '_view', lambda *_: {'concepts': [
        {'concept_id': first, 'label': 'A'}, {'concept_id': second, 'label': 'B'}]})
    monkeypatch.setattr(card_generation.compact, 'source_texts', lambda _, key: [SOURCE] if key == first else [SOURCE, other])
    budget = card_generation._luna_token_count(json.dumps(
        [{'index': 0, 'labels': ['A', 'B'], 'text': SOURCE}], ensure_ascii=False)) + 5
    monkeypatch.setattr(card_generation, 'BATCH_SOURCE_TOKENS', budget)
    identity = create(closed_loop)
    with database_session(dsn) as db:
        db.get(CardSet, identity).concept_ids = [first, second]
    sent = []
    def call(path, body):
        items = body['request']['items']
        if 'edits' in body['schema']['properties']:
            assert card_generation._luna_token_count(json.dumps(items, ensure_ascii=False)) <= budget
            assert len(items) == 1;sent.append(items[0]['text'])
            return {'edits': [{'index': 0, 'text': SHORT}]}
        return {'reviews': [{'index': 0, 'supported': True, 'reason': ''}]}
    for _ in range(20):
        if not card_generation.step(dsn=dsn, call=call):
            break
    with database_session(dsn) as db:
        row = db.get(CardSet, identity)
        assert row.status == 'ready' and row.generation['calls'] == 4
        assert len(row.generation['results'][second]['edits']) == 2
    assert sent == [SOURCE, other]


def test_expired_lease_reclaims_and_deleted_set_cannot_publish(closed_loop):
    owner, _, _, _, dsn, _ = closed_loop
    identity = create(closed_loop)
    old = card_generation._claim(dsn)
    with database_session(dsn) as db:
        db.get(CardSet, identity).lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    current = card_generation._claim(dsn)
    assert current['token'] != old['token']
    card_generation._save(old, {'stage': 'complete'}, 'ready', dsn)
    assert card_sets.generation_status(owner.learner_id, identity, dsn=dsn)['status'] == 'running'
    card_sets.delete_card_set(owner.learner_id, identity, dsn=dsn)
    card_generation._save(current, {'stage': 'complete'}, 'ready', dsn)
    with database_session(dsn) as db:
        row = db.get(CardSet, identity)
        assert row.deleted_at is not None and row.generation == {}


def test_unserializable_new_state_fails_with_saved_progress_and_usage(closed_loop, monkeypatch):
    _, _, _, _, dsn, _ = closed_loop
    identity = create(closed_loop)
    def malformed(claim, state, *_):
        state['unserializable'] = '\x00'
        state['calls'] = 1
        state['estimated_input_tokens'] = 123
        state['saved_output_tokens'] = 45
    monkeypatch.setattr(card_generation, '_advance', malformed)
    assert card_generation.step(dsn=dsn)
    with database_session(dsn) as db:
        row = db.get(CardSet, identity)
        assert row.status == 'failed' and row.lease_token is None
        assert row.generation['stage'] == 'preparing'
        assert row.generation['results'] == {} and 'unserializable' not in row.generation
        assert row.generation['error_code'] == 'CARD_GENERATION_FAILED'
        assert row.generation['calls'] == 1
        assert row.generation['estimated_input_tokens'] == 123
        assert row.generation['saved_output_tokens'] == 45
