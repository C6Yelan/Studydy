"""Baseline 接軌必須核對真實結構與資料，而非只替換版本數。"""

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from uuid import uuid4

import psycopg
import pytest

from content_identity import canonical_sha256
from migration_fixtures import create_legacy_database
from product_fixtures import seed_pdf, _pdf
from runtime.learner_session import register_account, resolve_session
from runtime.storage import baseline
from runtime.storage.migrations import load_migrations, run_migrations


@pytest.fixture
def legacy_copy(clean_database_dsn, tmp_path, monkeypatch):
    create_legacy_database(clean_database_dsn)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(mode=0o700)
    monkeypatch.setenv("STUDYDY_ARTIFACT_ROOT", str(artifacts))
    account = register_account(
        "baseline@example.com", "Synthetic baseline password 42", dsn=clean_database_dsn
    )
    seed_pdf(
        account.learner_id, BytesIO(_pdf()), "baseline-source", dsn=clean_database_dsn
    )
    return clean_database_dsn, artifacts, account


def snapshot(dsn):
    with psycopg.connect(dsn) as connection:
        return baseline.data_snapshot(connection), connection.execute(
            "SELECT version,sql_sha256,applied_at FROM schema_migrations ORDER BY version"
        ).fetchall()


def test_check_adopt_repeat_and_restore_preserve_existing_data(legacy_copy):
    dsn, artifacts, account = legacy_copy
    before, ledger = snapshot(dsn)
    with pytest.raises(baseline.BaselineError, match="MIGRATION_BASELINE_REQUIRED"):
        run_migrations(dsn)
    checked = baseline.check_or_adopt(dsn, load_migrations(), artifacts)
    assert not checked["applied"] and checked["artifacts"] == 3
    assert snapshot(dsn) == (before, ledger)
    with psycopg.connect(dsn) as connection:
        assert baseline.read_marker(connection) is None
    adopted = baseline.check_or_adopt(dsn, load_migrations(), artifacts, apply=True)
    assert adopted["applied"] and snapshot(dsn)[0] == before
    assert run_migrations(dsn) == ()
    assert baseline.check_or_adopt(dsn, load_migrations(), artifacts, apply=True)[
        "already_adopted"
    ]
    assert snapshot(dsn)[0] == before
    assert baseline.restore_legacy_ledger(dsn, load_migrations())[
        "restored_legacy_ledger"
    ]
    assert snapshot(dsn) == (before, ledger)
    assert resolve_session(account.raw_token, dsn=dsn).learner_id == account.learner_id


def test_fresh_schema_matches_independent_legacy_fixture(clean_database_dsn):
    run_migrations(clean_database_dsn)
    with psycopg.connect(clean_database_dsn) as connection:
        assert (
            canonical_sha256(baseline.schema_snapshot(connection))
            == baseline.legacy_manifest()["schema_sha256"]
        )
        assert baseline.read_marker(connection)[2] == "fresh"


@pytest.mark.parametrize(
    "tamper,reason",
    [
        (
            "ALTER TABLE schema_migrations DROP CONSTRAINT schema_migrations_pkey",
            "BASELINE_SCHEMA_MISMATCH",
        ),
        (
            "ALTER TABLE artifacts DROP CONSTRAINT artifact_role_media",
            "BASELINE_SCHEMA_MISMATCH",
        ),
        (
            "ALTER TABLE materials DROP CONSTRAINT material_head_fk",
            "BASELINE_SCHEMA_MISMATCH",
        ),
        (
            "ALTER TABLE material_sources DISABLE TRIGGER source_identity_immutable",
            "BASELINE_SCHEMA_MISMATCH",
        ),
        (
            "DROP INDEX material_runs_pending; CREATE INDEX material_runs_pending ON material_processing_runs(created_at,run_id) WHERE status='failed'",
            "BASELINE_SCHEMA_MISMATCH",
        ),
        (
            "UPDATE schema_migrations SET sql_sha256=repeat('0',64) WHERE version=1",
            "BASELINE_LEGACY_LEDGER_MISMATCH",
        ),
        (
            "DELETE FROM schema_migrations WHERE version=2",
            "BASELINE_LEGACY_LEDGER_MISMATCH",
        ),
        (
            "INSERT INTO schema_migrations VALUES(9999,repeat('0',64),now())",
            "BASELINE_LEGACY_LEDGER_MISMATCH",
        ),
    ],
)
def test_same_ledger_or_similar_schema_is_not_enough(legacy_copy, tamper, reason):
    dsn, artifacts, _ = legacy_copy
    with psycopg.connect(dsn) as connection:
        connection.execute(tamper)
    before = snapshot(dsn)
    with pytest.raises(baseline.BaselineError, match=reason):
        baseline.check_or_adopt(dsn, load_migrations(), artifacts, apply=True)
    assert snapshot(dsn) == before
    with psycopg.connect(dsn) as connection:
        assert baseline.read_marker(connection) is None


def test_missing_artifact_prevents_adoption(legacy_copy):
    dsn, artifacts, _ = legacy_copy
    next((artifacts / "objects").iterdir()).unlink()
    before = snapshot(dsn)
    with pytest.raises(baseline.BaselineError, match="BASELINE_ARTIFACT_INVALID"):
        baseline.check_or_adopt(dsn, load_migrations(), artifacts, apply=True)
    assert snapshot(dsn) == before


def test_invalid_proposal_is_not_stamped_as_upgraded(legacy_copy):
    dsn, artifacts, account = legacy_copy
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "INSERT INTO topic_scopes(topic_id,learner_id,request,proposal,status,idempotency_key_sha256,created_at) VALUES(%s,%s,'synthetic','{}','ready',%s,now())",
            (uuid4(), account.learner_id, bytes(32)),
        )
    with pytest.raises(baseline.BaselineError, match="BASELINE_PROPOSAL_INVALID"):
        baseline.check_or_adopt(dsn, load_migrations(), artifacts, apply=True)


def test_failure_after_ledger_replacement_rolls_back_every_control_write(
    legacy_copy, monkeypatch
):
    dsn, artifacts, _ = legacy_copy
    before = snapshot(dsn)
    real = baseline.data_snapshot
    calls = []

    def interrupted(connection):
        calls.append(True)
        if len(calls) == 2:
            raise RuntimeError("synthetic interruption after ledger write")
        return real(connection)

    monkeypatch.setattr(baseline, "data_snapshot", interrupted)
    with pytest.raises(RuntimeError, match="synthetic interruption"):
        baseline.check_or_adopt(dsn, load_migrations(), artifacts, apply=True)
    monkeypatch.setattr(baseline, "data_snapshot", real)
    assert snapshot(dsn) == before
    with psycopg.connect(dsn) as connection:
        assert baseline.read_marker(connection) is None


def test_concurrent_adoption_has_one_receipt_and_no_replay(legacy_copy):
    dsn, artifacts, _ = legacy_copy
    before = snapshot(dsn)[0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: baseline.check_or_adopt(
                    dsn, load_migrations(), artifacts, apply=True
                ),
                range(2),
            )
        )
    assert sum(result.get("applied", False) for result in results) == 1
    assert sum(result.get("already_adopted", False) for result in results) == 1
    assert snapshot(dsn)[0] == before


def test_existing_unledgered_database_is_not_initialized(clean_database_dsn):
    with psycopg.connect(clean_database_dsn) as connection:
        connection.execute("CREATE TABLE retained_data(id integer)")
        connection.execute("INSERT INTO retained_data VALUES(42)")
    with pytest.raises(baseline.BaselineError, match="MIGRATION_DATABASE_NOT_EMPTY"):
        run_migrations(clean_database_dsn)
    with psycopg.connect(clean_database_dsn) as connection:
        assert connection.execute("SELECT * FROM retained_data").fetchall() == [(42,)]


def test_interrupted_fresh_prefix_resumes_in_the_same_generation(
    clean_database_dsn, monkeypatch
):
    from runtime.storage import migrations as loader

    real_connect = loader.connect_database
    fail_sql = load_migrations()[2].sql

    class InterruptedConnection:
        def __init__(self, connection):
            self.connection = connection

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def execute(self, statement, *args, **kwargs):
            if statement == fail_sql:
                raise psycopg.OperationalError("synthetic interruption")
            return self.connection.execute(statement, *args, **kwargs)

    monkeypatch.setattr(
        loader,
        "connect_database",
        lambda *args, **kwargs: InterruptedConnection(real_connect(*args, **kwargs)),
    )
    with pytest.raises(loader.MigrationSqlError):
        run_migrations(clean_database_dsn)
    with psycopg.connect(clean_database_dsn) as connection:
        assert baseline.read_marker(connection)[2] == "fresh"
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,)]
    monkeypatch.setattr(loader, "connect_database", real_connect)
    assert run_migrations(clean_database_dsn) == tuple(
        m.version for m in load_migrations()[2:]
    )
    assert run_migrations(clean_database_dsn) == ()


def test_adoption_table_locks_exclude_ordinary_writers(legacy_copy, monkeypatch):
    dsn, artifacts, _ = legacy_copy
    real_verify = baseline.verify_content
    attempts = []

    def verify_while_writer_waits(connection, root):
        with psycopg.connect(dsn) as writer:
            writer.execute("SET LOCAL lock_timeout='100ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                writer.execute(
                    "INSERT INTO learners(learner_id,created_at) VALUES(%s,now())",
                    (uuid4(),),
                )
            writer.rollback()
        attempts.append(True)
        return real_verify(connection, root)

    monkeypatch.setattr(baseline, "verify_content", verify_while_writer_waits)
    baseline.check_or_adopt(dsn, load_migrations(), artifacts, apply=True)
    assert len(attempts) == 2
    with psycopg.connect(dsn) as connection:
        assert connection.execute("SELECT count(*) FROM learners").fetchone() == (1,)


def test_adopted_marker_does_not_hide_catalog_corruption(legacy_copy):
    dsn, artifacts, _ = legacy_copy
    baseline.check_or_adopt(dsn, load_migrations(), artifacts, apply=True)
    with psycopg.connect(dsn) as connection:
        connection.execute("ALTER TABLE artifacts DROP CONSTRAINT artifact_role_media")
    with pytest.raises(baseline.BaselineError, match="BASELINE_SCHEMA_MISMATCH"):
        baseline.check_or_adopt(dsn, load_migrations(), artifacts, apply=True)
