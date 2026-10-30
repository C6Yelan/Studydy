"""真 HTTP provider 與 DB worker 的驗證重試；模型回應受控，不呼叫真模型。"""
from copy import deepcopy
import json
import stat

import pytest

from product_fixtures import closed_loop
from qa_fixtures import qa_model
from runtime import voice
from test_podcast_process import provider_server, provider as http_provider
from test_voice import setup


@pytest.mark.parametrize('invalid_attempts', [1, 2])
def test_answer_retry_keeps_original_private_candidate(closed_loop, provider_server, monkeypatch, tmp_path, invalid_attempts):
    owner, _, cid, dsn = setup(closed_loop)
    monkeypatch.setenv('STUDYDY_ARTIFACT_ROOT', str(tmp_path / 'artifacts'))
    candidate = {'segments': [{'text': '\ue200cite\ue202private\ue201', 'citations': [0]}], 'queries': [], 'missing': ''}
    calls = []

    def luna(prompt, schema, **kwargs):
        body = {'request': json.loads(prompt.split('\nINPUT:\n', 1)[1]), 'schema': schema}
        calls.append(body)
        return deepcopy(candidate) if len(calls) <= invalid_attempts else qa_model(body)

    # 文字請求依 runtime lock 分流；本案例驗證 Luna 通道（真 provider HTTP＋替身 CLI）。
    monkeypatch.setenv('STUDYDY_TEXT_TEST_PROVIDER', 'codex-exec-luna')
    monkeypatch.setattr(http_provider, 'luna', luna)
    voice.add_turn(owner, cid, 'retry-candidate', 'Stack LIFO', dsn=dsn)
    assert voice.step(dsn=dsn)
    turn = voice.read(owner, cid, dsn=dsn)['turns'][0]
    assert turn['status'] == ('ready' if invalid_attempts == 1 else 'failed')
    assert len(calls) == (3 if invalid_attempts == 1 else 2)
    files = list((tmp_path / 'artifacts/failures').glob('*.json'))
    assert len(files) == invalid_attempts
    for path in files:
        saved = json.loads(path.read_text())
        assert saved['candidates'][0]['candidate'] == candidate
        assert 'segment_empty_after_citation_cleanup' in saved['rejections']
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert 'synthetic-provider-token' not in path.read_text()
    assert 'candidates' not in turn and 'rejections' not in turn


@pytest.mark.parametrize('failure', ['truncated', 'provider', 'cancelled'])
def test_truncated_provider_failure_and_cancelled_work_are_not_replayed(closed_loop, provider_server, monkeypatch, failure):
    owner, _, cid, dsn = setup(closed_loop)
    turn = voice.add_turn(owner, cid, 'do-not-replay', 'Stack LIFO', dsn=dsn)
    calls = []

    def luna(prompt, schema, **kwargs):
        calls.append(prompt)
        if failure == 'truncated':
            return json.loads('{"segments": [{"text": "unfinished')
        if failure == 'provider':
            raise RuntimeError('LUNA_GENERATION_TIMEOUT')
        voice.action(owner, cid, turn['turn_id'], 'cancel', dsn=dsn)
        return {'segments': [None], 'queries': [], 'missing': ''}

    # 文字請求依 runtime lock 分流；本案例驗證 Luna 通道（真 provider HTTP＋替身 CLI）。
    monkeypatch.setenv('STUDYDY_TEXT_TEST_PROVIDER', 'codex-exec-luna')
    monkeypatch.setattr(http_provider, 'luna', luna)
    assert voice.step(dsn=dsn)
    saved = voice.read(owner, cid, dsn=dsn)['turns'][0]
    assert len(calls) == 1
    assert saved['status'] == ('cancelled' if failure == 'cancelled' else 'failed')
    if failure != 'cancelled':
        # 文字模型失敗經 semantic_service 回報，回答工作統一記為 VOICE_PROVIDER_FAILED。
        assert saved['error_code'] == 'VOICE_PROVIDER_FAILED'
