
from product_fixtures import publish_fixture_structure
"""單一教材執行流程：出題設定可獨立更新，原 run／checkpoint 身分不改寫。"""
from copy import deepcopy
import json

import pytest

import runtime.material_processing as processing
from runtime.material_runtime import lock_matches_binding, same_material_runtime, runtime_binding
from runtime.storage.analysis_archive import _material_directory
from runtime.storage.tables import MaterialProcessingRun, database_session
from test_closed_loop_v1 import _settings
from test_source_revisions import revisions, closed_loop, normalizer


def test_only_material_dependencies_and_actual_model_define_resume_compatibility(tmp_path):
    config = _settings(tmp_path)
    first = runtime_binding(config)
    changed = deepcopy(config)
    changed['runtime_lock']['schema'] = 'studydy-runtime-lock/v1'
    changed['runtime_lock']['assessment'] = {'prompt': 'new assessment contract is irrelevant to material processing'}
    second = runtime_binding(changed)
    assert first != second
    assert same_material_runtime(config['runtime_lock'], first, changed['runtime_lock'], second)
    assert not lock_matches_binding(changed['runtime_lock'], first)
    changed['runtime_lock']['material_semantics']['prompt'] += ' Different material instructions.'
    third = runtime_binding(changed)
    assert not same_material_runtime(config['runtime_lock'], first, changed['runtime_lock'], third)


def test_http_model_change_is_not_an_assessment_only_update(tmp_path):
    config = _settings(tmp_path)
    first = runtime_binding(config)
    changed = deepcopy(config)
    changed['runtime_lock']['semantic_service'].update(model_id='example/second-model', revision='a' * 40)
    second = runtime_binding(changed)
    assert not same_material_runtime(config['runtime_lock'], first, changed['runtime_lock'], second)


def test_pending_run_uses_its_frozen_settings_after_assessment_update(revisions):
    learner, material, settings, dsn, add, start, execute, _, old, calls = revisions
    second = add('B.pdf', 'A queue removes the first inserted element first.')
    original_lock = deepcopy(settings['runtime_lock'])
    run = start([second], 'pending-before-update', old['revision'])
    settings['runtime_lock'] = deepcopy(original_lock)
    settings['runtime_lock']['schema'] = 'studydy-runtime-lock/v1'
    settings['runtime_lock']['assessment'] = {'prompt': 'changed after the work was created'}
    before = len(calls)
    assert start([second], 'pending-before-update', old['revision']).run_id == run.run_id
    result = execute()
    assert result.status == 'succeeded', result.error_code
    assert len(calls) == before + 1
    assert result.runtime_binding == run.runtime_binding
    assert result.output_binding['runtime_lock_sha256'] == run.runtime_binding['runtime_lock_sha256']
    with database_session(dsn) as session:
        assert session.get(MaterialProcessingRun, run.run_id).runtime_lock_document == original_lock


def test_failed_checkpoint_resumes_after_assessment_update_without_repeating_models(revisions, monkeypatch):
    learner, material, settings, dsn, add, start, execute, _, old, calls = revisions
    second = add('B.pdf', 'A queue removes the first inserted element first.')
    run = start([second], 'before-update-failed', old['revision'])
    with monkeypatch.context() as patch:
        patch.setattr(processing, 'publish_knowledge_structure', lambda *a, **k:
                      (_ for _ in ()).throw(ValueError('Synthetic publication failure')))
        assert execute().status == 'failed'
    path = _material_directory(learner.learner_id, material) / run.run_id.hex / 'checkpoint.json'
    before_checkpoint = path.read_bytes()
    original_runtime = deepcopy(run.runtime_binding)
    settings['runtime_lock'] = deepcopy(settings['runtime_lock'])
    settings['runtime_lock']['schema'] = 'studydy-runtime-lock/v1'
    settings['runtime_lock']['assessment']['prompt'] += ' New quality guidance.'
    retry = start([second], 'after-update-retry', old['revision'])
    assert path.read_bytes() == before_checkpoint
    before = len(calls)
    result = execute()
    assert result.status == 'succeeded', result.error_code
    assert len(calls) == before
    assert result.runtime_binding != original_runtime
    assert result.output_binding['runtime_lock_sha256'] == result.runtime_binding['runtime_lock_sha256']
    with database_session(dsn) as session:
        assert session.get(MaterialProcessingRun, run.run_id).runtime_binding == original_runtime
    completed = _material_directory(learner.learner_id, material) / retry.run_id.hex
    assert json.loads((completed / 'completion.json').read_text())['reused_from_run'] == str(run.run_id)
    assert not path.exists() and not (completed / 'checkpoint.json').exists()


@pytest.mark.parametrize('changed', ['material_prompt', 'tampered_snapshot'])
def test_unverified_or_changed_material_runtime_never_silently_restarts(revisions, monkeypatch, changed):
    learner, material, settings, dsn, add, start, execute, _, old, calls = revisions
    second = add('B.pdf', 'A queue removes the first inserted element first.')
    run = start([second], 'keep-failed', old['revision'])
    with monkeypatch.context() as patch:
        patch.setattr(processing, 'publish_knowledge_structure', lambda *a, **k:
                      (_ for _ in ()).throw(ValueError('Synthetic publication failure')))
        assert execute().status == 'failed'
    path = _material_directory(learner.learner_id, material) / run.run_id.hex / 'checkpoint.json'
    saved = path.read_bytes()
    if changed == 'material_prompt':
        settings['runtime_lock'] = deepcopy(settings['runtime_lock'])
        settings['runtime_lock']['material_semantics']['prompt'] += ' Changed material policy.'
    else:
        with database_session(dsn) as session:
            row = session.get(MaterialProcessingRun, run.run_id)
            row.runtime_lock_document = {**row.runtime_lock_document, 'schema': 'tampered'}
    start([second], 'must-not-restart', old['revision'])
    before = len(calls)
    result = execute()
    assert result.status == 'failed' and result.error_code == 'ANALYSIS_RUNTIME_CHANGED'
    assert len(calls) == before
    assert path.read_bytes() == saved
