from copy import deepcopy

import pytest

from knowledge_map import consolidation as merge
from knowledge_map.material_review import ReviewError
from knowledge_map.structure import SemanticState, apply_semantic_response, build_document_context
from knowledge_map.structure_rules import _revision
from knowledge_map.structure_validation import validate_knowledge_structure
from runtime.material_consolidation import consolidate_structure
from runtime.semantic_service import SemanticServiceError
from structure_fixtures import build_knowledge_structure
from test_knowledge_structure_v1 import _block, _page


def document(texts=None, labels=None, relations=None):
    texts = texts or ['任務重試最多 8 次，仍未完成就停止。',
                      '任務重試最多 8 次，仍未完成就停止。',
                      '實驗乙在 k=5 時比較停止策略，僅適用測試環境。']
    labels = labels or ['有限重試', '重試上限', '有限重試']
    context = build_document_context([_page(i + 1, [_block(i + 1, 0, 'paragraph', t)])
                                      for i, t in enumerate(texts)], page_count=len(texts))
    state = SemanticState()
    apply_semantic_response({'concepts': [{'k': str(i), 'l': label, 'a': [],
        'c': [{'m': None, 's': [i]}]} for i, label in enumerate(labels)],
        'relations': relations or []}, context=context,
        bundle={'evidence': context['evidence'], 'sections': context['sections']}, state=state)
    return build_knowledge_structure(context, state, source_sha256='1' * 64,
        run_id='00000000-0000-4000-8000-000000000001', produced_at='2026-09-21T00:00:00+00:00',
        runtime_lock_sha256='2' * 64, model_id='google/gemma-4-31B-it-qat-w4a16-ct',
        model_revision='52f3f65bc7a02d555763bc923bd1d9094898219d', semantic_calls=1, ocr_calls=0)


def keep(payload):
    return {'concepts': [{'concepts': [c['h']], 'title_from': c['h'],
        'claims': [{'claims': [h], 'text': None} for h in c['claims']], 'reason': '獨立觀念。'}
        for c in payload['concepts']]}


def proposal(doc):
    result = keep(merge.pack(doc, [0, 1, 2])[0])
    result['concepts'][0].update(concepts=[0, 1],
        claims=[{'claims': [0, 1], 'text': doc['concepts'][0]['claims'][0]['text']}])
    del result['concepts'][1]
    return result


def test_merge_is_once_with_all_sources_and_keeps_different_definition_and_old_revision():
    doc = document()
    before = deepcopy(doc)
    result, audit = merge.apply(doc, merge.project(doc, [0, 1, 2], proposal(doc)))
    assert validate_knowledge_structure(result) and doc == before
    assert len(result['concepts']) == 2
    first = result['concepts'][0]
    assert len(first['claims']) == 1 and first['source_pages'] == [1, 2]
    assert first['aliases'] == ['重試上限']
    assert result['concepts'][1] == doc['concepts'][2]
    assert first['evidence_refs'] == doc['concepts'][0]['evidence_refs'] + doc['concepts'][1]['evidence_refs']
    assert result['evidence'] == doc['evidence'] and result['input_binding'] == doc['input_binding']
    assert audit['concept_mapping'][doc['concepts'][0]['concept_id']] == audit['concept_mapping'][doc['concepts'][1]['concept_id']]
    assert len(result['initial_learning_path']) == 2 and result['revision'] != doc['revision']


def test_single_claim_bilingual_repetition_can_be_removed_without_changing_evidence():
    text = '任務最多重試 8 次。The task retries at most 8 times. 超過上限須交接，不能繼續。'
    doc = document([text], ['重試上限'])
    value = keep(merge.pack(doc, [0])[0])
    value['concepts'][0]['claims'][0]['text'] = '任務最多重試 8 次。超過上限須交接，不能繼續。'
    result, _ = merge.apply(doc, merge.project(doc, [0], value))
    assert validate_knowledge_structure(result)
    assert result['concepts'][0]['claims'][0]['text'].count('8') == 1
    assert result['concepts'][0]['claims'][0]['source_spans'] == doc['concepts'][0]['claims'][0]['source_spans']


def test_literal_repair_reports_every_missing_value_with_its_claim():
    doc = document()
    value = keep(merge.pack(doc, [0, 1, 2])[0])
    value['concepts'][0]['claims'][0]['text'] = '任務會重試。'
    value['concepts'][2]['claims'][0]['text'] = '實驗乙比較停止策略。'
    with pytest.raises(ReviewError, match='CONSOLIDATION_LITERAL_LOSS') as caught:
        merge.project(doc, [0, 1, 2], value)
    issues = caught.value.details['claims']
    assert [item['claims'] for item in issues] == [[0], [2]]
    assert '8' in issues[0]['missing_literals'] and 'k=5' in issues[1]['missing_literals']


@pytest.mark.parametrize('bad', ['omit_claim', 'repeat_concept', 'wrong_number', 'move_claim'])
def test_untrusted_merges_cannot_drop_claims_reuse_owners_change_numbers_or_move_sources(bad):
    doc = document()
    value = proposal(doc)
    if bad == 'omit_claim':
        value['concepts'][0]['claims'][0]['claims'] = [0]
    elif bad == 'repeat_concept':
        value['concepts'][0]['concepts'].append(0)
    elif bad == 'wrong_number':
        value['concepts'][0]['claims'][0]['text'] = '任務重試最多 9 次，仍未完成就停止。'
    else:
        value['concepts'][0]['claims'][0]['claims'] = [0, 2]
        value['concepts'][1]['claims'][0]['claims'] = [1]
    with pytest.raises(ReviewError) as caught:
        merge.project(doc, [0, 1, 2], value)
    if bad == 'move_claim':
        assert caught.value.details == {'concepts': [0, 1], 'expected_handles': [0, 1],
            'missing_handles': [1], 'unexpected_handles': [2], 'duplicate_handles': []}


def test_relations_remap_union_sources_and_prevent_new_cycles():
    relations = [{'s': str(s), 't': str(t), 'k': 'prerequisite', 'r': reason, 'e': [s, t], 'c': .8}
        for s, t, reason in [(0, 2, '先了解上限才能比較實驗。'), (1, 2, '重試定義是比較的前提。')]]
    doc = document(relations=relations)
    assert len(doc['relations']) == 2
    result, _ = merge.apply(doc, merge.project(doc, [0, 1, 2], proposal(doc)))
    assert validate_knowledge_structure(result) and len(result['relations']) == 1
    assert set(result['relations'][0]['evidence_refs']) == {e for r in doc['relations'] for e in r['evidence_refs']}
    assert set(result['relations'][0]['context_refs']) == {e for r in doc['relations'] for e in r['context_refs']}
    chain = [{'s': str(s), 't': str(t), 'k': 'prerequisite', 'r': '此步完成才可繼續下一步。', 'e': [s, t], 'c': .8}
             for s, t in [(0, 2), (2, 1)]]
    doc = document(relations=chain)
    with pytest.raises(ReviewError, match='CONSOLIDATION_RELATION_CONFLICT'):
        merge.apply(doc, merge.project(doc, [0, 1, 2], proposal(doc)))


class Archive:
    def __init__(self):
        self.values = {}
    def save_review(self, key, value):
        self.values[key] = deepcopy(value)
    def load_review(self, key, **kwargs):
        return self.values.get('cache-' + key)


def config():
    return {'material_review': {'consolidation_policy': merge.POLICY, 'prompt': 'synthetic'}}


def approved(request):
    return {'checks': [{'concept': h, 'equivalent_concepts': True, 'preserves_unique_information': True,
        'sources_support_text': True, 'duplicates_removed': True, 'reason': '逐項保留來源與獨有條件。'}
        for h in request['check_concepts']]}


def test_runtime_cross_catalog_capacity_boundaries_still_merges_and_reuses_cache(monkeypatch):
    doc = document()
    archive = Archive()
    calls = []
    def model(_client, **kw):
        request = kw['request']
        calls.append(request)
        if request['operation'] == 'consolidation_candidates':
            if len(request['catalog']) > 2:
                raise SemanticServiceError('SEMANTIC_INPUT_TOO_LARGE')
            handles = {r['h'] for r in request['catalog']}
            return {'groups': [{'concepts': [0, 1], 'reason': '同一重試上限。'}] if handles == {0, 1} else []}
        if request['operation'] == 'consolidation_rewrite':
            return proposal(doc)
        assert kw['response_schema']['$defs']['Check']['properties']['concept']['enum'] == [0, 1]
        return approved(request)
    monkeypatch.setattr('runtime.material_consolidation.request_semantics', model)
    result = consolidate_structure(doc, config(), archive, lambda: None, lambda *a: None)
    assert len(result['concepts']) == 2 and validate_knowledge_structure(result)
    assert any({r['h'] for r in c.get('catalog', [])} == {0, 1} for c in calls)
    before = len(calls)
    replay = consolidate_structure(doc, config(), archive, lambda: None, lambda *a: None)
    # 容量檢查不推論；已完成模型回應全數重用。
    assert all(len(c.get('catalog', [])) > 2 for c in calls[before:])
    assert replay['concepts'] == result['concepts']
    assert archive.values['consolidation/result']['model_calls'] == 0


def test_semantic_rejection_keeps_distinct_definitions_and_records_failed_check(monkeypatch):
    doc = document()
    archive = Archive()
    operations = []
    def model(_client, **kw):
        request = kw['request']
        operations.append(request['operation'])
        if request['operation'] == 'consolidation_candidates':
            return {'groups': []}
        if request['operation'] == 'consolidation_rewrite':
            return keep(request)
        response = approved(request)
        response['checks'][0].update(duplicates_removed=False, reason='還有重複，不能當作完成。')
        return response
    monkeypatch.setattr('runtime.material_consolidation.request_semantics', model)
    result = consolidate_structure(doc, config(), archive, lambda: None, lambda *a: None)
    assert result['concepts'] == doc['concepts'] and result['status']['quality'] == 'needs_review'
    # 原批及兩個完整候選群各遇到同樣拒絕便停止，不耗滿四份稿或無限重試。
    assert operations.count('consolidation_rewrite') == 6
    assert [b['concepts'] for b in archive.values['consolidation/result']['blocked_changes']] == [[0, 2], [1]]


def test_incremental_unchanged_content_still_runs_global_consolidation(monkeypatch):
    from runtime.material_review import review_structure
    doc = document()
    archive = Archive()
    def model(_client, **kw):
        request = kw['request']
        if request['operation'] == 'consolidation_candidates':
            return {'groups': [{'concepts': [0, 1], 'reason': '等義'}]}
        if request['operation'] == 'consolidation_rewrite':
            return proposal(doc)
        return approved(request)
    monkeypatch.setattr('runtime.material_review.request_semantics', lambda *a, **kw: pytest.fail('unchanged regular review'))
    monkeypatch.setattr('runtime.material_consolidation.request_semantics', model)
    result = review_structure(doc, config(), archive, lambda: None, lambda *a: None, base_structure=doc)
    assert len(result['concepts']) == 2


def test_same_names_are_compared_even_if_catalog_model_misses_them():
    from runtime.material_consolidation import _candidate_components, _same_name_candidates
    catalog = [
        {'h': 0, 'label': '停止條件', 'aliases': ['Stop Rule']},
        {'h': 1, 'label': '不同研究的停止政策', 'aliases': []},
        {'h': 2, 'label': '停止條件', 'aliases': []},
        {'h': 3, 'label': 'Other title', 'aliases': ['stop rule']},
    ]
    assert _candidate_components(4, _same_name_candidates(catalog)) == [[0, 2, 3], [1]]


def test_replaying_multiple_rejections_keeps_a_fresh_repair_opportunity(monkeypatch):
    class ReplayArchive(Archive):
        def load_review(self, key, *, validate_response=None):
            saved = super().load_review(key)
            if saved is not None:
                return saved
            for name, value in list(self.values.items()):
                if name.endswith('/request') and value['cache_key'] == key:
                    response = self.values.get(name.removesuffix('/request') + '/response')
                    if response is not None:
                        try:
                            validate_response(response)
                        except ValueError:
                            continue
                        return response
    doc = document(['任務失敗時停止。'], ['停止條件'])
    archive = ReplayArchive()
    original_drafts = 0
    def first(_client, **kw):
        nonlocal original_drafts
        request = kw['request']
        if request['operation'] == 'consolidation_check':
            response = approved(request)
            response['checks'][0].update(duplicates_removed=False, reason='合成的語意拒絕。')
            return response
        original_drafts += 1
        if original_drafts == 3:
            raise SemanticServiceError('SEMANTIC_PROVIDER_UNAVAILABLE')
        response = keep(request)
        if request.get('correction'):
            response['concepts'][0]['claims'][0]['claims'] = [99]
        return response
    monkeypatch.setattr('runtime.material_consolidation.request_semantics', first)
    with pytest.raises(SemanticServiceError, match='SEMANTIC_PROVIDER_UNAVAILABLE'):
        consolidate_structure(doc, config(), archive, lambda: None, lambda *a: None)
    operations = []
    def fixed(_client, **kw):
        request = kw['request']
        operations.append(request['operation'])
        if request['operation'] == 'consolidation_rewrite':
            assert request['correction']['details']['unexpected_handles'] == [99]
            response = keep(request)
            response['concepts'][0]['claims'][0]['text'] = '若任務失敗，應停止。'
            return response
        return approved(request)
    monkeypatch.setattr('runtime.material_consolidation.request_semantics', fixed)
    consolidate_structure(doc, config(), archive, lambda: None, lambda *a: None)
    assert operations == ['consolidation_rewrite', 'consolidation_check']
    assert not archive.values['consolidation/result']['blocked_changes']


def test_semantic_then_literal_rejection_can_both_be_repaired(monkeypatch):
    doc = document(['任務最多重試 8 次。任務最多重試 8 次。'], ['重試上限'])
    archive = Archive()
    drafts = []
    def model(_client, **kw):
        request = kw['request']
        if request['operation'] == 'consolidation_check':
            response = approved(request)
            if len(drafts) == 1:
                response['checks'][0].update(duplicates_removed=False, reason='同一句仍出現兩次。')
            return response
        drafts.append(request)
        response = keep(request)
        if len(drafts) == 2:
            response['concepts'][0]['claims'][0]['text'] = '任務可重試。'
        elif len(drafts) == 3:
            assert request['correction']['details']['claims'][0]['missing_literals'] == ['8']
            response['concepts'][0]['claims'][0]['text'] = '任務最多重試 8 次。'
        return response
    monkeypatch.setattr('runtime.material_consolidation.request_semantics', model)
    result = consolidate_structure(doc, config(), archive, lambda: None, lambda *a: None)
    assert len(drafts) == 3
    assert result['concepts'][0]['claims'][0]['text'] == '任務最多重試 8 次。'
    assert not archive.values['consolidation/result']['blocked_changes']


def test_failed_large_batch_retries_complete_candidate_components(monkeypatch):
    doc = document(labels=['重試上限', '最多重試次數', '不同實驗'])
    archive = Archive()
    batches = []
    def model(_client, **kw):
        request = kw['request']
        if request['operation'] == 'consolidation_candidates':
            return {'groups': [{'concepts': [0, 1], 'reason': '同一上限。'}]}
        if request['operation'] == 'consolidation_check':
            return approved(request)
        handles = [c['h'] for c in request['concepts']]
        batches.append(handles)
        response = keep(request)
        if len(handles) == 3:
            response['concepts'][0]['claims'] = []
        elif handles == [0, 1]:
            response['concepts'][0].update(concepts=[0, 1], claims=[
                {'claims': [0, 1], 'text': request['claims'][0]['text']}])
            del response['concepts'][1]
        return response
    monkeypatch.setattr('runtime.material_consolidation.request_semantics', model)
    result = consolidate_structure(doc, config(), archive, lambda: None, lambda *a: None)
    assert [0, 1] in batches and [2] in batches
    assert [0] not in batches and [1] not in batches
    assert len(result['concepts']) == 2 and validate_knowledge_structure(result)
    assert archive.values['consolidation/result']['split_batches']
    assert not archive.values['consolidation/result']['blocked_changes']


def test_equivalence_check_only_applies_when_multiple_nodes_were_merged(monkeypatch):
    doc = document(['任務失敗就停止。'], ['停止條件'])
    archive = Archive()
    operations = []
    def model(_client, **kw):
        request = kw['request']
        operations.append(request['operation'])
        if request['operation'] == 'consolidation_rewrite':
            return keep(request)
        response = approved(request)
        response['checks'][0].update(equivalent_concepts=False, reason='本概念與其他概念不同，應獨立保留。')
        return response
    monkeypatch.setattr('runtime.material_consolidation.request_semantics', model)
    result = consolidate_structure(doc, config(), archive, lambda: None, lambda *a: None)
    assert result['concepts'] == doc['concepts']
    assert operations == ['consolidation_rewrite', 'consolidation_check']
    assert not archive.values['consolidation/result']['blocked_changes']
