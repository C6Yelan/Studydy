"""帳號自助：修改密碼、登入裝置清單與登出其他裝置（真 API／隔離 DB）。"""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

from fastapi.testclient import TestClient
import psycopg
import pytest

from runtime.learner_session import (
    LAST_USED_INTERVAL, PasswordAttemptLimiter, SessionError, change_password,
    device_label, login_account, register_account, resolve_session,
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


def _sessions(response):
    """回傳清單的 session_id；同時核對 schema 與目前這個排第一。"""
    body = response.json()
    assert response.status_code == 200, body
    assert body["schema"] == "account-sessions/v2"
    assert [item["current"] for item in body["sessions"]][:1] == [True]
    assert sum(item["current"] for item in body["sessions"]) == 1
    return [item["session_id"] for item in body["sessions"]]


def _session_id(dsn, token):
    digest = sha256(base64.urlsafe_b64decode(token + "=")).digest()
    with psycopg.connect(dsn) as connection:
        return str(connection.execute(
            "SELECT session_id FROM learner_sessions WHERE token_sha256 = %s", (digest,)).fetchone()[0])


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
    assert len(_sessions(client.get("/v1/account/sessions"))) == 3

    changed = _change(client, PASSWORD, NEW_PASSWORD)
    assert _sessions(changed) == [_session_id(dsn, current.raw_token)]
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
    assert len(_sessions(client.get("/v1/account/sessions"))) == 2


def test_sign_out_other_devices_keeps_current_and_is_owner_scoped(app, clean_database_dsn):
    dsn = clean_database_dsn
    current = register_account(EMAIL, PASSWORD, dsn=dsn)
    other = login_account(EMAIL, PASSWORD, dsn=dsn)
    stranger = register_account("stranger@example.com", PASSWORD, dsn=dsn)
    stranger_other = login_account("stranger@example.com", PASSWORD, dsn=dsn)
    client = _client(app, current.raw_token)
    assert len(_sessions(client.get("/v1/account/sessions"))) == 2
    assert client.get("/v1/account/sessions", params={"learner_id": str(stranger.learner_id)}).status_code == 400

    response = client.delete("/v1/account/sessions/others", headers=HEADERS)
    assert _sessions(response) == [_session_id(dsn, current.raw_token)]
    assert resolve_session(current.raw_token, dsn=dsn) is not None
    assert resolve_session(other.raw_token, dsn=dsn) is None
    assert resolve_session(stranger.raw_token, dsn=dsn) is not None
    assert resolve_session(stranger_other.raw_token, dsn=dsn) is not None
    # 冪等：再按一次仍只剩目前這個。
    assert len(_sessions(client.delete("/v1/account/sessions/others", headers=HEADERS))) == 1
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
        results = list(pool.map(attempt, [first.raw_token, second.raw_token]))
    # 先完成的一方撤銷另一方；後到者因 session 已失效被拒，不會兩邊都保留。
    assert sorted(str(len(r)) if isinstance(r, list) else r for r in results) == ["1", "SESSION_REQUIRED"]
    alive = [resolve_session(token.raw_token, dsn=dsn) is not None for token in (first, second)]
    assert alive.count(True) == 1


@pytest.mark.parametrize(("agent", "label"), [
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
     "Chrome/141.0.0.0 Safari/537.36", "Chrome · Windows"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
     "Chrome/141.0.0.0 Safari/537.36 Edg/141.0.0.0", "Edge · Windows"),
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 "
     "(KHTML, like Gecko) Version/18.6 Mobile/15E148 Safari/604.1", "Safari · iPhone"),
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 "
     "(KHTML, like Gecko) CriOS/141.0.0.0 Mobile/15E148 Safari/604.1", "Chrome · iPhone"),
    ("Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
     "Version/17.0 Mobile/15E148 Safari/604.1", "Safari · iPad"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
     "Version/18.6 Safari/605.1.15", "Safari · macOS"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:143.0) Gecko/20100101 Firefox/143.0",
     "Firefox · macOS"),
    ("Mozilla/5.0 (Linux; Android 15; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) "
     "SamsungBrowser/28.0 Chrome/130.0.0.0 Mobile Safari/537.36", "Samsung Internet · Android"),
    ("Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) "
     "Chrome/141.0.0.0 Mobile Safari/537.36", "Chrome · Android"),
    ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 "
     "Safari/537.36 OPR/122.0.0.0", "Opera · Linux"),
    ("Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) AppleWebKit/537.36 (KHTML, like Gecko) "
     "Chrome/141.0.0.0 Safari/537.36", "Chrome · ChromeOS"),
    # App 內建瀏覽器沒有 Version/，只顯示系統。
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 "
     "(KHTML, like Gecko) Mobile/15E148 Line/14.0.0", "iPhone"),
    ("python-requests/2.32", None),
    ("", None),
    (None, None),
])
def test_device_label_parses_browser_and_system(agent, label):
    assert device_label(agent) == label


CHROME_WINDOWS = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")


def test_device_list_shows_only_own_active_sessions_with_labels(app, clean_database_dsn):
    dsn = clean_database_dsn
    anonymous = _client(app)
    created = anonymous.post("/v1/accounts", headers={**HEADERS, "User-Agent": CHROME_WINDOWS},
                             json={"email": EMAIL, "password": PASSWORD})
    assert created.status_code == 201
    current = anonymous.cookies.get("studydy_session")
    phone = login_account(EMAIL, PASSWORD, dsn=dsn, label="Safari · iPhone")
    unknown = login_account(EMAIL, PASSWORD, dsn=dsn)
    revoked = login_account(EMAIL, PASSWORD, dsn=dsn)
    expired = login_account(EMAIL, PASSWORD, dsn=dsn)
    stranger = register_account("stranger@example.com", PASSWORD, dsn=dsn, label="Firefox · Linux")
    with psycopg.connect(dsn) as connection:
        # 只存解析後的標籤，不存原始 User-Agent。
        stored = [row[0] for row in connection.execute("SELECT device_label FROM learner_sessions")]
        assert "Chrome · Windows" in stored and CHROME_WINDOWS not in stored
        connection.execute("UPDATE learner_sessions SET revoked_at = now() WHERE session_id = %s",
                           (_session_id(dsn, revoked.raw_token),))
        connection.execute("UPDATE learner_sessions SET created_at = now() - interval '8 days', "
                           "idle_expires_at = now() - interval '1 second' "
                           "WHERE session_id = %s", (_session_id(dsn, expired.raw_token),))
        # 手機比未知裝置更近使用，清單依最後使用時間排序。
        connection.execute("UPDATE learner_sessions SET last_used_at = now() - interval '1 hour' "
                           "WHERE session_id = %s", (_session_id(dsn, unknown.raw_token),))

    client = _client(app, current)
    response = client.get("/v1/account/sessions")
    assert _sessions(response) == [_session_id(dsn, t) for t in (current, phone.raw_token, unknown.raw_token)]
    items = response.json()["sessions"]
    assert [item["device_label"] for item in items] == ["Chrome · Windows", "Safari · iPhone", None]
    assert set(items[0]) == {"session_id", "device_label", "created_at", "last_used_at", "current"}
    assert "token" not in response.text and _session_id(dsn, stranger.raw_token) not in response.text
    # 0013 前建立的 session 沒有最後使用時間，以 updated_at 代替。
    with psycopg.connect(dsn) as connection:
        connection.execute("UPDATE learner_sessions SET last_used_at = NULL, device_label = NULL "
                           "WHERE session_id = %s", (_session_id(dsn, phone.raw_token),))
    legacy = client.get("/v1/account/sessions").json()["sessions"]
    assert {item["session_id"]: item["device_label"] for item in legacy}[_session_id(dsn, phone.raw_token)] is None
    assert all(item["last_used_at"] for item in legacy)


def test_last_used_is_recorded_on_requests_at_most_every_interval(app, clean_database_dsn):
    dsn = clean_database_dsn
    created = register_account(EMAIL, PASSWORD, dsn=dsn)
    session_id = _session_id(dsn, created.raw_token)

    def last_used():
        with psycopg.connect(dsn) as connection:
            return connection.execute(
                "SELECT last_used_at, idle_expires_at FROM learner_sessions WHERE session_id = %s",
                (session_id,)).fetchone()
    with psycopg.connect(dsn) as connection:
        connection.execute("UPDATE learner_sessions SET last_used_at = now() - %s WHERE session_id = %s",
                           (LAST_USED_INTERVAL - timedelta(minutes=1), session_id))
    recent, idle = last_used()
    assert resolve_session(created.raw_token, dsn=dsn) is not None
    assert last_used() == (recent, idle)
    with psycopg.connect(dsn) as connection:
        connection.execute("UPDATE learner_sessions SET last_used_at = now() - %s WHERE session_id = %s",
                           (LAST_USED_INTERVAL + timedelta(minutes=1), session_id))
    assert _client(app, created.raw_token).get("/v1/profile").status_code == 200
    used, after_idle = last_used()
    assert used > recent + timedelta(minutes=1)
    # 記錄最後使用不延長閒置期限。
    assert after_idle == idle


def test_revoke_single_session_is_owner_scoped_and_cannot_target_current(app, clean_database_dsn):
    dsn = clean_database_dsn
    current = register_account(EMAIL, PASSWORD, dsn=dsn)
    other = login_account(EMAIL, PASSWORD, dsn=dsn)
    kept = login_account(EMAIL, PASSWORD, dsn=dsn)
    stranger = register_account("stranger@example.com", PASSWORD, dsn=dsn)
    client = _client(app, current.raw_token)
    current_id, other_id, kept_id, stranger_id = (
        _session_id(dsn, t.raw_token) for t in (current, other, kept, stranger))

    # 跨帳號與不存在的 session 一律 404，不撤銷任何東西。
    for target in (stranger_id, str(uuid4())):
        response = client.delete(f"/v1/account/sessions/{target}", headers=HEADERS)
        assert response.status_code == 404 and response.json()["reason_code"] == "RESOURCE_NOT_FOUND"
    assert resolve_session(stranger.raw_token, dsn=dsn) is not None
    # 另一帳號也不能撤銷這個帳號的登入。
    assert _client(app, stranger.raw_token).delete(
        f"/v1/account/sessions/{other_id}", headers=HEADERS).status_code == 404
    # 目前這個登入不能用這支撤銷（改用登出）。
    response = client.delete(f"/v1/account/sessions/{current_id}", headers=HEADERS)
    assert response.status_code == 400 and response.json()["reason_code"] == "REQUEST_INVALID"
    assert resolve_session(current.raw_token, dsn=dsn) is not None
    # 需要 Origin、空 body 與合法 UUID。
    assert client.delete(f"/v1/account/sessions/{other_id}").status_code == 403
    assert client.request("DELETE", f"/v1/account/sessions/{other_id}", headers=HEADERS,
                          content=b"{}").status_code == 400
    assert client.delete("/v1/account/sessions/not-a-uuid", headers=HEADERS).status_code == 400
    assert _client(app).delete(f"/v1/account/sessions/{other_id}", headers=HEADERS).status_code == 401
    assert resolve_session(other.raw_token, dsn=dsn) is not None

    response = client.delete(f"/v1/account/sessions/{other_id}", headers=HEADERS)
    assert _sessions(response) == [current_id, kept_id]
    assert resolve_session(other.raw_token, dsn=dsn) is None
    assert resolve_session(kept.raw_token, dsn=dsn) is not None
    assert _client(app, other.raw_token).get("/v1/profile").status_code == 401
    # 已撤銷的本人登入再撤銷一次視為已完成。
    assert _sessions(client.delete(f"/v1/account/sessions/{other_id}", headers=HEADERS)) == [current_id, kept_id]
    # 被撤銷的裝置不能反過來撤銷目前裝置。
    assert _client(app, other.raw_token).delete(
        f"/v1/account/sessions/{current_id}", headers=HEADERS).status_code == 401
