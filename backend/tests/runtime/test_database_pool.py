import pytest
from sqlalchemy import text

from runtime.storage.tables import database_session, dispose_database_engine


def test_sessions_reuse_connection_without_sharing_transactions(clean_database_dsn):
    dsn = clean_database_dsn
    with database_session(dsn) as db:
        first_pid = db.scalar(text('SELECT pg_backend_pid()'))
        db.execute(text('CREATE TABLE pool_probe (value integer)'))
        db.execute(text('INSERT INTO pool_probe VALUES (1)'))
    with pytest.raises(RuntimeError, match='rollback'):
        with database_session(dsn) as db:
            assert db.scalar(text('SELECT pg_backend_pid()')) == first_pid
            db.execute(text('INSERT INTO pool_probe VALUES (2)'))
            db.execute(text("SET LOCAL statement_timeout = '1ms'"))
            raise RuntimeError('rollback')
    with database_session(dsn) as db:
        assert db.scalar(text('SELECT pg_backend_pid()')) == first_pid
        assert db.scalars(text('SELECT value FROM pool_probe')).all() == [1]
        assert db.scalar(text('SHOW statement_timeout')) == '1min'
        assert db.scalar(text('SHOW lock_timeout')) == '5s'


def test_disposing_one_environment_allows_a_new_connection(clean_database_dsn):
    dsn = clean_database_dsn
    with database_session(dsn) as db:
        first_pid = db.scalar(text('SELECT pg_backend_pid()'))
    dispose_database_engine(dsn)
    with database_session(dsn) as db:
        assert db.scalar(text('SELECT pg_backend_pid()')) != first_pid
