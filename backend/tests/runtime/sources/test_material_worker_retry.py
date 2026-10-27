"""真 DB 領取與持久重試；受控失敗不呼叫模型。"""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event, Lock

import pytest
from sqlalchemy import select

from product_fixtures import closed_loop, seed_run
from runtime import material_processing as processing, workers, source_normalization as sources, material_notes
from runtime.storage.database import database_session
from runtime.storage.tables import MaterialProcessingRun, SourceNormalization
from test_note_queue import setup as notes_setup, answer


@pytest.mark.parametrize('reason,retryable', [
    ('SEMANTIC_SERVICE_TIMEOUT', True), ('SEMANTIC_SERVICE_UNAVAILABLE', True),
    ('STORAGE_BUSY', True), ('SOURCE_BUSY', True), ('REVIEW_CONTRACT_INVALID', False),
    ('SEMANTIC_GENERATION_FAILED', False), ('STORAGE_QUOTA_EXCEEDED', False),
    ('MATERIAL_MODEL_BUDGET_EXHAUSTED', False),
])
def test_retry_is_persistent_once_and_content_failures_stay_failed(closed_loop, reason, retryable):
    owner, source, config, _, dsn, _ = closed_loop
    run = seed_run(owner.learner_id, source.material_id, 'retry', config, dsn=dsn)
    first = processing.claim_next_material_processing_run(dsn=dsn)
    processing._record_failure(run.run_id, reason, worker_token=first.worker_token, dsn=dsn)
    stored = processing.read_material_processing_run(owner.learner_id, run.run_id, dsn=dsn)
    assert stored.status == ('pending' if retryable else 'failed')
    assert stored.auto_retry_count == int(retryable)
    assert stored.auto_retry_reason == (reason if retryable else None)
    if retryable:
        second = processing.claim_next_material_processing_run(dsn=dsn)
        assert second.worker_token != first.worker_token
        # 舊 worker 的遲到失敗不能改新租約。
        processing._record_failure(run.run_id, 'REVIEW_CONTRACT_INVALID', worker_token=first.worker_token, dsn=dsn)
        assert processing.read_material_processing_run(owner.learner_id, run.run_id, dsn=dsn).status == 'running'
        processing._record_failure(run.run_id, reason, worker_token=second.worker_token, dsn=dsn)
        stored = processing.read_material_processing_run(owner.learner_id, run.run_id, dsn=dsn)
        assert stored.status == 'failed' and stored.auto_retry_count == 1
        assert processing.recover_interrupted_material_runs(dsn=dsn) == 0


@pytest.mark.parametrize('maintenance', [False, True])
def test_restart_shares_retry_limit_and_excludes_maintenance(closed_loop, maintenance):
    owner, source, config, _, dsn, _ = closed_loop
    run = seed_run(owner.learner_id, source.material_id, 'restart', config, dsn=dsn)
    claim = processing.claim_next_material_processing_run(dsn=dsn)
    with database_session(dsn) as db:
        row = db.get(MaterialProcessingRun, run.run_id)
        if maintenance:
            row.maintenance = {'mode': 'demo-full-reanalysis/v1', 'limit': 300, 'reserved_calls': 11}
        row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: processing.recover_interrupted_material_runs(dsn=dsn), range(2)))
    assert sum(results) == 1
    stored = processing.read_material_processing_run(owner.learner_id, run.run_id, dsn=dsn)
    assert stored.status == ('failed' if maintenance else 'pending')
    assert stored.auto_retry_count == (0 if maintenance else 1)
    if not maintenance:
        processing.claim_next_material_processing_run(dsn=dsn)
        with database_session(dsn) as db:
            db.get(MaterialProcessingRun, run.run_id).lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        assert processing.recover_interrupted_material_runs(dsn=dsn) == 1
        stored = processing.read_material_processing_run(owner.learner_id, run.run_id, dsn=dsn)
        assert stored.status == 'failed' and stored.auto_retry_count == 1


def test_cancelled_run_is_never_requeued(closed_loop):
    owner, source, config, _, dsn, _ = closed_loop
    run = seed_run(owner.learner_id, source.material_id, 'cancel', config, dsn=dsn)
    processing.claim_next_material_processing_run(dsn=dsn)
    processing.request_material_processing_cancellation(owner.learner_id, run.run_id, update_only=True, dsn=dsn)
    processing.recover_interrupted_material_runs(dsn=dsn)
    stored = processing.read_material_processing_run(owner.learner_id, run.run_id, dsn=dsn)
    assert stored.status == 'cancelled' and stored.auto_retry_count == 0


def test_material_claim_is_exclusive_and_live_lease_is_renewed(closed_loop):
    owner, source, config, _, dsn, _ = closed_loop
    run = seed_run(owner.learner_id, source.material_id, 'claim', config, dsn=dsn)
    barrier = Barrier(3)
    def claim():
        barrier.wait()
        return processing.claim_next_material_processing_run(dsn=dsn)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: claim(), range(3)))
    claims = [item for item in results if item]
    assert len(claims) == 1
    with database_session(dsn) as db:
        db.get(MaterialProcessingRun, run.run_id).lease_expires_at = datetime.now(UTC) + timedelta(seconds=2)
    processing._check_cancellation(run.run_id, worker_token=claims[0].worker_token, dsn=dsn)
    with database_session(dsn) as db:
        assert db.get(MaterialProcessingRun, run.run_id).lease_expires_at > datetime.now(UTC) + timedelta(minutes=9)
    assert processing.recover_interrupted_material_runs(dsn=dsn) == 0


def test_normalization_workers_claim_different_sources(closed_loop, monkeypatch):
    owner, _, _, _, dsn, _ = closed_loop
    material = sources.create_draft(owner.learner_id, 'parallel', 'parallel', dsn=dsn)
    for index in range(2):
        sources.upload_source(owner.learner_id, material, f'Synthetic source {index}'.encode(), f'{index}.txt', 'text/plain', str(index), dsn=dsn)
    convert = sources.convert
    barrier = Barrier(2)
    def concurrent(*args, **kwargs):
        barrier.wait(timeout=5)
        return convert(*args, **kwargs)
    monkeypatch.setattr(sources, 'convert', concurrent)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert all(pool.map(lambda _: sources.normalize_next(dsn=dsn), range(2)))
    with database_session(dsn) as db:
        rows = db.scalars(select(SourceNormalization).where(SourceNormalization.material_id == material)).all()
        assert len(rows) == 2
        assert all(row.status == 'ready' and row.attempt == 1 for row in rows)


def test_note_step_does_not_duplicate_claim(closed_loop, tmp_path, monkeypatch):
    fixture, _, _ = notes_setup(closed_loop, tmp_path, monkeypatch)
    entered, release = Event(), Event()
    claimed = []
    original_claim = material_notes._claim
    def record_claim(dsn):
        item = original_claim(dsn)
        if item and not item.get('skipped'):
            claimed.append(item['key'])
        return item
    monkeypatch.setattr(material_notes, '_claim', record_claim)
    def blocked(payload):
        entered.set()
        assert release.wait(10)
        return answer(payload)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(material_notes.step, dsn=fixture['dsn'], call=blocked)
        try:
            assert entered.wait(5)
            pool.submit(material_notes.step, dsn=fixture['dsn'], call=answer).result(timeout=5)
            assert len(claimed) == len(set(claimed))
        finally:
            release.set()
        assert first.result(timeout=5)


def test_extra_workers_start_after_single_recovery(monkeypatch):
    monkeypatch.setenv('STUDYDY_MATERIAL_WORKERS', '3')
    recovered, entered = [], []
    mutex, all_entered = Lock(), Event()
    for name in ('reconcile_new_artifacts', 'reconcile_removed_material_analysis',
                 'reconcile_published_checkpoints', 'recover_interrupted_material_runs', 'finish_material_discards'):
        monkeypatch.setattr(workers, name, lambda name=name, **_: recovered.append(name))
    def normalize(**_):
        from threading import current_thread
        with mutex:
            assert len(recovered) == 5
            entered.append(current_thread().name)
            if len(set(entered)) == 3:
                all_entered.set()
        all_entered.wait(3)
    monkeypatch.setattr(workers, 'normalize_next', normalize)
    monkeypatch.setattr(workers, 'claim_next_material_processing_run', lambda **_: None)
    monkeypatch.setattr(workers.material_notes, 'step', lambda **_: False)
    monkeypatch.setattr(workers.version_moves, 'run_next', lambda **_: False)
    monkeypatch.setattr(workers.RuntimeWorkers, '_assessment_loop', lambda self: self._stop.wait())
    monkeypatch.setattr(workers.RuntimeWorkers, '_maintenance_loop', lambda self: self._stop.wait())
    runtime = workers.RuntimeWorkers(None, {})
    runtime.start()
    try:
        assert all_entered.wait(3) and runtime.is_alive()
        assert len(set(entered)) == 3 and len(recovered) == 5
    finally:
        runtime.stop()
    assert not runtime.is_alive()


def test_retry_migration_applies_and_rolls_back(clean_database_dsn, migrations_dir, tmp_path):
    import shutil
    import psycopg
    from runtime.storage.migrations import load_migrations, run_migrations
    previous = tmp_path / 'migrations'
    previous.mkdir()
    for path in migrations_dir.glob('*.sql'):
        if int(path.name[:4]) < 22:
            shutil.copyfile(path, previous / path.name)
    run_migrations(clean_database_dsn, migrations_dir=previous)
    sql = (migrations_dir / '0022_material_auto_retry.sql').read_text()
    with psycopg.connect(clean_database_dsn, autocommit=True) as connection:
        with connection.transaction(force_rollback=True):
            connection.execute(sql)
            assert connection.execute("SELECT count(*) FROM information_schema.columns WHERE table_name='material_processing_runs' AND column_name IN ('auto_retry_count','auto_retry_reason')").fetchone() == (2,)
        assert connection.execute("SELECT count(*) FROM information_schema.columns WHERE table_name='material_processing_runs' AND column_name IN ('auto_retry_count','auto_retry_reason')").fetchone() == (0,)
    assert run_migrations(clean_database_dsn, migrations_dir=migrations_dir) == tuple(m.version for m in load_migrations(migrations_dir)[21:])


def test_same_run_retry_reuses_checkpoint_and_terminal_failure_can_archive(closed_loop):
    from runtime.storage.analysis_archive import AnalysisArchive
    owner, source, config, _, dsn, _ = closed_loop
    run = seed_run(owner.learner_id, source.material_id, 'checkpoint-retry', config, dsn=dsn)
    claim = processing.claim_next_material_processing_run(dsn=dsn)
    archive = AnalysisArchive(claim, dsn=dsn)
    saved = {'complete': False, 'synthetic_checkpoint': True}
    archive.save_checkpoint(saved)
    processing._record_failure(run.run_id, 'SEMANTIC_SERVICE_TIMEOUT', worker_token=claim.worker_token, dsn=dsn)
    next_claim = processing.claim_next_material_processing_run(dsn=dsn)
    next_archive = AnalysisArchive(next_claim, dsn=dsn)
    assert next_archive.load_checkpoint() == saved
    processing._record_failure(run.run_id, 'SEMANTIC_SERVICE_TIMEOUT', worker_token=next_claim.worker_token, dsn=dsn)
    next_archive.save_failure(RuntimeError('synthetic'))
    assert (next_archive.directory / 'failure.json').is_file()
