
from product_fixtures import publish_fixture_structure
"""Checkpoint 依發布交易清理；內容品質提示不影響工作是否需要接續。"""
import json
from pathlib import Path

import pytest

from pdf_evidence import material_pipeline
import runtime.material_processing as processing
from runtime.api.models import project_material_run
from runtime.storage.analysis_archive import (
    AnalysisArchiveError, _material_directory, reconcile_published_checkpoints,
)
from runtime.storage.knowledge_structures import read_knowledge_structure
from test_material_library import product_snapshot
from test_source_revisions import revisions, closed_loop, normalizer


def _review_semantics(monkeypatch, review_required):
    calls = []

    def semantic(_client, **kwargs):
        calls.append(kwargs['request'])
        handle = kwargs['request']['sections'][0]['evidence'][0][0]
        return {'concepts': [{'k': 'queue', 'l': 'Queue', 'a': [], 'c': [{'m': None, 's': [handle]}]}],
                'relations': [], 'review_required': review_required}

    monkeypatch.setattr(processing, 'analyze_material', lambda *args, **kwargs:
                        material_pipeline.analyze_material(*args, **kwargs, semantic_call=semantic))
    return calls


@pytest.mark.parametrize('review_required', [False, True])
def test_published_checkpoint_is_removed_even_when_quality_needs_review(revisions, monkeypatch, review_required):
    learner, material, _, dsn, add, start, execute, _, old, _ = revisions
    calls = _review_semantics(monkeypatch, review_required)
    archives = []
    factory = processing.AnalysisArchive

    def capture(*args, **kwargs):
        archive = factory(*args, **kwargs)
        archives.append(archive)
        return archive

    monkeypatch.setattr(processing, 'AnalysisArchive', capture)
    second = add('B.pdf', 'A queue removes the first inserted element first.')
    run = start([second], 'review-cleanup', old['revision'])
    result = execute()
    assert result.status == ('partial' if review_required else 'succeeded')
    assert result.error_code is None
    document = read_knowledge_structure(learner.learner_id, material, run_id=run.run_id, dsn=dsn).document
    if review_required:
        assert document['source_review_required'] is True
        assert document['status']['quality'] == 'needs_review'
    directory = _material_directory(learner.learner_id, material) / run.run_id.hex
    assert not (directory / 'checkpoint.json').exists()
    assert (directory / 'call-000001/decoded.json').is_file()
    assert json.loads((directory / 'completion.json').read_text())['knowledge_structure_revision'] == document['revision']
    assert project_material_run(result).analysis_saved is False
    # 即使舊 worker token 尚在，也不能在已發布後重建 checkpoint。
    with pytest.raises(AnalysisArchiveError, match='MATERIAL_RUN_UNAVAILABLE'):
        archives[0].save_checkpoint({'late': True})
    before = product_snapshot(dsn)
    reconcile_published_checkpoints(dsn=dsn)
    assert product_snapshot(dsn) == before
    assert len(calls) == 1
    assert not (directory / 'checkpoint.json').exists()


@pytest.mark.parametrize('corrupt_checkpoint', [False, True])
def test_cleanup_failure_keeps_published_result_and_reconciles_without_model(revisions, monkeypatch, caplog, corrupt_checkpoint):
    learner, material, _, dsn, add, start, execute, _, old, _ = revisions
    calls = _review_semantics(monkeypatch, True)
    second = add('B.pdf', 'A queue removes the first inserted element first.')
    run = start([second], 'cleanup-interrupted', old['revision'])
    directory = _material_directory(learner.learner_id, material) / run.run_id.hex
    checkpoint = directory / 'checkpoint.json'
    unlink = Path.unlink

    def unavailable(path, *args, **kwargs):
        if path == checkpoint:
            raise PermissionError('Synthetic cleanup failure')
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, 'unlink', unavailable)
        result = execute()
    assert result.status == 'partial' and result.error_code is None
    assert 'ANALYSIS_CHECKPOINT_CLEANUP_FAILED' in caplog.text
    assert checkpoint.is_file()
    assert project_material_run(result).analysis_saved is False
    if corrupt_checkpoint:
        checkpoint.write_text('invalid saved checkpoint')
    before = product_snapshot(dsn)
    reconcile_published_checkpoints(dsn=dsn)
    assert not checkpoint.exists()
    if corrupt_checkpoint:
        assert 'ANALYSIS_CHECKPOINT_METADATA_UNAVAILABLE' in caplog.text
    assert product_snapshot(dsn) == before
    assert len(calls) == 1


def test_unpublished_or_different_input_failure_keeps_its_checkpoint(revisions, monkeypatch):
    learner, material, _, dsn, add, start, execute, _, old, _ = revisions
    second = add('B.pdf', 'A queue removes the first inserted element first.')
    failed_run = start([second], 'publication-failed', old['revision'])
    with monkeypatch.context() as patch:
        patch.setattr(processing, 'publish_knowledge_structure', lambda *a, **k:
                      (_ for _ in ()).throw(ValueError('Synthetic publication failure')))
        assert execute().status == 'failed'
    checkpoint = _material_directory(learner.learner_id, material) / failed_run.run_id.hex / 'checkpoint.json'
    assert json.loads(checkpoint.read_text())['data']['complete'] is True
    before = checkpoint.read_bytes()
    reconcile_published_checkpoints(dsn=dsn)
    assert checkpoint.read_bytes() == before  # 模型完成不等於資料庫發布完成。

    third = add('C.pdf', 'A tree traversal visits each node in the tree.')
    start([third], 'different-input-success', old['revision'])
    assert execute().status == 'succeeded'
    reconcile_published_checkpoints(dsn=dsn)
    assert checkpoint.read_bytes() == before
