"""帳號自助：修改密碼、登出其他裝置與有效登入數（真 API／隔離 DB）。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
import psycopg
import pytest

from runtime.learner_session import (
    PasswordAttemptLimiter, SessionError, change_password, login_account,
    register_account, resolve_session,
)
from runtime.storage.migrations import run_migrations
from product_fixtures import HEADERS, ORIGIN, _app

PASSWORD = "Synthetic account password 42"
NEW_PASSWORD = "Another synthetic password 77"
EMAIL = "security_user@example.com"


def _client(app, token=None):
    client = TestClient(app, base_url=ORIGIN)
    if token:
        client.cookies.set("studydy_session", token)
    return client


def _change(client, current, new, headers=HEADERS):
    return client.post("/v1/account/password", headers=headers, json={
        "schema": "password-change/v1", "current_password": current, "new_password": new,
    })


@pytest.fixture
def app(clean_database_dsn, tmp_path, monkeypatch):
    run_migrations(clean_database_dsn)
    return _app(clean_database_dsn, tmp_path, monkeypatch)


def test_password_change_rejects_wrong_or_invalid_input_without_writing(app, clean_database_dsn):
    dsn = clean_database_dsn
    current = register_account(EMAIL, PASSWORD, dsn=dsn)
    other = login_account(EMAIL, PASSWORD, dsn=dsn)
    client = _client(app, current.raw_token)
    with psycopg.connect(dsn) as connection:
        before = connection.execute("SELECT password_hash FROM learners").fetchone()

    wrong = _change(client, "Wrong synthetic password 00", NEW_PASSWORD)
    assert wrong.status_code == 400
    assert wrong.json()["reason_code"] == "CURRENT_PASSWORD_INVALID"
    assert "Wrong synthetic" not in wrong.text and NEW_PASSWORD not in wrong.text
    # 新密碼沿用註冊規則（15–128 字元），不合規則不核對目前密碼。
    for invalid in ["short pass", "x" * 129]:
        response = _change(client, PASSWORD, invalid)
        assert response.status_code == 400
        assert response.json()["reason_code"] == "REQUEST_INVALID"
    assert _change(client, "", NEW_PASSWORD).status_code == 400
    assert client.post("/v1/account/password", headers=HEADERS, json={
        "current_password": PASSWORD, "new_password": NEW_PASSWORD,
    }).status_code == 400
    assert client.post("/v1/account/password", headers=HEADERS, json={
        "schema": "password-change/v1", "current_password": PASSWORD,
        "new_password": NEW_PASSWORD, "learner_id": str(current.learner_id),
    }).status_code == 400

    with psycopg.connect(dsn) as connection:
        assert connection.execute("SELECT password_hash FROM learners").fetchone() == before
        assert connection.execute(
            "SELECT count(*) FROM learner_sessions WHERE revoked_at IS NULL").fetchone() == (2,)
    assert resolve_session(other.raw_token, dsn=dsn) is not None
    assert login_account(EMAIL, PASSWORD, dsn=dsn)


def test_password_change_keeps_current_session_and_signs_out_other_devices(app, clean_database_dsn):
    dsn = clean_database_dsn
    current = register_account(EMAIL, PASSWORD, dsn=dsn)
    others = [login_account(EMAIL, PASSWORD, dsn=dsn) for _ in range(2)]
    stranger = register_account("stranger@example.com", PASSWORD, dsn=dsn)
    client = _client(app, current.raw_token)
    assert client.get("/v1/account/sessions").json() == {"schema": "account-sessions/v1", "active_sessions": 3}

    changed = _change(client, PASSWORD, NEW_PASSWORD)
    assert changed.status_code == 200, changed.json()
    assert changed.json() == {"schema": "account-sessions/v1", "active_sessions": 1}
    # 目前 session 沿用原 token，不另發 cookie。
    assert "set-cookie" not in changed.headers
    assert client.get("/v1/session").json()["learner_id"] == str(current.learner_id)
    for other in others:
        assert resolve_session(other.raw_token, dsn=dsn) is None
        assert _client(app, other.raw_token).get("/v1/profile").status_code == 401
    # 別的帳號不受影響。
    assert resolve_session(stranger.raw_token, dsn=dsn) is not None

    with pytest.raises(SessionError, match="INVALID_CREDENTIALS"):
        login_account(EMAIL, PASSWORD, dsn=dsn)
    assert login_account(EMAIL, NEW_PASSWORD, dsn=dsn).learner_id == current.learner_id
    with psycopg.connect(dsn) as connection:
        stored = connection.execute(
            "SELECT password_hash FROM learners WHERE learner_id = %s", (current.learner_id,)).fetchone()[0]
    assert stored.startswith("scrypt$16384$8$5$") and NEW_PASSWORD not in stored
    assert client.get("/v1/account/sessions").json()["active_sessions"] == 2


def test_sign_out_other_devices_keeps_current_and_is_owner_scoped(app, clean_database_dsn):
    dsn = clean_database_dsn
    current = register_account(EMAIL, PASSWORD, dsn=dsn)
    other = login_account(EMAIL, PASSWORD, dsn=dsn)
    stranger = register_account("stranger@example.com", PASSWORD, dsn=dsn)
    stranger_other = login_account("stranger@example.com", PASSWORD, dsn=dsn)
    client = _client(app, current.raw_token)
    assert client.get("/v1/account/sessions").json()["active_sessions"] == 2
    assert client.get("/v1/account/sessions", params={"learner_id": str(stranger.learner_id)}).status_code == 400

    response = client.delete("/v1/account/sessions/others", headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == {"schema": "account-sessions/v1", "active_sessions": 1}
    assert resolve_session(current.raw_token, dsn=dsn) is not None
    assert resolve_session(other.raw_token, dsn=dsn) is None
    assert resolve_session(stranger.raw_token, dsn=dsn) is not None
    assert resolve_session(stranger_other.raw_token, dsn=dsn) is not None
    # 冪等：再按一次仍只剩目前這個。
    assert client.delete("/v1/account/sessions/others", headers=HEADERS).json()["active_sessions"] == 1
    # 已撤銷的裝置不能反過來登出目前裝置。
    assert _client(app, other.raw_token).delete("/v1/account/sessions/others", headers=HEADERS).status_code == 401
    assert resolve_session(current.raw_token, dsn=dsn) is not None


def test_account_routes_require_session_origin_and_empty_body(app, clean_database_dsn):
    current = register_account(EMAIL, PASSWORD, dsn=clean_database_dsn)
    other = login_account(EMAIL, PASSWORD, dsn=clean_database_dsn)
    anonymous = _client(app)
    assert anonymous.get("/v1/account/sessions").json()["reason_code"] == "SESSION_REQUIRED"
    assert anonymous.delete("/v1/account/sessions/others", headers=HEADERS).status_code == 401
    assert _change(anonymous, PASSWORD, NEW_PASSWORD).status_code == 401

    client = _client(app, current.raw_token)
    for headers in ({}, {"Origin": "https://attacker.test"}):
        assert _change(client, PASSWORD, NEW_PASSWORD, headers=headers).json()["reason_code"] == "ORIGIN_NOT_ALLOWED"
        assert client.delete("/v1/account/sessions/others", headers=headers).status_code == 403
    assert client.request("DELETE", "/v1/account/sessions/others", headers=HEADERS, content=b"{}").status_code == 400
    assert resolve_session(other.raw_token, dsn=clean_database_dsn) is not None
    assert login_account(EMAIL, PASSWORD, dsn=clean_database_dsn)


def test_wrong_current_password_is_throttled_per_account(app, clean_database_dsn):
    current = register_account(EMAIL, PASSWORD, dsn=clean_database_dsn)
    stranger = register_account("stranger@example.com", PASSWORD, dsn=clean_database_dsn)
    client = _client(app, current.raw_token)
    for _ in range(5):
        assert _change(client, "Wrong synthetic password 00", NEW_PASSWORD).status_code == 400
    blocked = _change(client, PASSWORD, NEW_PASSWORD)
    assert blocked.status_code == 429
    assert blocked.json()["reason_code"] == "TOO_MANY_ATTEMPTS" and blocked.json()["retryable"] is True
    # 暫停期間正確密碼也不受理；其他帳號不受影響。
    assert login_account(EMAIL, PASSWORD, dsn=clean_database_dsn)
    assert _change(_client(app, stranger.raw_token), PASSWORD, NEW_PASSWORD).status_code == 200


def test_attempt_limiter_window_and_reset():
    now = [datetime(2026, 10, 22, tzinfo=UTC)]
    limiter = PasswordAttemptLimiter(limit=2, window=timedelta(minutes=15), clock=lambda: now[0])
    learner = uuid4()
    limiter.record_failure(learner)
    assert not limiter.blocked(learner)
    now[0] += timedelta(minutes=10)
    limiter.record_failure(learner)
    assert limiter.blocked(learner)
    # 最早一次失敗超出視窗後恢復。
    now[0] += timedelta(minutes=6)
    assert not limiter.blocked(learner)
    limiter.record_failure(learner)
    assert limiter.blocked(learner)
    limiter.reset(learner)
    assert not limiter.blocked(learner)


def test_concurrent_password_changes_from_two_devices_do_not_deadlock(clean_database_dsn):
    dsn = clean_database_dsn
    run_migrations(dsn)
    first = register_account(EMAIL, PASSWORD, dsn=dsn)
    second = login_account(EMAIL, PASSWORD, dsn=dsn)

    def attempt(token):
        try:
            return change_password(token, PASSWORD, NEW_PASSWORD, dsn=dsn)
        except SessionError as error:
            return str(error)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = sorted(map(str, pool.map(attempt, [first.raw_token, second.raw_token])))
    # 先完成的一方撤銷另一方；後到者因 session 已失效被拒，不會兩邊都保留。
    assert results == ["1", "SESSION_REQUIRED"]
    alive = [resolve_session(token.raw_token, dsn=dsn) is not None for token in (first, second)]
    assert alive.count(True) == 1
