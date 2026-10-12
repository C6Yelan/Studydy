"""大型概念目錄不得阻塞新來源，也不得放寬來源及概念身分邊界。"""
from copy import deepcopy
from dataclasses import asdict

import pytest

from knowledge_map.semantic_projection import (
    SemanticState, apply_semantic_response, build_semantic_bundles, semantic_request,
)
from knowledge_map.structure import build_document_context
from test_knowledge_structure_v1 import _block, _page


def test_thousands_of_related_concepts_fit_and_resume_without_dropping_evidence():
    context = build_document_context([
        _page(1, [_block(1, i, 'paragraph', f'TCP fact {i}.') for i in range(30)])
    ], page_count=1)
    evidence = context['evidence'][0]
    state = SemanticState(concepts={f'old_{i}': {
        'label': f'Saved concept {i}', 'aliases': [], 'claims': [{
            'text': 'Saved detail ' * 30,
            'source_spans': [{'evidence_id': evidence['evidence_id'], 'quote': evidence['exact_text']}],
        }],
    } for i in range(5000)})
    state.concepts['tcp'] = {'label': '傳輸控制協定', 'aliases': ['TCP'], 'claims': []}
    # 使用 checkpoint 保存形狀重建，從游標接續而非重跑前段。
    saved = deepcopy(asdict(state))
    restored = SemanticState(**saved)
    def fits(request):
        return (sum(len(s['evidence']) for s in request['sections']) <= 4
                and len(request['existing_concepts']) <= 5
                and all('c' not in c for c in request['existing_concepts']))
    bundles = list(build_semantic_bundles(context, state=restored, fits=fits, minimum_evidence_index=7))
    assert [e['evidence_id'] for b in bundles for e in b['evidence']] == [
        e['evidence_id'] for e in context['evidence'][7:]]
    for bundle in bundles:
        request = semantic_request(context, bundle, restored)
        assert fits(request)
        assert request['existing_concepts'][0]['k'] == 'tcp'
    assert asdict(restored) == saved == asdict(state)


def test_oversized_single_candidate_can_be_omitted_without_losing_new_source():
    context = build_document_context([_page(1, [_block(1, 0, 'paragraph', 'TCP fact.')])], page_count=1)
    state = SemanticState(concepts={'tcp': {'label': 'TCP', 'aliases': ['large alias ' * 1000], 'claims': []}})
    before = deepcopy(state)
    bundle = next(build_semantic_bundles(context, state=state, fits=lambda request: not request['existing_concepts']))
    assert semantic_request(context, bundle, state)['existing_concepts'] == []
    assert bundle['evidence'] == context['evidence'] and state == before


def test_reordered_candidates_keep_their_own_source_ids():
    context = build_document_context([
        _page(1, [_block(1, 0, 'heading', 'Earlier'), _block(1, 1, 'paragraph', 'Earlier fact.')]),
        _page(2, [_block(2, 0, 'heading', 'Current'), _block(2, 1, 'paragraph', 'TCP fact.')]),
    ], page_count=2)
    context['source_pages'] = [{'source_id': 'old-source'}, {'source_id': 'new-source'}]
    def concept(label, i):
        e = context['evidence'][i]
        return {'label': label, 'aliases': [], 'claims': [{'text': e['exact_text'], 'source_spans': [{'evidence_id': e['evidence_id'], 'quote': e['exact_text']}]}]}
    state = SemanticState(concepts={'z_local': concept('Local', 3), 'a_tcp': concept('TCP', 1)})
    bundle = {'sections': [context['sections'][1]], 'evidence': context['evidence'][2:]}
    catalog = semantic_request(context, bundle, state)['existing_concepts']
    assert [c['k'] for c in catalog] == ['a_tcp', 'z_local']
    assert [c['claim_sources'] for c in catalog] == [[['old-source']], [['new-source']]]


def test_hidden_key_collision_does_not_merge_old_concept_or_redirect_relations():
    context = build_document_context([_page(1, [_block(1, 0, 'paragraph', 'Queue and Stack differ.')])], page_count=1)
    original = {'label': 'Unrelated saved meaning', 'aliases': [], 'claims': []}
    state = SemanticState(concepts={'queue': deepcopy(original)})
    bundle = {'sections': context['sections'], 'evidence': context['evidence'], 'catalog_keys': []}
    response = {'concepts': [
        {'k': 'queue', 'l': 'Queue', 'a': [], 'c': [{'m': None, 's': [0]}]},
        {'k': 'stack', 'l': 'Stack', 'a': [], 'c': [{'m': None, 's': [0]}]},
    ], 'relations': [{'s': 'queue', 't': 'stack', 'k': 'contrast', 'r': 'Different removal order.', 'e': [0], 'c': 0.9}]}
    apply_semantic_response(response, context=context, bundle=bundle, state=state)
    assert state.concepts['queue'] == original
    added = next(k for k in state.concepts if k.startswith('queue.'))
    assert state.concepts[added]['label'] == 'Queue'
    assert state.relations[0]['source_concept'] == added
    # 模型未提出新 queue 時，也不得自行引用隱藏的舊 queue。
    response['concepts'] = []
    apply_semantic_response(response, context=context, bundle=bundle, state=state)
    assert len(state.relations) == 1 and state.rejected_relations == 1


@pytest.mark.parametrize('provider', ['luna', 'gemma'])
@pytest.mark.parametrize('catalog_tokens,expected', [(8192, True), (8193, False)])
def test_catalog_token_budget_applies_even_to_single_evidence(monkeypatch, provider, catalog_tokens, expected):
    from runtime import semantic_service as service
    from test_semantic_service_v1 import _lock as _runtime_lock
    from runtime.luna_test import test_lock
    lock = _runtime_lock()
    if provider == 'luna':
        lock = test_lock(lock)
        monkeypatch.setattr(service, '_luna_input_tokens', lambda prompt, request: 100 + (catalog_tokens if request['existing_concepts'] else 0))
    else:
        import json
        def count(client, config, messages, template, check):
            request = json.loads(messages[-1]['content'].split('\nINPUT:\n', 1)[1])
            return 100 + (catalog_tokens if request['existing_concepts'] else 0)
        monkeypatch.setattr(service, '_token_count', count)
    request = {'existing_concepts': [{'k': 'tcp', 'l': 'TCP', 'a': []}], 'sections': [{'evidence': [[0, 1, 'paragraph', 'TCP fact.']]}]}
    assert service.material_request_fits(None, lock, request) is expected
