"""正式協調者的有界並行、分段與取消；不呼叫真實模型。"""
from threading import Barrier, Event, Lock
from copy import deepcopy
import pytest

from runtime.material_review import _review_units
from runtime.semantic_service import SemanticServiceError
from knowledge_map.material_review import combine_reviews, project_review
from test_review_parallel_feasibility import inputs, Archive
from test_material_review import wire_proposal


def response(request):
    return {'assignments': [{'concept': c['h'], 'action': 'keep', 'target': None,
        'issue': 'none', 'reason': '來源支持', 'evidence': c['evidence']} for c in request['concepts']],
        'alias_edits': [], 'claim_edits': [], 'relation_edits': []}


def config(luna=True):
    return {'semantic_service': {'api_protocol': 'codex-exec-luna/v1' if luna else 'openai/v1'},
            'material_review': {'prompt': 'synthetic'}}


@pytest.mark.parametrize('flag,luna,width', [('0', True, 1), ('1', False, 1), ('1', True, 2)])
def test_real_coordinator_parallelism_is_opt_in_and_luna_only(tmp_path, monkeypatch, flag, luna, width):
    monkeypatch.setenv('STUDYDY_LUNA_PARALLEL_TEST', flag)
    view, units = inputs(4)
    before = deepcopy(view)
    gate, mutex = Barrier(width), Lock()
    active = peak = 0
    def model(client, **kwargs):
        nonlocal active, peak
        with mutex:
            active += 1
            peak = max(peak, active)
        try:
            gate.wait(5)
            return wire_proposal(response(kwargs['request']))
        finally:
            with mutex: active -= 1
    monkeypatch.setattr('runtime.material_review.request_semantics', model)
    archive = Archive(tmp_path / 'archive')
    progress = []
    reviews, calls = _review_units(view, units, config(luna), archive, lambda: None, lambda *p: progress.append(p))
    assert peak == width and calls == 4
    assert [u.payload['title'] for u, _ in reviews] == [u.payload['title'] for u in units]
    merged, proposal = combine_reviews(view, reviews)
    project_review(view, merged, proposal)
    assert view == before and progress[-1] == ('review', 4, 4)
    monkeypatch.setattr('runtime.material_review.request_semantics', lambda *a, **k: pytest.fail('cache replayed'))
    assert _review_units(view, units, config(luna), archive, lambda: None, lambda *p: None)[1] == 0


def test_parallel_capacity_splits_keep_full_evidence_and_unique_calls(tmp_path, monkeypatch):
    monkeypatch.setenv('STUDYDY_LUNA_PARALLEL_TEST', '1')
    view, units = inputs(2)
    def model(client, **kwargs):
        request = kwargs['request']
        assert len(request['evidence']) == 5
        if len(request['concepts']) > 2:
            raise SemanticServiceError('SEMANTIC_INPUT_TOO_LARGE')
        return wire_proposal(response(request))
    monkeypatch.setattr('runtime.material_review.request_semantics', model)
    archive = Archive(tmp_path / 'archive')
    progress = []
    reviews, calls = _review_units(view, units, config(), archive, lambda: None, lambda *p: progress.append(p))
    assert calls == 6 and len(reviews) == 6
    assert len(set(archive.requests)) == len(archive.requests)
    assert progress[-1] == ('review', 6, 6)
    merged, proposal = combine_reviews(view, reviews)
    assert len(merged.concepts) == 10
    project_review(view, merged, proposal)


def test_parallel_cancel_stops_both_requests_before_more_dispatch(tmp_path, monkeypatch):
    monkeypatch.setenv('STUDYDY_LUNA_PARALLEL_TEST', '1')
    view, units = inputs(4)
    gate = Barrier(2)
    cancelled = Event()
    def check():
        if cancelled.is_set(): raise RuntimeError('synthetic cancellation')
    def model(client, **kwargs):
        gate.wait(5)
        cancelled.set()
        kwargs['cancellation_check']()
        pytest.fail('cancelled model returned')
    monkeypatch.setattr('runtime.material_review.request_semantics', model)
    archive = Archive(tmp_path / 'archive')
    with pytest.raises(RuntimeError, match='synthetic cancellation'):
        _review_units(view, units, config(), archive, check, lambda *p: None)
    assert len(archive.requests) == 2
    assert not list(archive.root.glob('cache-*'))


def test_capacity_split_scopes_complete_pages_and_keeps_approved_scope(tmp_path, monkeypatch):
    from knowledge_map.material_review import _pack_review
    view, original = inputs(4)
    evidence = [e for unit in original for e in unit.evidence]
    # 模擬來源批次亂序完成，拆批仍按頁面保留連續範圍。
    concepts = view['concepts'][10:] + view['concepts'][:10]
    unit = _pack_review(view, concepts, evidence, 'Four pages')
    unit.payload.update(learning_scope={'goals': ['保留明確限制'], 'topics': ['條件判斷']}, learning_scope_policy='依已確認範圍')
    seen = []
    def model(client, **kwargs):
        request = kwargs['request']; seen.append(deepcopy(request))
        if len(request['evidence']) > 10:
            raise SemanticServiceError('SEMANTIC_INPUT_TOO_LARGE')
        assert request['learning_scope'] == unit.payload['learning_scope']
        assert request['learning_scope_policy'] == unit.payload['learning_scope_policy']
        return wire_proposal(response(request))
    monkeypatch.setattr('runtime.material_review.request_semantics', model)
    archive = Archive(tmp_path / 'archive')
    before = deepcopy(unit)
    reviews, calls = _review_units(view, [unit], config(), archive, lambda: None, lambda *p: None)
    assert calls == 2 and len(seen) == 3 and unit == before
    assert [{e['page'] for e in r['evidence']} for r in seen[1:]] == [{1, 2}, {3, 4}]
    assert {e['evidence_id'] for u, _ in reviews for e in u.evidence} == {e['evidence_id'] for e in evidence}
    merged, proposal = combine_reviews(view, reviews)
    assert len(merged.concepts) == 20
    project_review(view, merged, proposal)


def test_oversized_single_concept_keeps_complete_cited_pages_and_cached_resume(tmp_path, monkeypatch):
    from knowledge_map.material_review import _pack_review
    from content_identity import canonical_sha256
    view, original = inputs(2)
    # 同一概念跨兩頁引用；同頁其他段落也必須保留。
    view['concepts'][0]['claims'][0]['evidence'].append(deepcopy(view['concepts'][5]['claims'][0]['evidence'][0]))
    evidence = [deepcopy(e) for u in original for e in u.evidence]
    unrelated = deepcopy(evidence[0]); unrelated.update(evidence_id='unrelated-page', page=3, normalized_page=3)
    foreign = deepcopy(evidence[0]); foreign.update(evidence_id='foreign-source', source_id='another-source')
    evidence += [unrelated, foreign]
    unit = _pack_review(view, view['concepts'][:1], evidence, 'Synthetic oversized context')
    seen = []
    def model(client, **kwargs):
        request = kwargs['request']; seen.append(deepcopy(request))
        if len(request['evidence']) > 10:
            raise SemanticServiceError('SEMANTIC_INPUT_TOO_LARGE')
        return wire_proposal(response(request))
    monkeypatch.setattr('runtime.material_review.request_semantics', model)
    archive = Archive(tmp_path / 'archive')
    before = deepcopy(unit)
    reviews, calls = _review_units(view, [unit], config(), archive, lambda: None, lambda *p: None)
    assert calls == 1 and len(seen) == 2 and unit == before
    scoped = reviews[0][0]
    assert scoped.concepts == unit.concepts and scoped.claims == unit.claims
    assert scoped.evidence == evidence[:10]
    assert len(scoped.payload['concepts']) == 1 and len(scoped.payload['claims']) == 1
    split = __import__('json').loads((archive.root / 'call-000001/split.json').read_text())
    assert split['source_scope'] == 'cited-pages/v1'
    # 接續沿原分段找到新範圍快取，不重跑已完成的模型請求。
    def only_capacity(client, **kwargs):
        assert {k: v for k, v in kwargs['request'].items() if k != 'assignment_format'} == unit.payload
        raise SemanticServiceError('SEMANTIC_INPUT_TOO_LARGE')
    monkeypatch.setattr('runtime.material_review.request_semantics', only_capacity)
    assert _review_units(view, [unit], config(), archive, lambda: None, lambda *p: None)[1] == 0
    # 原範圍若已有成功結果，優先重用，不因新分批策略作廢它。
    key = canonical_sha256({'source': canonical_sha256(unit.evidence), 'request': unit.payload, 'policy': config()['material_review']})
    archive.save_review(f'cache-{key}', response(unit.payload))
    monkeypatch.setattr('runtime.material_review.request_semantics', lambda *a, **k: pytest.fail('valid old response replayed'))
    restored, count = _review_units(view, [unit], config(), archive, lambda: None, lambda *p: None)
    assert count == 0 and restored[0][0].payload == unit.payload


def test_single_concept_that_still_exceeds_capacity_stops_without_truncation(tmp_path, monkeypatch):
    from knowledge_map.material_review import _pack_review
    view, original = inputs(1)
    unit = _pack_review(view, view['concepts'][:1], original[0].evidence, 'One complete page')
    calls = []
    def model(client, **kwargs):
        calls.append(kwargs['request'])
        raise SemanticServiceError('SEMANTIC_INPUT_TOO_LARGE')
    monkeypatch.setattr('runtime.material_review.request_semantics', model)
    archive = Archive(tmp_path / 'archive')
    with pytest.raises(SemanticServiceError, match='SEMANTIC_INPUT_TOO_LARGE'):
        _review_units(view, [unit], config(), archive, lambda: None, lambda *p: None)
    assert [{k: v for k, v in request.items() if k != 'assignment_format'} for request in calls] == [unit.payload]
    assert not list(archive.root.glob('cache-*'))


def test_review_cache_reuses_same_input_across_run_revisions_but_not_source_changes(tmp_path, monkeypatch):
    from knowledge_map.material_review import _pack_review
    view, units = inputs(1)
    calls = []
    def model(client, **kwargs):
        calls.append(deepcopy(kwargs['request']))
        return wire_proposal(response(kwargs['request']))
    monkeypatch.setattr('runtime.material_review.request_semantics', model)
    archive = Archive(tmp_path / 'archive')
    assert _review_units(view, units, config(), archive, lambda: None, lambda *p: None)[1] == 1
    retry_view = deepcopy(view)
    retry_view['knowledge_structure_revision'] = 'another-run-revision'
    unit = _pack_review(retry_view, retry_view['concepts'], units[0].evidence, units[0].payload['title'])
    assert unit.source_digest != units[0].source_digest
    assert _review_units(retry_view, [unit], config(), archive, lambda: None, lambda *p: None)[1] == 0
    changed = deepcopy(unit.evidence)
    changed[0]['quote'] += '新增限制。'
    unit = _pack_review(retry_view, retry_view['concepts'], changed, unit.payload['title'])
    assert _review_units(retry_view, [unit], config(), archive, lambda: None, lambda *p: None)[1] == 1
    assert len(calls) == 2
