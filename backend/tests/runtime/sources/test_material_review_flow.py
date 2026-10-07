"""以受控模型驗證 worker 檢核、發布、重試及版本保留。"""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from knowledge_map.structure import validate_knowledge_structure
from runtime.source_revisions import SourceError, create_revision, retry_revision
from runtime.storage.knowledge_structures import read_knowledge_structure
from product_fixtures import closed_loop
from test_source_revisions import revisions


def enable_review(settings):
    path = Path(__file__).parents[4] / "local_ai/runtime-lock.json"
    lock = json.loads(path.read_text())
    settings["runtime_lock"]["material_review"] = lock["material_review"]


def keep_response(request):
    return {
        "assignments": [
            {
                "concept": concept["h"],
                "action": "keep",
                "target": None,
                "issue": "none",
                "reason": "保留來源中的獨立觀念。",
                "evidence": concept["evidence"],
            }
            for concept in request["concepts"]
        ],
        "alias_edits": [],
        "claim_edits": [],
        "relation_edits": [],
    }


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
    start([second], 'append-review', original['revision'])
    failed = execute()
    assert failed.status == 'failed' and failed.error_code == 'REVIEW_RESPONSE_INVALID'
    assert len(calls) == 2
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
    assert len(calls) == (4 if raise_budget else 3) and len(requests) == before
    doc = read_knowledge_structure(owner.learner_id, material, run_id=completed.run_id, dsn=dsn).document
    assert doc['page_count'] == 2 and validate_knowledge_structure(doc)


def test_explicit_review_after_cancellation_reuses_saved_responses(revisions, monkeypatch):
    from runtime.material_processing import request_material_processing_cancellation
    owner, material, settings, dsn, add, start, execute, first, original, requests = revisions
    enable_review(settings)
    run = create_revision(owner.learner_id, material, [], 'cancel-review', settings, base_revision=original['revision'], dsn=dsn)
    def model(client, **kw):
        request_material_processing_cancellation(owner.learner_id, run.run_id, update_only=True, dsn=dsn)
        return keep_response(kw['request'])
    monkeypatch.setattr('runtime.material_review.request_semantics', model)
    assert execute().status == 'cancelled'
    monkeypatch.setattr('runtime.material_review.request_semantics', lambda *a, **kw: pytest.fail('model replay'))
    create_revision(owner.learner_id, material, [], 'resume-reviewed', settings, base_revision=original['revision'], dsn=dsn)
    completed = execute()
    assert completed.status == 'succeeded', completed.error_code
    assert read_knowledge_structure(owner.learner_id, material, revision=original['revision'], dsn=dsn).document == original


def test_coverage_failure_retry_repairs_saved_response_without_analysis(revisions, monkeypatch):
    owner, material, settings, dsn, add, start, execute, first, original, requests = revisions
    enable_review(settings)
    calls = []

    def model(client, **kw):
        request = kw['request']
        calls.append(request)
        response = keep_response(request)
        if len(calls) <= 2:
            response['assignments'].append(deepcopy(response['assignments'][0]))
        return response

    monkeypatch.setattr('runtime.material_review.request_semantics', model)
    monkeypatch.setattr('runtime.material_processing.analyze_material', lambda *a, **kw: pytest.fail('analysis replay'))
    create_revision(owner.learner_id, material, [], 'coverage-review', settings,
                    base_revision=original['revision'], dsn=dsn)
    failed = execute()
    assert failed.status == 'failed' and failed.error_code == 'REVIEW_CONCEPT_COVERAGE_INVALID'
    assert len(calls) == 2 and 'review_correction' in calls[1]
    assert read_knowledge_structure(owner.learner_id, material, revision=original['revision'], dsn=dsn).document == original
    retry_revision(owner.learner_id, failed.run_id, 'repair-coverage', settings, dsn=dsn)
    completed = execute()
    assert completed.status == 'succeeded', completed.error_code
    assert len(calls) == 3 and 'review_correction' in calls[2]
    doc = read_knowledge_structure(owner.learner_id, material, run_id=completed.run_id, dsn=dsn).document
    assert validate_knowledge_structure(doc) and doc['evidence'] == original['evidence']


@pytest.mark.parametrize('single_fits', [True, False])
def test_review_capacity_splits_concepts_without_truncating_sources(revisions, monkeypatch, single_fits):
    from runtime import material_review as review
    from runtime.semantic_service import SemanticServiceError
    from runtime.storage.analysis_archive import _material_directory
    owner, material, settings, dsn, add, start, execute, first, original, requests = revisions
    enable_review(settings)
    second = add('B.pdf', 'A queue removes the first inserted element first.')
    original_inputs = review.review_inputs
    def inputs(document):
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
    assert all(r['evidence'] == all_requests[0]['evidence'] for r in all_requests)
    if single_fits:
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
        assert not model_calls
        assert read_knowledge_structure(owner.learner_id, material, revision=original['revision'], dsn=dsn).document == original
