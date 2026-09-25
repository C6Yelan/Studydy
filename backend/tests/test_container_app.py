import pytest
from psycopg.conninfo import conninfo_to_dict

from runtime import container_app


def test_data_initialization_preserves_files_and_rejects_symlink(tmp_path, monkeypatch):
    monkeypatch.setattr(container_app.os, 'chown', lambda *_: None)
    root = tmp_path / 'data'
    container_app.initialize_data(root, 1000, 1000)
    saved = root / 'artifacts' / 'saved.pdf'
    saved.write_bytes(b'synthetic source')
    container_app.initialize_data(root, 1000, 1000)
    assert saved.read_bytes() == b'synthetic source'
    assert (root / 'artifacts').stat().st_mode & 0o777 == 0o700

    other = tmp_path / 'other'
    other.mkdir()
    (other / 'artifacts').symlink_to(root / 'artifacts', target_is_directory=True)
    with pytest.raises(ValueError, match='CONTAINER_DATA_INVALID'):
        container_app.initialize_data(other, 1000, 1000)
    assert saved.read_bytes() == b'synthetic source'


def test_database_password_is_quoted_without_becoming_connection_options(monkeypatch):
    monkeypatch.setenv('STUDYDY_DATABASE_DSN', 'replaced-by-test')
    password = "synthetic ' space \\ host=other"
    container_app.configure_database({
        'POSTGRES_DB': 'studydy_test', 'POSTGRES_USER': 'tester', 'POSTGRES_PASSWORD': password,
    })
    fields = conninfo_to_dict(container_app.os.environ['STUDYDY_DATABASE_DSN'])
    assert fields['host'] == 'postgres'
    assert fields['password'] == password


def test_invalid_endpoint_stops_before_database_and_errors_do_not_leak(monkeypatch, capsys):
    monkeypatch.setattr('sys.argv', ['container_app', 'serve'])
    monkeypatch.setenv('STUDYDY_SEMANTIC_BASE_URL', 'http://user:synthetic-secret@model')
    monkeypatch.setattr(container_app, 'run_migrations', lambda: pytest.fail('invalid config must not migrate'))
    with pytest.raises(SystemExit) as failure:
        container_app.main()
    assert failure.value.code == 1
    output = capsys.readouterr().out
    assert 'CONTAINER_OPERATION_FAILED' in output
    assert 'synthetic-secret' not in output


def test_sandbox_failure_is_reported(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(container_app.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=1))
    with pytest.raises(RuntimeError, match='CONTAINER_SANDBOX_UNAVAILABLE'):
        container_app.check_sandbox()
