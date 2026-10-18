"""整卡設計的持久進度、來源、快取與失敗重試；模型回應均為合成測試資料。"""
from copy import deepcopy
from datetime import UTC, datetime, timedelta

from product_fixtures import closed_loop
from runtime import card_generation, card_sets
from runtime.storage.tables import CardSet
from runtime.storage.database import database_session


def create(fixture, key='cards'):
    owner, material, _, document, dsn, _ = fixture
    return card_sets.create_card_set(owner.learner_id, material.material_id, document['revision'],
        '卡組', [document['concepts'][0]['concept_id']], key, dsn=dsn)['card_set_id']


def finish(dsn, call):
    for _ in range(12):
        if not card_generation.step(dsn=dsn, call=call): return
    raise AssertionError('generation did not settle')


def response(path, body):
    assert path == '/semantics' and body['task'] == 'concept_card'
    if 'pages' not in body['schema']['properties']:
        return {'supported': True, 'core_preserved': True, 'issues': []}
    refs = [item['ref'] for item in body['request']['sources'] if item['kind'] == 'claim']
    return {'pages': [{'title': '堆疊的取出順序', 'layout': 'focus',
        'takeaway': {'text': '最後放入的元素最先取出。', 'source_refs': refs},
        'blocks': [{'heading': 'LIFO', 'text': '依後進先出順序取出元素。', 'icon': 'list-check', 'source_refs': refs}], 'links': []}], 'details_refs': []}


def test_durable_design_cache_counts_and_original_sources(closed_loop):
    owner, _, _, _, dsn, _ = closed_loop
    identity = create(closed_loop)
    original = deepcopy(card_sets.read_card_set(owner.learner_id, identity, dsn=dsn)['cards'])
    calls = []
    def call(path, body): calls.append(body['prompt']); return response(path, body)
    assert card_generation.step(dsn=dsn, call=call)
    assert card_sets.generation_status(owner.learner_id, identity, dsn=dsn)['stage'] == 'designing'
    assert card_generation.step(dsn=dsn, call=call)
    assert card_sets.generation_status(owner.learner_id, identity, dsn=dsn)['stage'] == 'checking'
    finish(dsn, call)
    view = card_sets.read_card_study(owner.learner_id, identity, dsn=dsn)
    assert view['card_count'] == 1 and view['navigation'][0]['page_count'] == 1
    card = view['batch']['cards'][0]
    assert card['claims'] == original[0]['claims']
    assert card['design']['pages'][0]['takeaway']['claim_ids'] == [c['claim_id'] for c in card['claims']]
    assert card['design']['pages'][0]['takeaway']['evidence_ids']
    assert len(calls) == 2
    cached = create(closed_loop, 'cached'); finish(dsn, call)
    assert len(calls) == 2
    assert card_sets.generation_status(owner.learner_id, cached, dsn=dsn)['cached'] == 1


def test_review_failure_stays_failed_and_retry_receives_feedback(closed_loop):
    owner, _, _, _, dsn, _ = closed_loop
    identity = create(closed_loop)
    def reject(path, body):
        if 'pages' in body['schema']['properties']: return response(path, body)
        return {'supported': False, 'core_preserved': False, 'issues': ['遺失必要的適用條件']}
    finish(dsn, reject)
    progress = card_sets.generation_status(owner.learner_id, identity, dsn=dsn)
    assert progress['status'] == 'failed' and progress['completed'] == 0
    assert progress['error_code'] == 'CARD_DESIGN_REVIEW_FAILED'
    card_sets.generation_status(owner.learner_id, identity, retry=True, dsn=dsn)
    def retry(path, body):
        if 'pages' in body['schema']['properties']: assert body['request']['previous_issues'] == ['遺失必要的適用條件']
        return response(path, body)
    finish(dsn, retry)
    assert card_sets.generation_status(owner.learner_id, identity, dsn=dsn)['status'] == 'ready'


def test_invalid_reference_repair_uses_candidate_and_records_actual_calls(closed_loop):
    owner, _, _, _, dsn, _ = closed_loop
    identity = create(closed_loop)
    attempts = 0
    def call(path, body):
        nonlocal attempts
        result = response(path, body)
        if 'pages' in body['schema']['properties']:
            attempts += 1
            if attempts == 1: result['pages'][0]['blocks'][0]['source_refs'] = [99]
            else:
                assert 'source_refs' in body['request']['previous_issues'][0]
                assert body['request']['previous_design']['pages'][0]['blocks'][0]['source_refs'] == [99]
        return result
    finish(dsn, call)
    assert card_sets.generation_status(owner.learner_id, identity, dsn=dsn)['status'] == 'ready'
    with database_session(dsn) as db:
        state = db.get(CardSet, identity).generation
        assert state['calls'] == 3
        assert next(iter(state['results'].values()))['repair_attempts'] == 1


def test_repair_uses_current_issues_and_latest_failed_candidate(closed_loop):
    owner, _, _, _, dsn, _ = closed_loop
    identity = create(closed_loop)
    attempts = 0
    def call(path, body):
        nonlocal attempts
        if 'pages' not in body['schema']['properties']:
            return {'supported': False, 'core_preserved': False, 'issues': ['不可改變限制程度']}
        attempts += 1
        value = response(path, body)
        if attempts == 2:
            assert body['request']['previous_design'] == value
            value['pages'][0]['blocks'][0]['source_refs'] = [99]
        return value
    finish(dsn, call)
    assert card_sets.generation_status(owner.learner_id, identity, dsn=dsn)['status'] == 'failed'
    card_sets.generation_status(owner.learner_id, identity, retry=True, dsn=dsn)
    def repair(path, body):
        if 'pages' in body['schema']['properties']:
            request = body['request']
            assert len(request['previous_issues']) == 1
            assert 'source_refs' in request['previous_issues'][0]
            assert request['previous_design']['pages'][0]['blocks'][0]['source_refs'] == [99]
        return response(path, body)
    finish(dsn, repair)
    assert card_sets.generation_status(owner.learner_id, identity, dsn=dsn)['status'] == 'ready'


def test_provider_switch_does_not_mix_luna_and_gemma_results(closed_loop, monkeypatch):
    owner, _, _, _, dsn, _ = closed_loop
    monkeypatch.setenv('STUDYDY_TEXT_TEST_PROVIDER', 'codex-exec-luna')
    identity = create(closed_loop)
    monkeypatch.delenv('STUDYDY_TEXT_TEST_PROVIDER')
    finish(dsn, lambda *_: (_ for _ in ()).throw(AssertionError('must not call a different provider')))
    assert card_sets.generation_status(owner.learner_id, identity, dsn=dsn)['error_code'] == 'CARD_PROVIDER_CHANGED'
    identity = create(closed_loop, key='current-provider')
    finish(dsn, response)
    design = card_sets.read_card_study(owner.learner_id, identity, dsn=dsn)['batch']['cards'][0]['design']
    assert design['provider']['api_protocol'] == 'openai-chat-completions/v1'


def test_late_model_response_cannot_restore_deleted_card(closed_loop):
    owner, _, _, _, dsn, _ = closed_loop
    identity = create(closed_loop)
    def deleted(path, body):
        card_sets.delete_card_set(owner.learner_id, identity, dsn=dsn)
        return response(path, body)
    finish(dsn, deleted)
    assert card_sets.list_card_sets(owner.learner_id, dsn=dsn)['card_sets'] == []


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
