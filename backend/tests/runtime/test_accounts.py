from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import shutil
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import psycopg
import pytest

import runtime.api.app as api_app
from runtime.learner_session import (
    SessionError, login_account, refresh_session, register_account,
    resolve_session, revoke_session,
)
from runtime.storage.migrations import run_migrations
from test_closed_loop_v1 import closed_loop, _settings, Client, _assessment_response

PASSWORD = "Synthetic account password 42"
ORIGIN = "https://studydy.test"
HEADERS = {"Origin": ORIGIN}


def _app(dsn, tmp_path, monkeypatch):
    # 帳號測試使用真 API/DB；只隔離與帳號無關的模型 preflight 與 worker。
    monkeypatch.setattr(api_app, "runtime_binding", lambda _: {})
    return api_app.create_app(api_app.ApiSettings(
        profile="test", public_origin=ORIGIN, secure_cookie=True,
        local_config=_settings(tmp_path), dsn=dsn,
    ))


def test_additive_migration_preserves_anonymous_owner_and_session(clean_database_dsn, migrations_dir, tmp_path):
    old = tmp_path / "accepted-migrations"
    old.mkdir()
    shutil.copyfile(migrations_dir / "0001_final_schema.sql", old / "0001_final_schema.sql")
    assert run_migrations(clean_database_dsn, migrations_dir=old) == (1,)
    learner_id = uuid4()
    old_session_id = uuid4()
    with psycopg.connect(clean_database_dsn) as connection:
        connection.execute("INSERT INTO learners (learner_id,created_at) VALUES (%s,now())", (learner_id,))
        connection.execute(
            "INSERT INTO learner_sessions VALUES (%s,%s,%s,now(),now()+interval '7 days',now()+interval '30 days',NULL,now())",
            (old_session_id, learner_id, bytes(32)),
        )
        old_session = connection.execute("SELECT * FROM learner_sessions WHERE session_id=%s", (old_session_id,)).fetchone()
    shutil.copyfile(migrations_dir / "0002_learner_credentials.sql", old / "0002_learner_credentials.sql")
    assert run_migrations(clean_database_dsn, migrations_dir=old) == (2,)
    assert run_migrations(clean_database_dsn, migrations_dir=old) == ()
    with psycopg.connect(clean_database_dsn) as connection:
        assert connection.execute("SELECT * FROM learner_sessions WHERE session_id=%s", (old_session_id,)).fetchone() == old_session
        assert connection.execute("SELECT username,password_hash FROM learners WHERE learner_id=%s", (learner_id,)).fetchone() == (None, None)


def test_credentials_are_salted_unique_and_registration_is_atomic(clean_database_dsn):
    run_migrations(clean_database_dsn)
    first = register_account("First_User@example.com", PASSWORD, dsn=clean_database_dsn)
    register_account("second_user@example.com", PASSWORD, dsn=clean_database_dsn)
    with psycopg.connect(clean_database_dsn) as connection:
        hashes = [row[0] for row in connection.execute("SELECT password_hash FROM learners")]
    assert len(set(hashes)) == 2
    assert all(value.startswith("scrypt$131072$8$1$") and PASSWORD not in value for value in hashes)
    assert login_account(" FIRST_USER@EXAMPLE.COM ", PASSWORD, dsn=clean_database_dsn).learner_id == first.learner_id
    for email in ("first_user@example.com", "absent_user@example.com"):
        with pytest.raises(SessionError, match="INVALID_CREDENTIALS"):
            login_account(email, "Wrong synthetic password", dsn=clean_database_dsn)

    def register():
        try:
            return register_account("same_name@example.com", PASSWORD, dsn=clean_database_dsn)
        except SessionError as error:
            return str(error)
    with ThreadPoolExecutor(max_workers=2) as pool:
        attempts = list(pool.map(lambda _: register(), range(2)))
    assert sum(attempt == "ACCOUNT_UNAVAILABLE" for attempt in attempts) == 1
    with psycopg.connect(clean_database_dsn) as connection:
        assert connection.execute("SELECT count(*) FROM learners").fetchone() == (3,)
        assert connection.execute("SELECT count(*) FROM learner_sessions").fetchone() == (4,)


def test_refresh_never_revives_expired_or_revoked_tokens(clean_database_dsn):
    run_migrations(clean_database_dsn)
    created = register_account("learner@example.com", PASSWORD, dsn=clean_database_dsn)
    assert refresh_session(created.raw_token, dsn=clean_database_dsn).learner_id == created.learner_id
    with psycopg.connect(clean_database_dsn) as connection:
        connection.execute("UPDATE learner_sessions SET absolute_expires_at=idle_expires_at")
    assert refresh_session(created.raw_token, dsn=clean_database_dsn).learner_id == created.learner_id
    with psycopg.connect(clean_database_dsn) as connection:
        assert connection.execute("SELECT idle_expires_at=absolute_expires_at FROM learner_sessions").fetchone() == (True,)
    assert revoke_session(created.raw_token, dsn=clean_database_dsn)
    assert revoke_session(created.raw_token, dsn=clean_database_dsn)
    assert resolve_session(created.raw_token, dsn=clean_database_dsn) is None
    assert refresh_session(created.raw_token, dsn=clean_database_dsn) is None
    for column in ("idle_expires_at", "absolute_expires_at"):
        current = login_account("learner@example.com", PASSWORD, dsn=clean_database_dsn)
        with psycopg.connect(clean_database_dsn) as connection:
            # 同時調整建立時間，保留既有 deadline constraints。
            connection.execute("UPDATE learner_sessions SET created_at=now()-interval '40 days', idle_expires_at=now()-interval '1 day', absolute_expires_at=now()+interval '1 day' WHERE revoked_at IS NULL")
            if column == "absolute_expires_at":
                connection.execute("UPDATE learner_sessions SET absolute_expires_at=now()-interval '1 day', idle_expires_at=now()-interval '2 days' WHERE revoked_at IS NULL")
        assert refresh_session(current.raw_token, dsn=clean_database_dsn) is None
        assert resolve_session(current.raw_token, dsn=clean_database_dsn) is None
        assert login_account("learner@example.com", PASSWORD, dsn=clean_database_dsn).learner_id == created.learner_id
    assert refresh_session("invalid-token", dsn=clean_database_dsn) is None




def test_email_cutover_retires_old_credentials_and_sessions_without_deleting_owned_data(clean_database_dsn, migrations_dir, tmp_path):
    """一次性清除舊登入資料；owner、教材及已撤銷 session 的歷史仍保留。"""
    from hashlib import sha256
    from runtime.learner_session import _encode_token

    previous = tmp_path / "before-email"
    previous.mkdir()
    for name in ("0001_final_schema.sql", "0002_learner_credentials.sql", "0003_material_display_name.sql"):
        shutil.copyfile(migrations_dir / name, previous / name)
    assert run_migrations(clean_database_dsn, migrations_dir=previous) == (1, 2, 3)
    learner_id, session_id, material_id, artifact_id = (uuid4() for _ in range(4))
    old_token = bytes(range(32))
    with psycopg.connect(clean_database_dsn) as connection:
        connection.execute("INSERT INTO learners (learner_id,created_at,username,password_hash) VALUES (%s,now(),'old_account','old-fixture-hash')", (learner_id,))
        connection.execute("INSERT INTO learner_sessions VALUES (%s,%s,%s,now(),now()+interval '7 days',now()+interval '30 days',NULL,now())", (session_id, learner_id, sha256(old_token).digest()))
        connection.execute("INSERT INTO materials (material_id,learner_id,source_artifact_id,upload_idempotency_key_sha256,upload_request_fingerprint,created_at,display_name) VALUES (%s,%s,%s,%s,%s,now(),'Synthetic.pdf')", (material_id, learner_id, artifact_id, bytes(32), bytes(32)))
        connection.execute("INSERT INTO artifacts VALUES (%s,%s,%s,'source_pdf','application/pdf',%s,1,now())", (artifact_id, learner_id, material_id, bytes(32)))
        material = connection.execute("SELECT * FROM materials").fetchone()
        owner = connection.execute("SELECT learner_id,created_at FROM learners").fetchone()
    assert run_migrations(clean_database_dsn) == (4, 5, 6, 7, 8, 9, 10, 11, 12, 13)
    with psycopg.connect(clean_database_dsn) as connection:
        columns = {row[0] for row in connection.execute("SELECT column_name FROM information_schema.columns WHERE table_name='learners'")}
        assert "email" in columns and "username" not in columns
        assert connection.execute("SELECT email,password_hash FROM learners").fetchone() == (None, None)
        assert connection.execute("SELECT learner_id,created_at FROM learners").fetchone() == owner
        assert connection.execute("SELECT material_id,learner_id,source_artifact_id,upload_idempotency_key_sha256,upload_request_fingerprint,created_at,display_name FROM materials").fetchone() == material
        assert connection.execute("SELECT discard_requested_at FROM materials").fetchone() == (None,)
        assert connection.execute("SELECT revoked_at IS NOT NULL FROM learner_sessions WHERE session_id=%s", (session_id,)).fetchone() == (True,)
    assert resolve_session(_encode_token(old_token), dsn=clean_database_dsn) is None
    fresh = register_account("new_account@example.com", PASSWORD, dsn=clean_database_dsn)
    assert fresh.learner_id != learner_id
    assert run_migrations(clean_database_dsn) == ()
    assert resolve_session(fresh.raw_token, dsn=clean_database_dsn).learner_id == fresh.learner_id


@pytest.mark.parametrize("email", ["", "not-an-email", "a@", "a b@example.com", "a..b@example.com", "a@example..com", "a@localhost"])
def test_invalid_email_is_rejected_before_storage(email, monkeypatch):
    import runtime.learner_session as sessions
    monkeypatch.setattr(sessions, "database_session", lambda *_: pytest.fail("Invalid Email must not reach storage"))
    for action in (register_account, login_account):
        with pytest.raises(SessionError, match="REQUEST_INVALID"):
            action(email, PASSWORD)


@pytest.mark.parametrize("password", ["", "short", "x" * 129])
def test_password_constraints_remain_enforced(password):
    for action in (register_account, login_account):
        with pytest.raises(SessionError, match="REQUEST_INVALID"):
            action("learner", password)


def test_email_normalization_and_syntax_validation_never_query_dns(clean_database_dsn, monkeypatch):
    import dns.resolver
    monkeypatch.setattr(dns.resolver.Resolver, "resolve", lambda *_args, **_kwargs: pytest.fail("Email identifier validation must not query DNS"))
    run_migrations(clean_database_dsn)
    created = register_account(" Learner+Tag@EXAMPLE.COM ", PASSWORD, dsn=clean_database_dsn)
    assert login_account("LEARNER+TAG@example.com", PASSWORD, dsn=clean_database_dsn).learner_id == created.learner_id
    with psycopg.connect(clean_database_dsn) as connection:
        assert connection.execute("SELECT email FROM learners").fetchone() == ("learner+tag@example.com",)
    with pytest.raises(SessionError, match="ACCOUNT_UNAVAILABLE"):
        register_account("learner+tag@Example.Com", PASSWORD, dsn=clean_database_dsn)
