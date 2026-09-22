
from product_fixtures import seed_pdf, seed_run, publish_fixture_structure
"""Discard uses real disposable PostgreSQL and synthetic PDFs, never models."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import io
from pathlib import Path
from threading import Event, local
from types import SimpleNamespace
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import runtime.api.app as api_app
import runtime.material_discard as discard
import runtime.material_processing as processing
import runtime.workers as workers
import runtime.storage.artifacts as artifacts
from runtime.storage.tables import Material, MaterialProcessingRun as RunRow, database_session
from runtime.storage.migrations import run_migrations
from runtime.storage.knowledge_structures import read_knowledge_structure, KnowledgeStructureStoreError
from runtime.learner_session import register_account
from runtime.source_normalization import SourceError
import runtime.source_revisions as revisions
from learning_adaptation.study_sessions import create_study_session
from test_closed_loop_v1 import closed_loop, _pdf, _structure

ORIGIN = "http://127.0.0.1:4173"

class SessionToken(str):
    def __repr__(self): return "<test session token>"


@pytest.fixture
def unused(closed_loop):
    learner, original_source, settings, original, dsn, token = closed_loop
    source = seed_pdf(learner.learner_id, io.BytesIO(_pdf()), "discard-source", dsn=dsn)
    return SimpleNamespace(learner=learner, source=source, original_source=original_source, original=original,
                           settings=settings, dsn=dsn, token=SessionToken(token), root=artifacts._root())


def create(f, key="discard-run"):
    return seed_run(f.learner.learner_id, f.source.material_id, f.source.artifact_id, key, f.settings, dsn=f.dsn)


def claim(f, stage="evidence"):
    claimed = processing.claim_next_material_processing_run(dsn=f.dsn)
    for next_stage in ("evidence", "semantics", "publishing"):
        if stage == "queued": break
        processing._record_progress(claimed.run.run_id, next_stage, 1, 1, dsn=f.dsn)
        if stage == next_stage: break
    return claimed


def request(f):
    return discard.request_material_discard(f.learner.learner_id, f.source.material_id, dsn=f.dsn)


def read(f, run):
    return processing.read_material_processing_run(f.learner.learner_id, run.run_id, dsn=f.dsn)


def assert_removed(f):
    with psycopg.connect(f.dsn) as c:
        for table in ('materials', 'material_processing_runs', 'artifacts'):
            assert c.execute(f"SELECT count(*) FROM {table} WHERE material_id=%s", (f.source.material_id,)).fetchone() == (0,)
    assert not (f.root / 'objects' / f.source.artifact_id.hex).exists()
    assert not (f.root / '.trash' / f.source.artifact_id.hex).exists()
    assert (f.root / 'objects' / f.original_source.artifact_id.hex).exists()
    assert read_knowledge_structure(f.learner.learner_id, f.original_source.material_id, revision=f.original['revision'], dsn=f.dsn).document == f.original


@pytest.mark.parametrize('state', ['no-run', 'failed', 'cancelled', 'pending'])
def test_unused_terminal_and_pending_materials_are_physically_removed(unused, state):
    if state != 'no-run':
        run = create(unused)
        if state == 'failed':
            claim(unused); processing._record_failure(run.run_id, 'EXPECTED_FAILURE', dsn=unused.dsn)
        if state == 'cancelled':
            # Historical terminal cancellation is valid, and must never be auto-discarded.
            with psycopg.connect(unused.dsn) as c:
                c.execute("UPDATE material_processing_runs SET status='cancelled',cancel_requested_at=now(),completed_at=now() WHERE run_id=%s", (run.run_id,))
    discard.finish_material_discards(dsn=unused.dsn)
    assert (unused.root / 'objects' / unused.source.artifact_id.hex).exists()
    assert request(unused) == 'removed'
    assert_removed(unused)
    assert processing.claim_next_material_processing_run(dsn=unused.dsn) is None


@pytest.mark.parametrize('stage', ['queued', 'evidence', 'semantics'])
def test_running_discard_commits_intent_then_cancels_and_purges(unused, stage, monkeypatch):
    run = create(unused); claimed = claim(unused, stage)
    assert request(unused) == 'removing'
    before = read(unused, run)
    assert before.status == 'running' and before.cancel_requested_at is not None
    assert request(unused) == 'removing' and read(unused, run) == before
    assert processing.request_material_processing_cancellation(unused.learner.learner_id, run.run_id, dsn=unused.dsn) == before
    with psycopg.connect(unused.dsn) as c:
        assert c.execute('SELECT discard_requested_at IS NOT NULL FROM materials WHERE material_id=%s', (unused.source.material_id,)).fetchone() == (True,)
    monkeypatch.setattr(processing, 'runtime_preflight', lambda _: pytest.fail('cancelled work must not preflight'))
    monkeypatch.setattr(processing, '_record_failure', lambda *a, **kw: pytest.fail('cancellation is not failure'))
    stopped = processing.execute_claimed_material_processing_run(claimed, unused.settings, dsn=unused.dsn)
    assert stopped.status == 'cancelled' and stopped.output_binding is None and stopped.error_code is None
    assert (stopped.progress_stage, stopped.completed_pages, stopped.total_pages) == (before.progress_stage, before.completed_pages, before.total_pages)
    discard.finish_material_discards(dsn=unused.dsn)
    assert_removed(unused)


@pytest.mark.parametrize('state', ['publishing', 'succeeded', 'partial'])
def test_published_data_deletes_and_discard_prevents_late_publication(unused, state):
    run = create(unused); claim(unused, 'publishing')
    document = _structure(str(run.run_id), unused.source.sha256, unused.settings['runtime_lock'], partial=state == 'partial')
    if state != 'publishing':
        publish_fixture_structure(unused.learner.learner_id, unused.source.material_id, run.run_id, document, dsn=unused.dsn)
    assert request(unused) == ('removing' if state == 'publishing' else 'removed')
    if state == 'publishing':
        assert read(unused, run).cancel_requested_at is not None
        with pytest.raises(SourceError, match='MATERIAL_NOT_DISCARDABLE'):
            create(unused, 'after-delete-intent')
        with pytest.raises(KnowledgeStructureStoreError,match='MATERIAL_RUN_UNAVAILABLE'):
            publish_fixture_structure(unused.learner.learner_id, unused.source.material_id, run.run_id, document, dsn=unused.dsn)
        with pytest.raises(processing.MaterialProcessingCancelled):
            processing._check_cancellation(run.run_id,dsn=unused.dsn)
        discard.finish_material_discards(dsn=unused.dsn)
    assert_removed(unused)
    with psycopg.connect(unused.dsn) as db:
        assert db.execute('SELECT count(*) FROM knowledge_structures WHERE material_id=%s', (unused.source.material_id,)).fetchone() == (0,)




def ordered_race(monkeypatch, f, run, first, second, first_module, second_module, *, lock_material):
    held, contender, release = Event(), Event(), Event()
    identity = local()
    @contextmanager
    def gated(dsn):
        with database_session(dsn) as session:
            if identity.role == 'first':
                if lock_material:
                    session.scalar(select(Material).where(Material.material_id == f.source.material_id).with_for_update())
                session.scalar(select(RunRow).where(RunRow.run_id == run.run_id).with_for_update())
                held.set(); assert release.wait(5)
            else: contender.set()
            yield session
    def invoke(role, action):
        identity.role = role
        try: return action()
        except (processing.MaterialProcessingError, processing.MaterialProcessingCancelled, discard.MaterialDiscardError, SourceError) as e: return type(e), str(e)
    with monkeypatch.context() as patch:
        patch.setattr(first_module, 'database_session', gated)
        patch.setattr(second_module, 'database_session', gated)
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(invoke, 'first', first); assert held.wait(5)
            b = pool.submit(invoke, 'second', second); assert contender.wait(5)
            release.set()
            return a.result(10), b.result(10)


@pytest.mark.parametrize('discard_first', [True, False])
def test_discard_publishing_race_is_ordered_by_row_locks(unused, monkeypatch, discard_first):
    run = create(unused); claim(unused, 'semantics')
    publish = lambda: processing._record_progress(run.run_id, 'publishing', 1, 1, dsn=unused.dsn)
    remove = lambda: request(unused)
    results = ordered_race(monkeypatch, unused, run, remove if discard_first else publish, publish if discard_first else remove,
                 discard if discard_first else processing, processing if discard_first else discard, lock_material=discard_first)
    if discard_first:
        assert results[1][0] is processing.MaterialProcessingCancelled
        discard.finish_material_discards(dsn=unused.dsn)
        assert_removed(unused)
        return
    saved = read(unused, run)
    assert saved.status == ('cancelled' if discard_first else 'running')
    assert saved.progress_stage == ('semantics' if discard_first else 'publishing')
    assert saved.cancel_requested_at is not None
    with psycopg.connect(unused.dsn) as c:
        assert c.execute('SELECT discard_requested_at IS NOT NULL FROM materials WHERE material_id=%s', (unused.source.material_id,)).fetchone() == (True,)


def test_discard_wins_create_run_race(unused, monkeypatch):
    run = create(unused); claim(unused)
    first, second = ordered_race(monkeypatch, unused, run, lambda: request(unused), lambda: create(unused, 'racing-create'), discard, revisions, lock_material=True)
    assert first == 'removing'
    assert second == (SourceError, 'MATERIAL_NOT_DISCARDABLE')
    with psycopg.connect(unused.dsn) as c:
        assert c.execute('SELECT count(*) FROM material_processing_runs WHERE material_id=%s', (unused.source.material_id,)).fetchone() == (1,)


@pytest.mark.parametrize('discard_first', [True, False])
def test_pending_claim_vs_discard_has_no_uncontrolled_worker(unused, monkeypatch, discard_first):
    run = create(unused)
    take = lambda: processing.claim_next_material_processing_run(dsn=unused.dsn)
    remove = lambda: request(unused)
    result = ordered_race(monkeypatch, unused, run, remove if discard_first else take, take if discard_first else remove,
                          discard if discard_first else processing, processing if discard_first else discard, lock_material=discard_first)
    if discard_first:
        assert result == ('removed', None)
    else:
        assert result[0].run.status == 'running' and result[1] == 'removing'
        with pytest.raises(processing.MaterialProcessingCancelled): processing._check_cancellation(run.run_id, dsn=unused.dsn)
        discard.finish_material_discards(dsn=unused.dsn)
    assert_removed(unused)


@pytest.mark.parametrize('discard_first', [True, False])
def test_failure_race_preserves_cancel_authority(unused, monkeypatch, discard_first):
    run = create(unused); claim(unused)
    failure = lambda: processing._record_failure(run.run_id, 'EXPECTED_FAILURE', dsn=unused.dsn)
    remove = lambda: request(unused)
    ordered_race(monkeypatch, unused, run, remove if discard_first else failure, failure if discard_first else remove,
                 discard if discard_first else processing, processing if discard_first else discard, lock_material=discard_first)
    discard.finish_material_discards(dsn=unused.dsn)
    assert_removed(unused)


def test_restart_recovers_persisted_discard_and_does_not_delete_ordinary_failure(unused):
    create(unused); claim(unused)
    assert request(unused) == 'removing'
    assert processing.recover_interrupted_material_runs(dsn=unused.dsn) == 1
    discard.finish_material_discards(dsn=unused.dsn)
    assert_removed(unused)


@pytest.mark.parametrize('restart', [False, True])
def test_real_worker_finishes_discard_without_model_calls(unused, monkeypatch, restart):
    run = create(unused)
    entered, release, purged = Event(), Event(), Event()
    if restart:
        claim(unused)
        assert request(unused) == 'removing'
    def analyze(_request, _settings, *, progress_callback, **kwargs):
        assert not restart
        progress_callback('evidence', 0, 1)
        entered.set(); assert release.wait(10)
        progress_callback('evidence', 1, 1)
        pytest.fail('accepted discard must unwind at checkpoint')
    monkeypatch.setattr(processing, 'runtime_preflight', lambda _: run.runtime_binding)
    monkeypatch.setattr(processing, 'analyze_material', analyze)
    original = workers.finish_material_discards
    def finish(**kwargs):
        original(**kwargs)
        with psycopg.connect(unused.dsn) as c:
            if c.execute('SELECT count(*) FROM materials WHERE material_id=%s', (unused.source.material_id,)).fetchone() == (0,): purged.set()
    monkeypatch.setattr(workers, 'finish_material_discards', finish)
    worker = workers.RuntimeWorkers(unused.dsn, unused.settings)
    worker.start()
    try:
        if not restart:
            assert entered.wait(10)
            assert request(unused) == 'removing'
            release.set()
        assert purged.wait(10)
    finally:
        release.set(); worker.stop()
    assert_removed(unused)


@pytest.mark.parametrize('failure', ['rename', 'delete', 'commit'])
def test_storage_failures_reconcile_source_with_database(unused, monkeypatch, failure):
    create(unused)
    with monkeypatch.context() as patch:
        if failure == 'rename':
            patch.setattr(artifacts.os, 'rename', lambda *a: (_ for _ in ()).throw(OSError('synthetic private detail')))
        else:
            original = discard.database_session
            @contextmanager
            def failing(dsn):
                deleting = False
                with original(dsn) as session:
                    execute = session.execute
                    def checked(statement, *args, **kwargs):
                        nonlocal deleting
                        if str(statement).startswith('DELETE FROM artifacts'):
                            deleting = True
                            if failure == 'delete': raise RuntimeError('synthetic private SQL detail')
                        return execute(statement, *args, **kwargs)
                    session.execute = checked
                    yield session
                if failure == 'commit' and deleting: raise RuntimeError('simulated lost commit acknowledgement')
            patch.setattr(discard, 'database_session', failing)
        with pytest.raises(discard.MaterialDiscardError, match='MATERIAL_DISCARD_STORAGE_FAILED'): request(unused)
    if failure == 'commit':
        assert_removed(unused)
    else:
        with artifacts.open_verified_source_pdf(unused.learner.learner_id, unused.source.artifact_id, dsn=unused.dsn) as source:
            assert source.file.read()
        assert not list((unused.root / '.trash').iterdir())
        assert request(unused) == 'removed'
        assert_removed(unused)


class SimulatedProcessCrash(BaseException): pass


@pytest.mark.parametrize('committed', [False, True])
def test_crash_quarantine_is_reconciled_after_restart(unused, monkeypatch, committed):
    quarantined=[]
    with monkeypatch.context() as patch:
        if committed:
            patch.setattr(discard, 'reconcile_discarded_sources', lambda **kw: (_ for _ in ()).throw(SimulatedProcessCrash()))
        else:
            original = discard.quarantine_source_pdf
            def crash(session, identity):
                original(session, identity)
                quarantined.append(identity)
                raise SimulatedProcessCrash()
            patch.setattr(discard, 'quarantine_source_pdf', crash)
        with pytest.raises(SimulatedProcessCrash): request(unused)
    moved=unused.source.artifact_id if committed else quarantined[0]
    assert (unused.root / '.trash' / moved.hex).exists()
    assert not (unused.root / 'objects' / moved.hex).exists()
    artifacts.reconcile_discarded_sources(dsn=unused.dsn)
    if committed: assert_removed(unused)
    else:
        with artifacts.open_verified_source_pdf(unused.learner.learner_id, unused.source.artifact_id, dsn=unused.dsn) as source: assert source.file.read()
        discard.finish_material_discards(dsn=unused.dsn)
        assert_removed(unused)


def test_unlink_failure_is_private_and_retried(unused, monkeypatch):
    original = Path.unlink
    with monkeypatch.context() as patch:
        def fail_trash(path, *a, **kw):
            if path.parent.name == '.trash': raise OSError('synthetic unlink failure')
            return original(path, *a, **kw)
        patch.setattr(Path, 'unlink', fail_trash)
        with pytest.raises(discard.MaterialDiscardError, match='MATERIAL_DISCARD_STORAGE_FAILED'): request(unused)
    assert (unused.root / '.trash' / unused.source.artifact_id.hex).exists()
    discard.finish_material_discards(dsn=unused.dsn)
    assert_removed(unused)


def test_discard_transport_owner_contract_and_cancel_surface_removed(unused, monkeypatch):
    monkeypatch.setattr(api_app, 'runtime_binding', lambda _: {})
    app = api_app.create_app(api_app.ApiSettings(profile='local', public_origin=ORIGIN, secure_cookie=False, local_config=unused.settings, dsn=unused.dsn))
    client = TestClient(app, base_url=ORIGIN)
    url = f'/v1/materials/{unused.source.material_id}'
    headers = {'Origin': ORIGIN}
    assert client.delete(url, headers=headers).status_code == 401
    client.cookies.set('studydy_session', unused.token)
    assert client.delete(url).status_code == 403
    assert client.delete(url, headers={'Origin': 'http://wrong.invalid'}).status_code == 403
    for body in (b'{}', b'null', b' '): assert client.request('DELETE', url, content=body, headers=headers).status_code == 400
    assert client.delete(url+'?x=1', headers=headers).status_code == 400
    foreign = register_account('foreign_discard@example.com', 'Synthetic password for tests 42', dsn=unused.dsn)
    client.cookies.set('studydy_session', foreign.raw_token)
    response = client.delete(url, headers=headers)
    assert response.status_code == 404 and response.json()['reason_code'] == 'RESOURCE_NOT_FOUND'
    client.cookies.set('studydy_session', unused.token)
    published = client.delete(f'/v1/materials/{unused.original_source.material_id}', headers=headers)
    assert published.status_code == 202 and published.json()['state'] == 'removed'
    run = create(unused); claim(unused)
    response = client.delete(url, headers=headers)
    assert response.status_code == 202 and response.json() == {'schema': 'material-discard/v1', 'material_id': str(unused.source.material_id), 'state': 'removing'}
    assert client.delete(url, headers=headers).json() == response.json()
    assert client.post(f'/v1/material-processing-runs/{run.run_id}/cancel', headers=headers).status_code == 404
    processing._record_failure(run.run_id, 'IN_FLIGHT_ERROR', dsn=unused.dsn)
    assert read(unused, run).status == 'cancelled'
    removed = client.delete(url, headers=headers)
    assert removed.status_code == 202 and removed.json()['state'] == 'removed'
    assert client.delete(url, headers=headers).status_code == 404
    assert not (unused.root / "objects" / unused.source.artifact_id.hex).exists()
    assert not (unused.root / "objects" / unused.original_source.artifact_id.hex).exists()
    schema = app.openapi()
    assert '/v1/material-processing-runs/{run_id}/cancel' not in schema['paths']
    operation = schema['paths']['/v1/materials/{material_id}']['delete']
    assert operation['security'] == [{'CookieSession': []}]
    assert any(p['name'] == 'Origin' and p['required'] for p in operation['parameters'])
    assert not any(p['name'] == 'Idempotency-Key' for p in operation['parameters'])
    assert 'requestBody' not in operation and '409' in operation['responses']


def test_discard_api_filesystem_failure_is_safe_503_and_retryable(unused, monkeypatch):
    monkeypatch.setattr(api_app, 'runtime_binding', lambda _: {})
    app = api_app.create_app(api_app.ApiSettings(profile='local', public_origin=ORIGIN, secure_cookie=False, local_config=unused.settings, dsn=unused.dsn))
    client = TestClient(app, base_url=ORIGIN)
    client.cookies.set('studydy_session', unused.token)
    with monkeypatch.context() as patch:
        patch.setattr(artifacts.os, 'rename', lambda *a: (_ for _ in ()).throw(OSError('private-file-details')))
        response = client.delete(f'/v1/materials/{unused.source.material_id}', headers={'Origin': ORIGIN})
    assert response.status_code == 503 and response.json()['reason_code'] == 'STORAGE_UNAVAILABLE'
    assert response.json()['retryable'] is True and 'private-file-details' not in response.text
    with artifacts.open_verified_source_pdf(unused.learner.learner_id, unused.source.artifact_id, dsn=unused.dsn) as source: assert source.file.read()
    assert client.delete(f'/v1/materials/{unused.source.material_id}', headers={'Origin': ORIGIN}).json()['state'] == 'removed'
    assert_removed(unused)


def test_0006_adds_only_nullable_intent_and_preserves_old_checksums(clean_database_dsn, migrations_dir, tmp_path):
    old = tmp_path / 'old-migrations'; old.mkdir()
    for path in migrations_dir.glob('*.sql'):
        if int(path.name[:4]) <= 5: (old / path.name).write_bytes(path.read_bytes())
    assert run_migrations(clean_database_dsn, migrations_dir=old) == (1, 2, 3, 4, 5)
    with psycopg.connect(clean_database_dsn) as c:
        c.execute('SET CONSTRAINTS ALL DEFERRED')
        learner, mid, aid, rid = uuid4(), uuid4(), uuid4(), uuid4()
        c.execute('INSERT INTO learners VALUES (%s,now())', (learner,))
        c.execute('INSERT INTO materials(material_id,learner_id,source_artifact_id,upload_idempotency_key_sha256,upload_request_fingerprint,created_at) VALUES (%s,%s,%s,%s,%s,now())', (mid,learner,aid,bytes(32),bytes(32)))
        c.execute("INSERT INTO artifacts VALUES (%s,%s,%s,'source_pdf','application/pdf',%s,1,now())", (aid,learner,mid,bytes(32)))
        c.execute("INSERT INTO material_processing_runs(run_id,learner_id,material_id,source_artifact_id,idempotency_key_sha256,request_fingerprint,runtime_binding,status,progress_stage,created_at,updated_at,completed_at,cancel_requested_at) VALUES (%s,%s,%s,%s,%s,%s,'{}','cancelled','queued',now(),now(),now(),now())", (rid,learner,mid,aid,bytes(32),bytes(32)))
        before = c.execute('SELECT row_to_json(m) FROM materials m').fetchone()[0]
        run_before = c.execute('SELECT row_to_json(r) FROM material_processing_runs r').fetchone()[0]
        checksums = c.execute('SELECT version,sql_sha256 FROM schema_migrations ORDER BY version').fetchall()
    through_six=tmp_path/"through-six";through_six.mkdir()
    for path in migrations_dir.glob("*.sql"):
        if int(path.name[:4])<=6:(through_six/path.name).write_bytes(path.read_bytes())
    assert run_migrations(clean_database_dsn,migrations_dir=through_six) == (6,)
    assert run_migrations(clean_database_dsn,migrations_dir=through_six) == ()
    with psycopg.connect(clean_database_dsn) as c:
        after = c.execute('SELECT row_to_json(m) FROM materials m').fetchone()[0]
        assert after.pop('discard_requested_at') is None and after == before
        assert c.execute('SELECT row_to_json(r) FROM material_processing_runs r').fetchone()[0] == run_before
        assert c.execute('SELECT version,sql_sha256 FROM schema_migrations WHERE version<=5 ORDER BY version').fetchall() == checksums
    for invalid in [
        "cancel_requested_at=NULL", "completed_at=NULL", "error_code='ERROR'", "output_binding='{}'",
        "status='running',completed_at=NULL,progress_stage='publishing'",
        "status='pending',completed_at=NULL", "status='failed',error_code='ERROR'",
        "status='succeeded',progress_stage='completed',output_binding='{}'",
    ]:
        with psycopg.connect(clean_database_dsn) as c:
            with pytest.raises(psycopg.errors.CheckViolation): c.execute('UPDATE material_processing_runs SET '+invalid)

def test_active_run_is_cancelled_and_new_revision_cannot_start(unused):
    run=create(unused);claim(unused)
    with pytest.raises(SourceError,match='REVISION_IN_PROGRESS'):create(unused,'second')
    assert request(unused)=='removing'
    assert read(unused,run).cancel_requested_at is not None
    with pytest.raises(SourceError,match='MATERIAL_NOT_DISCARDABLE'):create(unused,'after-discard')
    with pytest.raises(processing.MaterialProcessingCancelled):
        processing._record_progress(run.run_id,'publishing',1,1,dsn=unused.dsn)
    assert discard.purge_discarded_material(unused.learner.learner_id,unused.source.material_id,dsn=unused.dsn)
    assert_removed(unused)
