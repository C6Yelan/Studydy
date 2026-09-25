from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from hashlib import scrypt

from fastapi.testclient import TestClient
import psycopg
import pytest

from runtime.learner_session import (
    SessionError, login_account, refresh_session, register_account,
    resolve_session, revoke_session,
)
from runtime.storage.migrations import run_migrations
from product_fixtures import HEADERS, ORIGIN, _app

PASSWORD = "Synthetic account password 42"
def test_credentials_are_salted_unique_and_registration_is_atomic(clean_database_dsn):
    run_migrations(clean_database_dsn)
    first = register_account("First_User@example.com", PASSWORD, dsn=clean_database_dsn)
    register_account("second_user@example.com", PASSWORD, dsn=clean_database_dsn)
    with psycopg.connect(clean_database_dsn) as connection:
        hashes = [row[0] for row in connection.execute("SELECT password_hash FROM learners")]
    assert len(set(hashes)) == 2
    assert all(value.startswith("scrypt$16384$8$5$") and PASSWORD not in value for value in hashes)
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


def test_existing_scrypt_hash_is_upgraded_after_login(clean_database_dsn):
    run_migrations(clean_database_dsn)
    created = register_account("existing@example.com", PASSWORD, dsn=clean_database_dsn)
    salt = bytes(range(16))
    digest = scrypt(PASSWORD.encode(), salt=salt, n=2**17, r=8, p=1,
                    maxmem=256 * 1024 * 1024, dklen=32)
    previous_hash = f"scrypt$131072$8$1${salt.hex()}${digest.hex()}"
    with psycopg.connect(clean_database_dsn) as connection:
        connection.execute(
            "UPDATE learners SET password_hash=%s WHERE learner_id=%s",
            (previous_hash, created.learner_id),
        )

    assert login_account("existing@example.com", PASSWORD, dsn=clean_database_dsn).learner_id == created.learner_id
    with psycopg.connect(clean_database_dsn) as connection:
        upgraded = connection.execute(
            "SELECT password_hash FROM learners WHERE learner_id=%s", (created.learner_id,),
        ).fetchone()[0]
    assert upgraded.startswith("scrypt$16384$8$5$") and upgraded != previous_hash
    assert login_account("existing@example.com", PASSWORD, dsn=clean_database_dsn).learner_id == created.learner_id


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
    # DB 約束 idle_expires_at <= absolute_expires_at；絕對期限過期時閒置期限也已過期。
    current = login_account("learner@example.com", PASSWORD, dsn=clean_database_dsn)
    with psycopg.connect(clean_database_dsn) as connection:
        connection.execute(
            "UPDATE learner_sessions SET created_at=now()-interval '40 days', "
            "idle_expires_at=now()-interval '1 day', absolute_expires_at=now()+interval '1 day' "
            "WHERE revoked_at IS NULL"
        )
    assert refresh_session(current.raw_token, dsn=clean_database_dsn) is None
    assert resolve_session(current.raw_token, dsn=clean_database_dsn) is None
    assert login_account("learner@example.com", PASSWORD, dsn=clean_database_dsn).learner_id == created.learner_id
    assert refresh_session("invalid-token", dsn=clean_database_dsn) is None


# EmailStr 負責語法細節；此處驗證空值／非空錯誤轉成產品錯誤，且兩個入口都不碰儲存。
@pytest.mark.parametrize("email", ["", "not-an-email"], ids=["empty", "invalid-syntax"])
def test_invalid_email_is_rejected_before_storage(email, monkeypatch):
    import runtime.learner_session as sessions
    monkeypatch.setattr(sessions, "database_session", lambda *_: pytest.fail("Invalid Email must not reach storage"))
    for action in (register_account, login_account):
        with pytest.raises(SessionError, match="REQUEST_INVALID"):
            action(email, PASSWORD)


@pytest.mark.parametrize("password", ["short", "x" * 129])
def test_password_constraints_remain_enforced(password, monkeypatch):
    import runtime.learner_session as sessions
    monkeypatch.setattr(sessions, "database_session", lambda *_: pytest.fail("Invalid password must not reach storage"))
    for action in (register_account, login_account):
        with pytest.raises(SessionError, match="REQUEST_INVALID"):
            action("learner@example.com", password)


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


def test_refresh_returns_the_already_verified_identity_without_replacing_session(clean_database_dsn,tmp_path,monkeypatch):
    run_migrations(clean_database_dsn)
    client=TestClient(_app(clean_database_dsn,tmp_path,monkeypatch),base_url=ORIGIN)
    created=client.post('/v1/accounts',headers=HEADERS,json={'email':'refresh@example.com','password':PASSWORD})
    assert created.status_code==201
    cookie=client.cookies.get('studydy_session')
    refreshed=client.post('/v1/session/refresh',headers=HEADERS)
    assert refreshed.status_code==200 and refreshed.json()==created.json()
    assert refreshed.json()['schema']=='learner-identity/v1'
    assert client.cookies.get('studydy_session')==cookie
    with psycopg.connect(clean_database_dsn) as connection:
        assert connection.execute('SELECT count(*) FROM learner_sessions').fetchone()==(1,)
    client.delete('/v1/session',headers=HEADERS)
    assert client.post('/v1/session/refresh',headers=HEADERS).status_code==401
