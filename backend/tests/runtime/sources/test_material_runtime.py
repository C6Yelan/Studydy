
"""單一教材執行流程：出題設定可獨立更新，原 run／checkpoint 身分不改寫。"""

from copy import deepcopy
import json

import pytest

import runtime.material_processing as processing
from runtime.material_runtime import lock_matches_binding, runtime_binding, same_material_runtime
from runtime.storage.analysis_archive import _material_directory
from runtime.storage.tables import MaterialProcessingRun
from runtime.storage.database import database_session
from product_fixtures import _settings, closed_loop
from test_source_revisions import revisions


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


@pytest.mark.parametrize('version', ['material-notes/v10', 'material-notes/v11', 'material-notes/v12'])
def test_historical_notes_lock_remains_valid_for_material_resume(tmp_path, version):
    from content_identity import canonical_sha256
    from runtime.material_runtime import validate_runtime_lock

    config = _settings(tmp_path)
    current = runtime_binding(config)
    historical = deepcopy(config)
    historical['runtime_lock']['material_notes'].update(
        version=version,
        prompt='歷史筆記提示', selection_prompt='歷史選點提示', review_prompt='歷史核對提示')
    if version == 'material-notes/v10':
        historical['runtime_lock']['material_notes']['summary_layout'] = 'a4-12pt-18mm/v1'
    old = historical['runtime_lock']
    before = deepcopy(old)
    # 合法 binding 只更新對應的封存 hash，不依賴待測 validator 建立。
    saved = {key: value for key, value in current.items() if key != 'runtime_binding_sha256'}
    saved['runtime_lock_sha256'] = canonical_sha256(old)
    saved['runtime_binding_sha256'] = canonical_sha256(saved)
    assert same_material_runtime(old, saved, config['runtime_lock'], current)
    assert validate_runtime_lock(old) == before
    assert runtime_binding(historical) == saved
    assert old == before and canonical_sha256(old) == saved['runtime_lock_sha256']


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


@pytest.mark.parametrize('update', ['assessment', 'budgets', 'context_capacity', 'luna_capacity'])
def test_failed_checkpoint_resumes_after_settings_update_without_repeating_models(revisions, monkeypatch, update):
    learner, material, settings, dsn, add, start, execute, _, old, calls = revisions
    second = add('B.pdf', 'A queue removes the first inserted element first.')
    if update == 'budgets':
        settings['runtime_lock']['material_semantics']['max_tokens'] = 8192
    elif update == 'context_capacity':
        settings['runtime_lock']['semantic_service']['max_model_len'] = 32768
    elif update == 'luna_capacity':
        from runtime.luna_test import test_lock as luna_lock
        settings['runtime_lock'] = luna_lock(settings['runtime_lock'])
        settings['runtime_lock']['semantic_service']['max_model_len'] = 272000
    run = start([second], 'before-update-failed', old['revision'])
    def fail_publication(*_args, **_kwargs):
        raise ValueError("Synthetic publication failure")

    with monkeypatch.context() as patch:
        patch.setattr(processing, 'publish_knowledge_structure', fail_publication)
        assert execute().status == 'failed'
    path = _material_directory(learner.learner_id, material) / run.run_id.hex / 'checkpoint.json'
    before_checkpoint = path.read_bytes()
    original_runtime = deepcopy(run.runtime_binding)
    original_lock = deepcopy(run.runtime_lock_document)
    settings['runtime_lock'] = deepcopy(settings['runtime_lock'])
    settings['runtime_lock']['schema'] = 'studydy-runtime-lock/v1'
    if update == 'budgets':
        settings['runtime_lock']['material_semantics']['max_tokens'] = 32768
    elif update in ('context_capacity', 'luna_capacity'):
        settings['runtime_lock']['semantic_service']['max_model_len'] = 65536
    else:
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
        assert session.get(MaterialProcessingRun, run.run_id).runtime_lock_document == original_lock
    completed = _material_directory(learner.learner_id, material) / retry.run_id.hex
    assert json.loads((completed / 'completion.json').read_text())['reused_from_run'] == str(run.run_id)
    assert not path.exists() and not (completed / 'checkpoint.json').exists()


@pytest.mark.parametrize('changed', ['material_prompt', 'tampered_snapshot'])
def test_unusable_prior_runtime_is_skipped_and_reanalysis_preserves_its_archive(revisions, monkeypatch, changed):
    learner, material, settings, dsn, add, start, execute, _, old, calls = revisions
    second = add('B.pdf', 'A queue removes the first inserted element first.')
    run = start([second], 'keep-failed', old['revision'])
    def fail_publication(*_args, **_kwargs):
        raise ValueError("Synthetic publication failure")

    with monkeypatch.context() as patch:
        patch.setattr(processing, 'publish_knowledge_structure', fail_publication)
        assert execute().status == 'failed'
    path = _material_directory(learner.learner_id, material) / run.run_id.hex / 'checkpoint.json'
    saved = {p.relative_to(path.parent): p.read_bytes() for p in path.parent.rglob('*') if p.is_file()}
    if changed == 'material_prompt':
        settings['runtime_lock'] = deepcopy(settings['runtime_lock'])
        settings['runtime_lock']['material_semantics']['prompt'] += ' Changed material policy.'
    else:
        with database_session(dsn) as session:
            row = session.get(MaterialProcessingRun, run.run_id)
            row.runtime_lock_document = {**row.runtime_lock_document, 'schema': 'tampered'}
    with database_session(dsn) as session:
        prior = session.get(MaterialProcessingRun, run.run_id)
        saved_lock, saved_binding = deepcopy(prior.runtime_lock_document), deepcopy(prior.runtime_binding)
    from pdf_evidence import material_pipeline
    actual = material_pipeline.extract_page
    extracted = []
    monkeypatch.setattr(material_pipeline, 'extract_page', lambda *args: (extracted.append(args[2]), actual(*args))[1])
    retry = start([second], 'new-runtime-reanalysis', old['revision'])
    before = len(calls)
    result = execute()
    assert result.status == 'succeeded', result.error_code
    assert len(calls) == before + 1 and extracted == [1]
    assert {p.relative_to(path.parent): p.read_bytes() for p in path.parent.rglob('*') if p.is_file()} == saved
    with database_session(dsn) as session:
        prior = session.get(MaterialProcessingRun, run.run_id)
        assert prior.runtime_lock_document == saved_lock and prior.runtime_binding == saved_binding
    completed = _material_directory(learner.learner_id, material) / retry.run_id.hex
    receipt = json.loads((completed / 'completion.json').read_text())
    assert receipt['reused_from_run'] is None and receipt['evidence_reused_from_runs'] == []
