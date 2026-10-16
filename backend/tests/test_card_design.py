from copy import deepcopy
import json
from pathlib import Path

import httpx
import pytest

from runtime.card_design import CardDesignError, DESIGN_SCHEMA, POLICY, checked_design, resolve_plan, source_input, validate_plan
from runtime import card_generation, luna_test, voice
from runtime.semantic_service import request_semantics


def plan():
    return {'pages': [{'title': '記住核心機制', 'layout': 'compare',
        'takeaway': {'text': '兩者的差異取決於原文條件。', 'source_refs': [0, 1]},
        'blocks': [{'heading': label, 'text': text, 'icon': 'none', 'source_refs': [i]}
                   for i, (label, text) in enumerate([('第一種', '保留第一種的必要條件。'), ('第二種', '保留第二種的必要條件。')])], 'links': []}],
        'details_refs': [2]}


def test_design_can_merge_and_move_details_without_losing_source_identity():
    value = validate_plan(plan(), 3, 3)
    bindings = [{'claim_ids': [f'claim-{i}'], 'relation_ids': [], 'evidence_ids': [f'evidence-{i}']} for i in range(3)]
    resolved = resolve_plan(value, bindings, {'model_id': 'synthetic', 'revision': 'test', 'api_protocol': 'test'})
    assert resolved['schema'] == POLICY
    assert resolved['pages'][0]['takeaway']['claim_ids'] == ['claim-0', 'claim-1']
    assert resolved['details_claim_ids'] == ['claim-2']
    assert checked_design(resolved) == resolved
    resolved['pages'][0]['takeaway']['text'] = 'tampered'
    with pytest.raises(CardDesignError): checked_design(resolved)


def test_invented_sources_unaccounted_claims_and_invalid_fields_are_rejected():
    for change in (
        lambda p: p['pages'][0]['blocks'][0].update(source_refs=[99]),
        lambda p: p.update(details_refs=[]),
        lambda p: p['pages'][0]['blocks'][0].update(icon='remote-html'),
        lambda p: p['pages'][0]['blocks'][0].update(text='bad\nline'),
    ):
        value = plan(); change(value)
        with pytest.raises(CardDesignError): validate_plan(value, 3, 3)


def test_long_card_text_is_preserved_for_actual_browser_wrapping():
    value = plan()
    value['pages'][0]['blocks'][0]['text'] = '完整文字由實際排版換行，不能因粗略字寬而退件。' * 8
    assert validate_plan(value, 3, 3) == value


def test_flow_requires_supported_adjacent_links():
    value = plan(); value['pages'][0]['layout'] = 'flow'
    with pytest.raises(CardDesignError): validate_plan(value, 3, 3)
    value['pages'][0]['links'] = [{'source': 0, 'target': 1, 'label': '條件成立', 'source_refs': [0, 1]}]
    assert validate_plan(value, 3, 3) == value


def test_source_and_evidence_namespaces_are_distinct_and_original_text_is_kept():
    claim = {'claim_id': 'claim-a', 'text': 'q = [1, 0]\nKeep the original formula.', 'evidence': [{'evidence_id': 'evidence-a', 'quote': 'q = [1, 0]'}]}
    card = {'concept_id': 'concept-a', 'label': 'Synthetic formula', 'claims': [claim]}
    context = {'concepts': [card], 'relations': [], 'relation_evidence': []}
    original = deepcopy(card)
    payload, bindings = source_input(card, context)
    assert payload['sources'][0]['ref'] == 0
    assert payload['sources'][0]['evidence_refs'] == ['E0']
    assert payload['evidence'][0]['block_id'] == 'E0'
    assert bindings[0]['claim_ids'] == ['claim-a'] and card == original


def lock():
    value = json.loads((Path(__file__).parents[2] / 'local_ai/runtime-lock.json').read_text())
    value['concept_card'] = {'prompt': 'Synthetic card design', 'max_tokens': 8192,
                             'generation': deepcopy(value['material_semantics']['generation'])}
    return value


def test_gemma_uses_existing_pod_endpoint_not_luna(monkeypatch):
    monkeypatch.delenv('STUDYDY_TEXT_TEST_PROVIDER', raising=False)
    monkeypatch.setenv('STUDYDY_SEMANTIC_BASE_URL', 'http://model-bridge:18000')
    monkeypatch.setattr(voice, 'provider', lambda *_: pytest.fail('Gemma must not call Luna provider'))
    calls = []; runtime_lock = lock()
    def respond(request):
        calls.append(request.url.path)
        assert request.url.host == 'model-bridge'
        body = json.loads(request.content)
        assert body['model'] == runtime_lock['semantic_service']['model_id']
        if request.url.path == '/tokenize': return httpx.Response(200, json={'count': 200, 'max_model_len': 65536})
        assert body['response_format']['json_schema']['name'] == 'concept_card'
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(plan())}}],
                                        'usage': {'prompt_tokens': 200, 'completion_tokens': 100, 'total_tokens': 300}})
    usage = {}
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = request_semantics(client, runtime_lock=runtime_lock, task='concept_card', request={'sources': []}, response_schema=DESIGN_SCHEMA, usage=usage)
    assert result == plan() and calls == ['/tokenize', '/v1/chat/completions']
    assert usage['total_tokens'] == 300
    assert card_generation.provider_identity(card_generation.configured_lock())['api_protocol'] == 'openai-chat-completions/v1'


def test_luna_remains_explicit_temporary_route(monkeypatch):
    monkeypatch.setenv('STUDYDY_TEXT_TEST_PROVIDER', 'codex-exec-luna')
    calls = []
    def provider(path, body):
        calls.append(path)
        assert body['task'] == 'concept_card' and body['schema'] == DESIGN_SCHEMA
        return plan()
    monkeypatch.setattr(voice, 'provider', provider)
    with httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail('Luna must not call the Pod'))) as client:
        result = request_semantics(client, runtime_lock=luna_test.test_lock(lock()), task='concept_card', request={'sources': []}, response_schema=DESIGN_SCHEMA)
    assert result == plan() and calls == ['/semantics']
    assert card_generation.provider_identity(card_generation.configured_lock())['api_protocol'] == 'codex-exec-luna/v1'
