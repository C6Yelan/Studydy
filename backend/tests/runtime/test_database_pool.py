import pytest
from sqlalchemy import text

from runtime.storage.database import database_ready, database_session, dispose_database_engine


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


def test_readiness_checks_real_database_and_rejects_unavailable_destination(clean_database_dsn):
    from psycopg.conninfo import make_conninfo
    from socket import socket

    assert database_ready(clean_database_dsn)
    # 綁定但不 listen 的本機 port 確保失敗目的地不是另一項服務。
    with socket() as unavailable:
        unavailable.bind(('127.0.0.1', 0))
        assert not database_ready(make_conninfo(
            host='127.0.0.1', port=unavailable.getsockname()[1], dbname='studydy_test',
        ))


def test_readiness_does_not_log_connection_details(monkeypatch, capsys, caplog):
    import psycopg
    from runtime.storage import database

    def failed(*args, **kwargs):
        raise psycopg.OperationalError('synthetic-secret from DSN')

    monkeypatch.setattr(database.psycopg, 'connect', failed)
    assert not database_ready('host=127.0.0.1 dbname=studydy_test')
    assert not caplog.text
    captured = capsys.readouterr()
    assert not captured.out and not captured.err
