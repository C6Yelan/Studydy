"""個人顯示名稱：真 API／DB 的正規化、驗證、Origin 與帳號隔離。"""
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from product_fixtures import HEADERS, ORIGIN, _app, closed_loop
from runtime.learner_session import register_account


def _client(dsn, token, tmp_path, monkeypatch):
    client = TestClient(_app(dsn, tmp_path, monkeypatch), base_url=ORIGIN)
    client.cookies.set("studydy_session", token)
    return client


def _save(client, name, headers=HEADERS):
    return client.post("/v1/profile", headers=headers,
                       json={"schema": "learner-profile-update/v1", "display_name": name})


def test_display_name_is_normalized_validated_and_owner_scoped(closed_loop, tmp_path, monkeypatch):
    learner, _, _, _, dsn, token = closed_loop
    client = _client(dsn, token, tmp_path, monkeypatch)
    # 舊帳號沒有名稱，不需回填。
    assert client.get("/v1/profile").json() == {"schema": "learner-profile/v1", "display_name": None}

    # NFC 正規化（e + 組合重音 → é）並去除前後空白，含全形空白。
    saved = _save(client, "　 René 以安 ")
    assert saved.status_code == 200, saved.json()
    assert saved.json()["display_name"] == "René 以安"
    assert client.get("/v1/profile").json()["display_name"] == "René 以安"

    for invalid in ["一" * 21, "line\nbreak", "tab\there", "x y"]:
        response = _save(client, invalid)
        assert response.status_code == 400, invalid
        assert response.json()["reason_code"] == "REQUEST_INVALID"
    assert _save(client, "一" * 20).status_code == 200
    assert _save(client, "x" * 201).status_code == 400
    # 空白或 null 都是清除名稱。
    assert _save(client, "   ").json()["display_name"] is None
    assert _save(client, "以安").json()["display_name"] == "以安"
    assert _save(client, None).json()["display_name"] is None

    assert _save(client, "以安", headers={}).status_code == 403
    assert client.post("/v1/profile", headers=HEADERS, json={"display_name": "以安"}).status_code == 400
    assert client.post("/v1/profile", headers=HEADERS, json={
        "schema": "learner-profile-update/v1", "display_name": "以安", "learner_id": str(uuid4()),
    }).status_code == 400
    assert client.get("/v1/profile", params={"learner_id": str(uuid4())}).status_code == 400

    _save(client, "以安")
    other = register_account(f"other_{uuid4().hex[:8]}@example.com", "Synthetic test password 42", dsn=dsn)
    foreign = _client(dsn, other.raw_token, tmp_path, monkeypatch)
    assert foreign.get("/v1/profile").json()["display_name"] is None
    assert _save(foreign, "別人").status_code == 200
    assert client.get("/v1/profile").json()["display_name"] == "以安"
    # 身分契約不變，名稱只經由 profile 讀取。
    assert client.get("/v1/session").json() == {"schema": "learner-identity/v1", "learner_id": str(learner.learner_id)}

    client.cookies.clear()
    assert client.get("/v1/profile").status_code == 401
    assert _save(client, "以安").status_code == 401


@pytest.mark.parametrize("value", ["", " padded", "padded ", "a\nb", "一" * 21])
def test_database_constraint_rejects_unnormalized_names(closed_loop, value):
    learner, _, _, _, dsn, _ = closed_loop
    with psycopg.connect(dsn) as connection, pytest.raises(psycopg.errors.CheckViolation):
        connection.execute("UPDATE learners SET display_name = %s WHERE learner_id = %s", (value, learner.learner_id))
