import base64
import io
import json
import sys
from types import SimpleNamespace
import urllib.request

import pytest

from runtime.ssh_bridge import REMOTE, REPLY, SSHBridge


def test_bridge_preserves_body_and_does_not_replay_after_lost_reply(monkeypatch):
    request_body = '合成教材請求'.encode()
    response = {'status': 200, 'body': base64.b64encode(b'{"ok":true}').decode(), 'type': 'application/json'}
    written = io.BytesIO()
    bridge = SSHBridge('synthetic@example.invalid', 22)
    bridge.process = SimpleNamespace(stdin=written, stdout=io.BytesIO(REPLY + json.dumps(response).encode() + b'\n'))
    monkeypatch.setattr(bridge, 'connect', lambda: None)
    closed = []
    monkeypatch.setattr(bridge, 'close', lambda: closed.append(True))
    assert bridge.forward('/v1/chat/completions', 'POST', request_body) == (200, b'{"ok":true}', 'application/json')
    sent = json.loads(written.getvalue())
    assert base64.b64decode(sent['body']) == request_body
    with pytest.raises(ValueError, match='MODEL_BRIDGE_REQUEST_INVALID'):
        bridge.forward('/private', 'GET', b'')
    assert len(written.getvalue().splitlines()) == 1
    with pytest.raises(RuntimeError, match='^MODEL_BRIDGE_UNAVAILABLE$'):
        bridge.forward('/v1/chat/completions', 'POST', request_body)
    assert len(written.getvalue().splitlines()) == 2
    assert closed == [True]


def test_remote_helper_applies_remote_auth_without_returning_it(monkeypatch):
    data = '合成內容'.encode()
    requests = [
        {'path': '/v1/chat/completions', 'method': 'POST', 'body': base64.b64encode(data).decode()},
        {'path': '/not-allowed', 'method': 'GET', 'body': None},
    ]
    stdin = io.BytesIO(b''.join(json.dumps(value).encode() + b'\n' for value in requests))
    stdout = io.StringIO()
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(buffer=stdin))
    monkeypatch.setattr(sys, 'stdout', stdout)
    monkeypatch.setenv('VLLM_API_KEY', 'synthetic-remote-only-key')
    calls = []

    class Response:
        status = 200
        headers = {'Content-Type': 'application/json'}
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self, _limit): return b'{"ok":true}'

    def respond(request):
        calls.append(request)
        assert request.full_url == 'http://127.0.0.1:8088/v1/chat/completions'
        assert request.data == data
        assert request.get_header('Authorization') == 'Bearer synthetic-remote-only-key'
        return Response()

    monkeypatch.setattr(urllib.request, 'build_opener', lambda *_: SimpleNamespace(open=respond))
    exec(compile(REMOTE, '<remote-helper>', 'exec'), {'MODEL_PORT': 8088})
    lines = stdout.getvalue().splitlines()
    assert lines[0] == 'STUDYDY_MODEL_BRIDGE_READY'
    assert [json.loads(line[len(REPLY):])['status'] for line in lines[1:]] == [200, 503]
    assert 'synthetic-remote-only-key' not in stdout.getvalue()
    assert len(calls) == 1
