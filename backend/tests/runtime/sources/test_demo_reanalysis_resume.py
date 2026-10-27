"""指定失敗維護工作只恢復一次；真隔離DB及受控provider，不呼叫模型。"""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from test_demo_reanalysis_flow import maintained, revisions, closed_loop
from runtime import maintenance_reanalysis as maintenance
from runtime.material_processing import claim_next_material_processing_run, execute_claimed_material_processing_run
from runtime.source_revisions import create_revision
from runtime.storage.database import database_session
from runtime.storage.tables import MaterialProcessingRun, Material


@pytest.fixture
def failed_maintenance(maintained, monkeypatch):
    import runtime.provider_http as http
    owner, material, settings, dsn, execute, original, sent = maintained
    provider = http.provider
    failed_calls = []

    def fail_review(path, body):
        if body['task'] == 'material_review':
            failed_calls.append(body['task'])
            raise ValueError('GENERATION_PROVIDER_UNAVAILABLE')
        return provider(path, body)

    monkeypatch.setattr(http, 'provider', fail_review)
    run_id = maintenance.enqueue(settings, dsn=dsn)
    failed = execute()
    assert failed.status == 'failed' and failed.error_code == 'SEMANTIC_SERVICE_UNAVAILABLE'
    assert failed.progress_stage == 'review'
    with database_session(dsn) as session:
        run = session.get(MaterialProcessingRun, run_id)
        run.maintenance = {**run.maintenance, 'reserved_calls': 11}
    monkeypatch.setattr(maintenance, 'RESUME_RUN_ID', run_id)
    monkeypatch.setattr(http, 'provider', provider)
    return owner, material, settings, dsn, execute, original, sent, run_id, fail_review, failed_calls


def test_resume_preserves_budget_identity_and_completes_with_same_run(failed_maintenance):
    owner, material, settings, dsn, _execute, original, sent, run_id, _fail, _failed_calls = failed_maintenance
    fields = ('run_id', 'created_at', 'base_revision', 'source_artifact_id',
              'input_source_set_id', 'runtime_binding', 'runtime_lock_document',
              'bundle_manifest', 'bundle_manifest_sha256', 'request_fingerprint')
    with database_session(dsn) as session:
        before = session.get(MaterialProcessingRun, run_id)
        preserved = {key: deepcopy(getattr(before, key)) for key in fields}
        old_token = before.worker_token
    assert maintenance.resume_once(settings, dsn=dsn) == run_id
    with database_session(dsn) as session:
        resumed = session.get(MaterialProcessingRun, run_id)
        assert {key: getattr(resumed, key) for key in fields} == preserved
        assert resumed.maintenance == {'mode': 'demo-full-reanalysis/v1', 'limit': 300,
                                       'reserved_calls': 11, 'resume_once_used': True}
        assert (resumed.status, resumed.progress_stage) == ('pending', 'queued')
        assert resumed.worker_token is None and resumed.lease_expires_at is None
        assert resumed.completed_at is None and resumed.error_code is None
        assert session.get(Material, material).head_revision == original['revision']
    with pytest.raises(ValueError, match='DEMO_REANALYSIS_RESUME_ALREADY_USED'):
        maintenance.resume_once(settings, dsn=dsn)
    start = len(sent)
    claim = claim_next_material_processing_run(dsn=dsn)
    assert claim.run.run_id == run_id and claim.worker_token != old_token
    completed = execute_claimed_material_processing_run(claim, settings, dsn=dsn)
    assert completed.status == 'succeeded', completed.error_code
    assert sent[start:]  # 舊格式fixture會再送分析；仍須逐次計入同一額度。
    report = maintenance.status(dsn=dsn)
    assert report['budget']['reserved_calls'] == 11 + len(sent[start:])
    assert report['budget']['resume_once_used'] is True


def test_second_failure_cannot_resume_or_reset_budget(failed_maintenance, monkeypatch):
    import runtime.provider_http as http
    _owner, material, settings, dsn, execute, original, sent, run_id, fail, failed_calls = failed_maintenance
    monkeypatch.setattr(http, 'provider', fail)
    maintenance.resume_once(settings, dsn=dsn)
    start, failed_start = len(sent), len(failed_calls)
    result = execute()
    assert result.status == 'failed' and result.error_code == 'SEMANTIC_SERVICE_UNAVAILABLE'
    before = maintenance.status(dsn=dsn)['budget']
    assert len(failed_calls) - failed_start == 1
    assert before['reserved_calls'] == 11 + len(sent) - start + len(failed_calls) - failed_start
    with pytest.raises(ValueError, match='DEMO_REANALYSIS_RESUME_ALREADY_USED'):
        maintenance.resume_once(settings, dsn=dsn)
    assert maintenance.enqueue(settings, dsn=dsn) == run_id
    assert maintenance.status(dsn=dsn)['budget'] == before
    assert claim_next_material_processing_run(dsn=dsn) is None
    with database_session(dsn) as session:
        assert session.get(Material, material).head_revision == original['revision']


def test_parallel_resume_only_one_transaction_succeeds(failed_maintenance):
    _owner, _material, settings, dsn, _execute, _original, _sent, run_id, _fail, _failed_calls = failed_maintenance
    def attempt(_):
        try:
            return maintenance.resume_once(settings, dsn=dsn)
        except ValueError as error:
            assert str(error) == 'DEMO_REANALYSIS_RESUME_ALREADY_USED'
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(attempt, range(2))).count(run_id) == 1
    assert maintenance.status(dsn=dsn)['budget']['reserved_calls'] == 11


@pytest.mark.parametrize('condition,code', [
    ('cancelled', 'DEMO_REANALYSIS_RESUME_UNAVAILABLE'),
    ('head', 'REVISION_CONFLICT'),
    ('active', 'REVISION_IN_PROGRESS'),
    ('budget', 'MATERIAL_MODEL_BUDGET_EXHAUSTED'),
    ('wrong_run', 'DEMO_REANALYSIS_RESUME_UNAVAILABLE'),
    ('deleted', 'DEMO_MATERIAL_UNAVAILABLE'),
    ('other_error', 'DEMO_REANALYSIS_RESUME_UNAVAILABLE'),
])
def test_unsafe_resume_is_rejected_without_consuming_authorization(failed_maintenance, monkeypatch, condition, code):
    owner, material, settings, dsn, _execute, original, _sent, run_id, _fail, _failed_calls = failed_maintenance
    if condition == 'active':
        create_revision(owner.learner_id, material, [], 'competing-work', settings,
                        base_revision=original['revision'], dsn=dsn)
    if condition == 'wrong_run':
        monkeypatch.setattr(maintenance, 'RESUME_RUN_ID', uuid4())
    with database_session(dsn) as session:
        run = session.get(MaterialProcessingRun, run_id)
        if condition == 'cancelled':
            run.status, run.error_code = 'cancelled', None
            run.cancel_requested_at = datetime.now(UTC)
        if condition == 'head':
            session.get(Material, material).head_revision = None
        if condition == 'budget':
            run.maintenance = {**run.maintenance, 'reserved_calls': 300}
        if condition == 'deleted':
            session.get(Material, material).discard_requested_at = datetime.now(UTC)
        if condition == 'other_error':
            run.error_code = 'MATERIAL_ANALYSIS_FAILED'
    with pytest.raises(ValueError, match='^'+code+'$'):
        maintenance.resume_once(settings, dsn=dsn)
    with database_session(dsn) as session:
        run = session.get(MaterialProcessingRun, run_id)
        assert not run.maintenance.get('resume_once_used')
        assert run.maintenance['reserved_calls'] == (300 if condition == 'budget' else 11)
