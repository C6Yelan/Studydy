"""超額 OCR 區塊可局部接續，但不能改寫已使用來源或無限重辨識。"""
from copy import deepcopy
from dataclasses import asdict

import pytest

from knowledge_map.structure import SemanticState, apply_semantic_response, build_document_context
from pdf_evidence.ocr_page_evidence import canonical_sha256
from pdf_evidence import material_pipeline as pipeline, ocr_recovery
from test_knowledge_structure_v1 import _block, _page
from test_material_pipeline_v1 import _pdf, _request, _settings, Client


def fixture_data(tmp_path):
    source = tmp_path / 'source.pdf'
    _pdf(source, 1)
    original = _block(1, 1, 'other', 'Repeated table content. ' * 200)
    original['source'] = 'unlimited_ocr'
    original['evidence_id'] = 'evidence:sha256:' + canonical_sha256({
        'page_ref': _page(1, [])['page_ref'], 'block_id': original['block_id'],
        'kind': original['kind'], 'source': original['source'], 'text': original['text'],
        'reading_order': original['reading_order'], 'region': original['locator']['region'],
    })
    context = build_document_context([_page(1, [_block(1, 0, 'paragraph', 'Already parsed.'), original])], page_count=1)
    state = SemanticState()
    apply_semantic_response({'concepts': [{'k': 'prior', 'l': 'Prior', 'a': [], 'c': [{'m': None, 's': [0]}]}],
                             'relations': []}, context=context,
                            bundle={'sections': context['sections'], 'evidence': context['evidence'][:1]}, state=state)
    saved = dict(context=context, state=asdict(state), cursor=1, semantic_calls=75, ocr_calls=1,
                 complete=False, source_sha256='1' * 64, evidence_duration_ms=0, semantic_duration_ms=0)
    binding = {'source_set_digest': '1' * 64,
               'manifest': {'items': [{'source_id': 'source'}]},
               'bundle': {'pages': [{'source_id': 'source', 'normalized_page': 1, 'page': 1}]}}
    return source, saved, binding


class Archive:
    def __init__(self, saved):
        self.original = deepcopy(saved)
        self.saved = deepcopy(saved)
        self.recoveries = []
    def load_checkpoint(self): return deepcopy(self.original)
    def save_checkpoint(self, data): self.saved = deepcopy(data)
    def save_ocr_recovery(self, data): self.recoveries.append(deepcopy(data))
    def reuse_review_response(self, request): return None
    def prepare_call(self, *args): pass
    def save_response(self, *args): pass


@pytest.mark.parametrize('outcome', ['recovered', 'still_oversized', 'write_failure'])
def test_pipeline_recovers_only_unused_block_and_keeps_completed_batches(tmp_path, monkeypatch, outcome):
    source, saved, binding = fixture_data(tmp_path)
    archive = Archive(saved)
    original = deepcopy(saved)
    calls = []
    class OCR:
        def request(self, request, timeout, **kwargs):
            calls.append(request)
            text = 'Recovered table content.' if outcome != 'still_oversized' else saved['context']['evidence'][1]['exact_text']
            return {'schema': 'local-ocr-response/v1', 'request_id': request['request_id'],
                    'blocks': [{'type': 'table', 'bbox': [0, 0, 1000, 1000], 'text': text}]}
        def close(self): pass
        def abort(self): pass
    monkeypatch.setattr(ocr_recovery, 'start_ocr_process', lambda settings: OCR())
    monkeypatch.setattr(pipeline, 'material_request_fits', lambda client, lock, request, **kwargs:
                        all(len(row[3]) < 100 for section in request['sections'] for row in section['evidence']))
    semantics = []
    def semantic(client, **kwargs):
        semantics.append(kwargs['request'])
        return {'concepts': [{'k': 'continued', 'l': 'Continued', 'a': [], 'c': [{'m': None, 's': [1]}]}], 'relations': []}
    if outcome == 'write_failure':
        def fail(data): raise RuntimeError('Synthetic recovery write failure')
        archive.save_ocr_recovery = fail
    arguments = dict(client=Client(), semantic_call=semantic, analysis_archive=archive)
    if outcome == 'recovered':
        result = pipeline.analyze_material([_request(source)], binding, _settings(tmp_path), **arguments)
        assert result['metrics']['semantic_calls'] == 76
        assert result['metrics']['ocr_calls'] == 2
        assert len(semantics) == 1
        replacement = archive.saved['context']['evidence'][1]
        assert replacement['evidence_id'] != original['context']['evidence'][1]['evidence_id']
        assert replacement['source_locator'] == original['context']['evidence'][1]['source_locator']
        assert archive.saved['state']['concepts']['prior'] == original['state']['concepts']['prior']
        assert archive.saved['context']['evidence'][0] == original['context']['evidence'][0]
        assert archive.saved['complete'] and archive.saved['cursor'] == 2
        assert archive.recoveries[0]['replacement'] == replacement
    elif outcome == 'still_oversized':
        with pytest.raises(pipeline.MaterialAnalysisError, match='SEMANTIC_INPUT_TOO_LARGE'):
            pipeline.analyze_material([_request(source)], binding, _settings(tmp_path), **arguments)
        assert len(archive.saved['recovered_ocr_blocks']) == 1
        assert not semantics
    else:
        with pytest.raises(RuntimeError, match='Synthetic recovery write failure'):
            pipeline.analyze_material([_request(source)], binding, _settings(tmp_path), **arguments)
        assert archive.saved['context'] == original['context']
        assert not semantics
    assert len(calls) == 1
    assert calls[0]['render']['width'] < 200 and calls[0]['render']['height'] < 200
    assert archive.original == original


def test_recovery_refuses_to_change_an_evidence_already_used_by_a_claim(tmp_path, monkeypatch):
    source, saved, binding = fixture_data(tmp_path)
    evidence = saved['context']['evidence'][1]
    state = SemanticState(**saved['state'])
    state.concepts['prior']['claims'][0]['source_spans'][0]['evidence_id'] = evidence['evidence_id']
    monkeypatch.setattr(ocr_recovery, 'start_ocr_process', lambda _: pytest.fail('must not call OCR'))
    with pytest.raises(ValueError, match='SEMANTIC_INPUT_TOO_LARGE'):
        ocr_recovery.recover_ocr_block(evidence, state, [_request(source)], binding, _settings(tmp_path), lambda: None)
