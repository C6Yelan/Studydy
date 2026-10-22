"""以隔離 PostgreSQL 驗證初始化標記、非空資料庫保護與中斷續跑。"""

import psycopg
import pytest

from runtime.storage import baseline
from runtime.storage.migrations import load_migrations, run_migrations


def test_fresh_initialization_records_marker(clean_database_dsn):
    run_migrations(clean_database_dsn)
    with psycopg.connect(clean_database_dsn) as connection:
        assert baseline.read_marker(connection)[:3] == (
            baseline.BASELINE_ID,
            baseline.prefix_digest(load_migrations()),
            "fresh",
        )


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
