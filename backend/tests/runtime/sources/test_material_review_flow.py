"""以受控模型驗證 worker 檢核、發布、重試及版本保留。"""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from knowledge_map.structure import validate_knowledge_structure
from runtime.source_revisions import SourceError, create_revision, retry_revision
from runtime.storage.knowledge_structures import read_knowledge_structure
from product_fixtures import closed_loop
from backend.tests.runtime.sources.test_source_revisions import revisions


def enable_review(settings, *, consolidation=False):
    path = Path(__file__).parents[4] / "local_ai/runtime-lock.json"
    lock = json.loads(path.read_text())
    settings["runtime_lock"]["material_review"] = lock["material_review"]
    if not consolidation:
        settings['runtime_lock']['material_review'].pop('consolidation_policy', None)


def test_consolidation_review_only_preserves_source_locations_and_old_revision(revisions, monkeypatch):
    from runtime.storage.knowledge_reads import resolve_evidence_source
    from test_material_consolidation import approved
    owner, material, settings, dsn, add, start, execute, first, original, requests = revisions
    second = add('Same-definition.pdf', original['concepts'][0]['claims'][0]['text'])
    start([second], 'append-duplicate', original['revision'])
    prior = execute()
    old = read_knowledge_structure(owner.learner_id, material, run_id=prior.run_id, dsn=dsn).document
    assert len(old['concepts']) == 2
    enable_review(settings, consolidation=True)
    monkeypatch.setattr('runtime.material_review.request_semantics', lambda _client, **kw: keep_response(kw['request']))
    operations = []
    def model(_client, **kw):
        request = kw['request']
        operations.append(request['operation'])
        if request['operation'] == 'consolidation_candidates':
            return {'groups': [{'concepts': [0, 1], 'reason': '兩個來源為相同定義。'}]}
        if request['operation'] == 'consolidation_rewrite':
            return {'concepts': [{'concepts': [0, 1], 'title_from': 0,
                'claims': [{'claims': [0, 1], 'text': request['claims'][0]['text']}], 'reason': '相同定義只呈現一次。'}]}
        return approved(request)
    monkeypatch.setattr('runtime.material_consolidation.request_semantics', model)
    monkeypatch.setattr('runtime.material_processing.analyze_material', lambda *a, **kw: pytest.fail('analysis rerun'))
    monkeypatch.setattr('runtime.material_processing.runtime_preflight', lambda *a, **kw: pytest.fail('OCR/POD preflight'))
    before_requests = len(requests)
    new = create_revision(owner.learner_id, material, [], 'consolidate', settings, base_revision=old['revision'], dsn=dsn)
    completed = execute()
    assert completed.status == 'succeeded', completed.error_code
    doc = read_knowledge_structure(owner.learner_id, material, run_id=new.run_id, dsn=dsn).document
    assert validate_knowledge_structure(doc) and len(doc['concepts']) == 1
    assert doc['evidence'] == old['evidence'] and doc['input_binding']['manifest'] == old['input_binding']['manifest']
    assert len(requests) == before_requests
    assert doc['metrics']['ocr_calls'] == doc['metrics']['evidence_duration_ms'] == 0
    assert doc['metrics']['semantic_calls'] == 2 + len(operations)
    locations = [resolve_evidence_source(owner.learner_id, material, doc['revision'], ref, dsn=dsn)
                 for ref in doc['concepts'][0]['evidence_refs']]
    assert [(r['original_name'], r['normalized_page']) for r in locations] == [('A.pdf', 1), ('Same-definition.pdf', 1)]
    assert read_knowledge_structure(owner.learner_id, material, revision=old['revision'], dsn=dsn).document == old


def test_failed_consolidation_reuses_rejected_draft_only_as_repair_input(revisions, monkeypatch):
    from runtime.semantic_service import SemanticServiceError
    from test_material_consolidation import approved, keep
    owner, material, settings, dsn, add, start, execute, first, original, requests = revisions
    enable_review(settings, consolidation=True)
    monkeypatch.setattr('runtime.material_review.request_semantics', lambda _client, **kw: keep_response(kw['request']))
    def fail(_client, **kw):
        request = kw['request']
        assert request['operation'] == 'consolidation_rewrite'
        if request.get('correction'):
            raise SemanticServiceError('SEMANTIC_SERVICE_UNAVAILABLE')
        result = keep(request)
        result['concepts'][0]['claims'][0]['claims'] = [99]
        return result
    monkeypatch.setattr('runtime.material_consolidation.request_semantics', fail)
    start([], 'failed-consolidation', original['revision'])
    # 暫時性失敗先自動接續一次，第二次失敗才停下。
    assert execute().status == 'pending'
    failed = execute()
    assert failed.status == 'failed' and failed.error_code == 'SEMANTIC_SERVICE_UNAVAILABLE'
    operations = []
    def repaired(_client, **kw):
        request = kw['request']
        operations.append(request['operation'])
        if request['operation'] == 'consolidation_rewrite':
            assert request['correction']['details']['unexpected_handles'] == [99]
            assert request['correction']['details']['concepts'] == [0]
            return keep(request)
        return approved(request)
    monkeypatch.setattr('runtime.material_consolidation.request_semantics', repaired)
    monkeypatch.setattr('runtime.material_review.request_semantics', lambda *a, **kw: pytest.fail('regular review replayed'))
    retry_revision(owner.learner_id, failed.run_id, 'repair-consolidation', settings, dsn=dsn)
    completed = execute()
    assert completed.status == 'succeeded', completed.error_code
    assert operations == ['consolidation_rewrite', 'consolidation_check']
    updated = read_knowledge_structure(owner.learner_id, material, run_id=completed.run_id, dsn=dsn).document
    assert updated['concepts'] == original['concepts'] and updated['evidence'] == original['evidence']


def keep_response(request):
    return {
        "assignments": {
            str(concept["h"]): {
                "concept_source": concept["evidence"][0],
                "decision": {
                "action": "keep",
                "target": None,
                "issue": "none",
                "reason": "保留來源中的獨立觀念。",
                "evidence": concept["evidence"][1:],
                },
            }
            for concept in request["concepts"]
        },
        "alias_edits": [],
        "claim_edits": [],
        "relation_edits": [],
    }


def test_incremental_review_only_sends_changed_concepts_and_keeps_old_sources(revisions, monkeypatch):
    from knowledge_map.source_identity import unchanged_claims
    owner, material, settings, dsn, add, start, execute, first, original, requests = revisions
    enable_review(settings)
    calls = []
    def model(client, **kw):
        calls.append(kw['request'])
        return keep_response(kw['request'])
    monkeypatch.setattr('runtime.material_review.request_semantics', model)
    addition = add('new.pdf', 'A queue removes the earliest inserted value first.')
    start([addition], 'incremental-review', original['revision'])
    completed = execute()
    assert completed.status in ('succeeded', 'partial')
    updated = read_knowledge_structure(owner.learner_id, material,
                                     revision=completed.output_binding['knowledge_structure_revision'], dsn=dsn).document
    assert calls
    reviewed = [claim['text'] for call in calls for claim in call['claims']]
    original_texts = {claim['text'] for concept in original['concepts'] for claim in concept['claims']}
    assert not original_texts.intersection(reviewed)
    assert any('queue' in text for text in reviewed)
    assert len(unchanged_claims(original, updated)) == sum(len(c['claims']) for c in original['concepts'])


def test_review_publishes_new_revision_without_ocr_or_analysis_and_preserves_old(revisions, monkeypatch):
    owner, material, settings, dsn, add, start, execute, first, original, requests = revisions
    enable_review(settings)
    calls = []
    def model(client, **kw):
        assert kw['task'] == 'material_review'
        calls.append(kw['request'])
        return keep_response(kw['request'])
    monkeypatch.setattr('runtime.material_review.request_semantics', model)
    monkeypatch.setattr('runtime.material_processing.runtime_preflight', lambda *_: pytest.fail('OCR/POD preflight'))
    monkeypatch.setattr('runtime.material_processing.analyze_material', lambda *a, **kw: pytest.fail('analysis rerun'))
    new = create_revision(owner.learner_id, material, [], 'review', settings, base_revision=original['revision'], dsn=dsn)
    completed = execute()
    assert completed.status == 'succeeded', completed.error_code
    doc = read_knowledge_structure(owner.learner_id, material, run_id=new.run_id, dsn=dsn).document
    assert validate_knowledge_structure(doc) and doc['revision'] != original['revision']
    assert doc['evidence'] == original['evidence'] and doc['concepts'] == original['concepts']
    assert doc['metrics']['ocr_calls'] == 0 and doc['metrics']['semantic_calls'] == len(calls) == 1
    assert read_knowledge_structure(owner.learner_id, material, revision=original['revision'], dsn=dsn).document == original
    assert create_revision(owner.learner_id, material, [], 'review', settings, base_revision=original['revision'], dsn=dsn).run_id == new.run_id
    with pytest.raises(SourceError, match='REVISION_CONFLICT'):
        create_revision(owner.learner_id, material, [], 'stale', settings, base_revision=original['revision'], dsn=dsn)


@pytest.mark.parametrize('raise_budget', [False, True])
def test_new_analysis_runs_review_and_failed_review_keeps_head(revisions, monkeypatch, raise_budget):
    owner, material, settings, dsn, add, start, execute, first, original, requests = revisions
    enable_review(settings)
    settings["runtime_lock"]["material_semantics"]["max_tokens"] = 8192
    settings["runtime_lock"]["material_review"]["max_tokens"] = 8192
    calls = []
    def model(client, **kw):
        calls.append(kw['request'])
        return keep_response(kw['request']) if len(calls) == 1 else {'bad': True}
    monkeypatch.setattr('runtime.material_review.request_semantics', model)
    second = add('B.pdf', 'A queue removes the first inserted element first.')
    third = add('C.pdf', 'An array stores values at indexed positions.')
    start([second, third], 'append-review', original['revision'])
    failed = execute()
    assert failed.status == 'failed' and failed.error_code == 'REVIEW_RESPONSE_INVALID'
    assert len(calls) == 3 and 'review_correction' in calls[2]
    assert failed.progress_stage == 'review'
    assert failed.completed_units == 1 and failed.total_units == 2
    assert read_knowledge_structure(owner.learner_id, material, revision=original['revision'], dsn=dsn).document == original
    before = len(requests)
    def corrected(_client, **kwargs):
        request = kwargs["request"]
        calls.append(request)
        return keep_response(request)

    monkeypatch.setattr('runtime.material_review.request_semantics', corrected)
    if raise_budget:
        settings['runtime_lock']['material_semantics']['max_tokens'] = 32768
        settings['runtime_lock']['material_review']['max_tokens'] = 32768
    retry_revision(owner.learner_id, failed.run_id, 'retry-review', settings, dsn=dsn)
    completed = execute()
    assert completed.status == 'succeeded', completed.error_code
    assert len(calls) == (5 if raise_budget else 4) and len(requests) == before
    doc = read_knowledge_structure(owner.learner_id, material, run_id=completed.run_id, dsn=dsn).document
    assert doc['page_count'] == 3 and validate_knowledge_structure(doc)


def test_explicit_review_after_cancellation_reuses_saved_responses(revisions, monkeypatch):
    from runtime.material_processing import request_material_processing_cancellation
    from runtime.storage.analysis_archive import AnalysisArchive
    owner, material, settings, dsn, add, start, execute, first, original, requests = revisions
    enable_review(settings)
    run = create_revision(owner.learner_id, material, [], 'cancel-review', settings, base_revision=original['revision'], dsn=dsn)
    save_review = AnalysisArchive.save_review
    def save_then_cancel(archive, name, data):
        save_review(archive, name, data)
        if archive.run.run_id == run.run_id and name.endswith('/response'):
            # 已落盤才取消；取消後才回來的模型結果不得寫入或被重用。
            request_material_processing_cancellation(owner.learner_id, run.run_id, update_only=True, dsn=dsn)
    monkeypatch.setattr(AnalysisArchive, 'save_review', save_then_cancel)
    monkeypatch.setattr('runtime.material_review.request_semantics', lambda client, **kw: keep_response(kw['request']))
    assert execute().status == 'cancelled'
    monkeypatch.setattr('runtime.material_review.request_semantics', lambda *a, **kw: pytest.fail('model replay'))
    create_revision(owner.learner_id, material, [], 'resume-reviewed', settings, base_revision=original['revision'], dsn=dsn)
    completed = execute()
    assert completed.status == 'succeeded', completed.error_code
    assert read_knowledge_structure(owner.learner_id, material, revision=original['revision'], dsn=dsn).document == original


@pytest.mark.parametrize('error_code', ['REVIEW_CONCEPT_COVERAGE_INVALID', 'REVIEW_ALIAS_EDIT_INVALID',
                                      'REVIEW_METADATA_REASON_INVALID'])
def test_contract_failure_retry_repairs_saved_response_without_analysis(revisions, monkeypatch, error_code):
    owner, material, settings, dsn, add, start, execute, first, original, requests = revisions
    enable_review(settings)
    calls = []

    def model(client, **kw):
        request = kw['request']
        calls.append(request)
        response = keep_response(request)
        if len(calls) <= 2:
            if error_code == 'REVIEW_CONCEPT_COVERAGE_INVALID':
                response['assignments'].pop('0')
            elif error_code == 'REVIEW_METADATA_REASON_INVALID':
                response['assignments']['0']['decision'].update(action='metadata', issue='none')
            else:
                concept = request['concepts'][0]
                response['alias_edits'] = [{'concept': concept['h'], 'remove': ['synthetic-absent-alias'],
                    'concept_source': concept['evidence'][0], 'evidence': [], 'reason': '合成不合法別名'}]
        return response

    monkeypatch.setattr('runtime.material_review.request_semantics', model)
    monkeypatch.setattr('runtime.material_processing.analyze_material', lambda *a, **kw: pytest.fail('analysis replay'))
    create_revision(owner.learner_id, material, [], 'coverage-review', settings,
                    base_revision=original['revision'], dsn=dsn)
    failed = execute()
    assert failed.status == 'failed' and failed.error_code == error_code
    assert len(calls) == 2 and 'review_correction' in calls[1]
    assert read_knowledge_structure(owner.learner_id, material, revision=original['revision'], dsn=dsn).document == original
    retry_revision(owner.learner_id, failed.run_id, 'repair-coverage', settings, dsn=dsn)
    completed = execute()
    assert completed.status == 'succeeded', completed.error_code
    assert len(calls) == 3 and 'review_correction' in calls[2]
    doc = read_knowledge_structure(owner.learner_id, material, run_id=completed.run_id, dsn=dsn).document
    assert validate_knowledge_structure(doc) and doc['evidence'] == original['evidence']


def test_retry_corrects_latest_repair_instead_of_repeating_original_error(revisions, monkeypatch):
    owner, material, settings, dsn, add, start, execute, first, original, requests = revisions
    enable_review(settings)
    calls = []
    def model(client, **kwargs):
        request = kwargs['request']
        calls.append(request)
        response = keep_response(request)
        if len(calls) == 1:
            response['assignments'].pop('0')
        elif len(calls) == 2:
            # 原稿的缺漏已修正，但補正稿另有不合法別名，接續應指出這個最新問題。
            concept = request['concepts'][0]
            response['alias_edits'] = [{'concept': 0, 'remove': ['synthetic-absent-alias'],
                'concept_source': concept['evidence'][0], 'evidence': [], 'reason': '合成錯誤別名'}]
        else:
            assert request['review_correction']['error'] == 'REVIEW_ALIAS_EDIT_INVALID'
            assert request['review_correction']['missing_concepts'] == []
        return response
    monkeypatch.setattr('runtime.material_review.request_semantics', model)
    create_revision(owner.learner_id, material, [], 'latest-repair-review', settings,
                    base_revision=original['revision'], dsn=dsn)
    failed = execute()
    assert failed.status == 'failed' and failed.error_code == 'REVIEW_ALIAS_EDIT_INVALID'
    assert calls[1]['review_correction']['error'] == 'REVIEW_CONCEPT_COVERAGE_INVALID'
    retry_revision(owner.learner_id, failed.run_id, 'latest-repair-resume', settings, dsn=dsn)
    completed = execute()
    assert completed.status == 'succeeded', completed.error_code
    assert len(calls) == 3


@pytest.mark.parametrize('single_fits', [True, False])
def test_review_capacity_splits_concepts_without_truncating_sources(revisions, monkeypatch, single_fits):
    from runtime import material_review as review
    from runtime.semantic_service import SemanticServiceError
    from runtime.storage.analysis_archive import _material_directory
    owner, material, settings, dsn, add, start, execute, first, original, requests = revisions
    enable_review(settings)
    second = add('B.pdf', 'A queue removes the first inserted element first.')
    original_inputs = review.review_inputs
    def inputs(document, **_scope):
        view, units = original_inputs(document)
        evidence = list({e['evidence_id']: e for unit in units for e in unit.evidence}.values())
        return view, [review._pack_review(view, view['concepts'], evidence, 'Synthetic full context')]
    monkeypatch.setattr(review, 'review_inputs', inputs)
    all_requests, model_calls, observed = [], [], []
    from runtime import material_processing as processing
    original_progress = processing._record_progress
    def progress(run_id, stage, done, total, **kwargs):
        original_progress(run_id, stage, done, total, **kwargs)
        stored = processing.read_material_processing_run(owner.learner_id, run_id, dsn=dsn)
        observed.append((stored.progress_stage, stored.completed_units, stored.total_units))
    monkeypatch.setattr(processing, '_record_progress', progress)
    def model(client, **kwargs):
        request = kwargs['request']
        all_requests.append(deepcopy(request))
        if len(request['concepts']) > 1 or not single_fits:
            raise SemanticServiceError('SEMANTIC_INPUT_TOO_LARGE')
        model_calls.append(request)
        return keep_response(request)
    monkeypatch.setattr(review, 'request_semantics', model)
    start([second], 'capacity-review', original['revision'])
    result = execute()
    assert len(all_requests[0]['concepts']) > 1
    if single_fits:
        # 容量分段沿用完整引用頁，無須把其他來源整批重送；所有原文仍須完整保留。
        def original_block(e):
            return e['source_name'], e['page'], e['kind'], e['text']
        assert {original_block(e) for r in model_calls for e in r['evidence']} == {
            original_block(e) for e in all_requests[0]['evidence']}
        assert result.status == 'succeeded', result.error_code
        doc = read_knowledge_structure(owner.learner_id, material, run_id=result.run_id, dsn=dsn).document
        assert validate_knowledge_structure(doc)
        assert len(model_calls) == len(all_requests[0]['concepts'])
        receipt = json.loads((_material_directory(owner.learner_id, material) / result.run_id.hex / 'review/result.json').read_text())['data']
        assert receipt['model_calls'] == receipt['units'] == len(model_calls)
        review_progress = [row[1:] for row in observed if row[0] == 'review']
        assert review_progress[0] == (0, 1)
        assert review_progress[-1] == (len(model_calls), len(model_calls))
        assert any(done == 0 and total > 1 for done, total in review_progress)
        assert observed[-1] == ('publishing', None, None)
        source = json.loads((_material_directory(owner.learner_id, material) / result.run_id.hex / 'review/source.json').read_text())['data']
        assert doc['concepts'] == source['concepts'] and doc['evidence'] == source['evidence']
    else:
        assert result.status == 'failed' and result.error_code == 'SEMANTIC_INPUT_TOO_LARGE'
        assert any(len(r['evidence']) < len(all_requests[0]['evidence']) for r in all_requests)
        original_blocks = {(e['source_name'], e['page'], e['text']) for e in all_requests[0]['evidence']}
        assert all({(e['source_name'], e['page'], e['text']) for e in r['evidence']} <= original_blocks for r in all_requests)
        assert not model_calls
        assert read_knowledge_structure(owner.learner_id, material, revision=original['revision'], dsn=dsn).document == original


def test_parallel_database_archive_keeps_successes_across_failed_batch(revisions, monkeypatch):
    from threading import Barrier, Lock
    from collections import Counter
    from runtime import material_review as review
    from runtime.luna_test import test_lock
    from runtime.storage.analysis_archive import _material_directory
    from learning_adaptation.study_sessions import create_study_session
    owner, material, settings, dsn, add, start, execute, first, original, requests = revisions
    create_study_session(owner, material, original['revision'], 'preserve-parallel-base', dsn=dsn)
    enable_review(settings)
    monkeypatch.setenv('STUDYDY_LUNA_PARALLEL_TEST', '1')
    actual = review._review_units
    monkeypatch.setattr(review, '_review_units', lambda view, units, lock, *a, **k: actual(view, units, test_lock(lock), *a, **k))
    b = add('B.pdf', 'A queue removes the first inserted element first.')
    c = add('C.pdf', 'A tree has a root and child nodes.')
    gate, mutex = Barrier(2), Lock()
    calls, active, peak = Counter(), 0, 0
    def model(client, **kw):
        nonlocal active, peak
        request = kw['request']
        with mutex:
            calls[request['title']] += 1
            number = sum(calls.values())
            active += 1
            peak = max(peak, active)
        try:
            if number <= 2: gate.wait(5)
            result = keep_response(request)
            if request['title'].startswith('B.pdf'):
                result['assignments']['0']['decision'].update(action='metadata', issue='none')
            return result
        finally:
            with mutex: active -= 1
    monkeypatch.setattr(review, 'request_semantics', model)
    run = start([b, c], 'parallel-review', original['revision'])
    failed = execute()
    assert failed.status == 'failed' and failed.error_code == 'REVIEW_METADATA_REASON_INVALID'
    assert peak == 2
    directory = _material_directory(owner.learner_id, material) / run.run_id.hex / 'review'
    cached = list(directory.glob('cache-*.json'))
    assert cached
    saved = {p.name: p.read_bytes() for p in cached}
    old_calls = calls.copy()
    def corrected(client, **kw):
        calls[kw['request']['title']] += 1
        return keep_response(kw['request'])
    monkeypatch.setattr(review, 'request_semantics', corrected)
    analysis_calls = len(requests)
    retry_revision(owner.learner_id, failed.run_id, 'parallel-review-resume', settings, dsn=dsn)
    completed = execute()
    assert completed.status == 'succeeded', completed.error_code
    assert len(requests) == analysis_calls
    assert all((directory / name).read_bytes() == content for name, content in saved.items())
    for title, count in old_calls.items():
        assert calls[title] == count + (1 if title.startswith('B.pdf') else 0)
    assert read_knowledge_structure(owner.learner_id, material, revision=original['revision'], dsn=dsn).document == original
