"""正式 worker 的檢核發布、重試與舊版本保存；模型僅用受控回應。"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from test_source_revisions import revisions
from test_closed_loop_v1 import closed_loop
from test_source_normalization import normalizer
from runtime.source_revisions import create_revision, retry_revision, SourceError
from runtime.storage.knowledge_structures import read_knowledge_structure
from runtime.material_processing import read_material_processing_run
from knowledge_map.structure import validate_knowledge_structure


def enable_review(settings):
    lock = json.loads((Path(__file__).parents[3]/'local_ai/runtime-lock.json').read_text())
    settings['runtime_lock']['material_review'] = lock['material_review']


def keep_response(request):
    return {'assignments': [{'concept': c['h'], 'action': 'keep', 'target': None, 'issue': 'none',
                            'reason': '保留來源中的獨立觀念。', 'evidence': c['evidence']} for c in request['concepts']],
            'alias_edits': [], 'claim_edits': [], 'relation_edits': []}


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


def test_new_analysis_runs_review_and_failed_review_keeps_head(revisions, monkeypatch):
    owner, material, settings, dsn, add, start, execute, first, original, requests = revisions
    enable_review(settings)
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
    assert read_knowledge_structure(owner.learner_id, material, revision=original['revision'], dsn=dsn).document == original
    before = len(requests)
    monkeypatch.setattr('runtime.material_review.request_semantics', lambda client, **kw: (calls.append(kw['request']) or keep_response(kw['request'])))
    retry_revision(owner.learner_id, failed.run_id, 'retry-review', settings, dsn=dsn)
    completed = execute()
    assert completed.status == 'succeeded', completed.error_code
    assert len(calls) == 3 and len(requests) == before
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
