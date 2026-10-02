"""HTTPS 與上傳入口契約；只使用 fixture DB，不建立外部連線。"""
from uuid import uuid4
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
import runtime.api.app as api
from product_fixtures import _settings
from runtime.storage.migrations import run_migrations

ORIGIN = 'https://studydy.net'


def app(tmp_path, monkeypatch, dsn=None, limit=104857600):
    monkeypatch.setattr(api, 'runtime_binding', lambda _: {})
    return api.create_app(api.ApiSettings(profile='local', public_origin=ORIGIN,
        secure_cookie=True, local_config=_settings(tmp_path), dsn=dsn, upload_max_bytes=limit))


def test_https_cookie_origin_and_forged_identity(clean_database_dsn, tmp_path, monkeypatch):
    run_migrations(clean_database_dsn)
    client = TestClient(app(tmp_path, monkeypatch, clean_database_dsn), base_url=ORIGIN)
    body = {'email': 'deployment@example.com', 'password': 'Synthetic password 42'}
    for headers in [[], [('Origin', 'null')], [('Origin', 'https://wrong.example')],
                    [('Origin', ORIGIN), ('Origin', ORIGIN)], [('Origin', 'http://127.0.0.1:4173')]]:
        response = client.post('/v1/accounts', headers=headers, json=body)
        assert response.status_code == 403
        assert response.json()['reason_code'] == 'ORIGIN_NOT_ALLOWED'
    response = client.post('/v1/accounts', headers={'Origin': ORIGIN}, json=body)
    assert response.status_code == 201
    cookie = response.headers['set-cookie']
    for flag in ['Secure', 'HttpOnly', 'SameSite=strict', 'Path=/']:
        assert flag in cookie
    assert 'Domain=' not in cookie
    headers = {'Forwarded': 'for=evil;proto=http;host=evil', 'X-Forwarded-For': '203.0.113.9',
        'X-Forwarded-Proto': 'http', 'CF-Access-Authenticated-User-Email': 'other@example.com',
        'CF-Access-Jwt-Assertion': 'synthetic', 'CF-Connecting-IP': '203.0.113.9'}
    assert client.get('/v1/session', headers=headers).json() == response.json()
    stranger = TestClient(client.app, base_url=ORIGIN)
    assert stranger.get('/v1/session', headers=headers).status_code == 401
    assert client.get('/v1/session').headers['cache-control'] == 'private, no-store'


@pytest.mark.parametrize('limit', [1024, 90 * 1024 * 1024])
def test_upload_limit_capability_stream_and_rejected_state(limit, tmp_path, monkeypatch):
    application = app(tmp_path, monkeypatch, limit=limit)
    monkeypatch.setattr(api, '_trusted_learner', lambda *_: SimpleNamespace(learner_id=uuid4()))
    monkeypatch.setattr(api, 'normalizer_available', lambda: False)
    accepted = []
    # Storage sentinel proves oversize is rejected before any source write.
    def store(*args, **kwargs):
        accepted.append(len(args[2]))
        raise api._ApiFailure('STORAGE_UNAVAILABLE')
    monkeypatch.setattr(api, 'upload_source', store)
    client = TestClient(application, base_url=ORIGIN)
    assert client.get('/v1/source-capabilities').json()['formats'][0]['max_bytes'] == limit
    for size in [limit - 1, limit, limit + 1]:
        def chunks():
            remaining = size
            while remaining:
                amount = min(65536, remaining)
                yield b'x' * amount
                remaining -= amount
        result = client.post(f'/v1/materials/{uuid4()}/sources', content=chunks(), headers={
            'Origin': ORIGIN, 'X-Material-Name': 'synthetic.pdf',
            'Content-Type': 'application/pdf', 'Idempotency-Key': str(uuid4())})
        assert result.json()['reason_code'] == ('MATERIAL_TOO_LARGE' if size > limit else 'STORAGE_UNAVAILABLE')
    assert accepted == [limit - 1, limit]


@pytest.mark.parametrize('limit', [0, -1, 104857601, True])
def test_upload_limit_rejects_unbounded_configuration(limit, tmp_path, monkeypatch):
    with pytest.raises(ValueError, match='API_SETTINGS_INVALID'):
        app(tmp_path, monkeypatch, limit=limit)
