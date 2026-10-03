"""來源核對失敗時只能有限修正，不以 HTTP／JSON 成功冒充內容通過。"""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("podcast_provider", Path(__file__).resolve().parents[2] / "ops/podcast/provider.py")
provider = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provider)


def body():
    return {"mode": "full", "delivery": "solo", "claims": [{"claim_id": "synthetic-claim", "label": "非空檢查",
        "text": "空堆疊不可 pop。", "evidence": [{"quote": "空堆疊不可 pop。"}]}]}


def candidate(text):
    return {"segments": [{"source_index": 0, "turns": [{"speaker": "host", "text": text}]}]}


def check(supported):
    return {"checks": [{"source_index": 0, "supported": supported, "reason": "否定條件須保留"}]}


def test_review_failure_repairs_once_then_requires_new_review(monkeypatch):
    answers = iter([candidate("空堆疊可以 pop。"), check(False), candidate("空堆疊不可 pop。"), check(True)])
    prompts = []
    def luna(prompt, schema):
        prompts.append(prompt)
        return next(answers)
    monkeypatch.setattr(provider, "luna", luna)
    result = provider.script(body())
    assert len(prompts) == 4
    assert "否定條件須保留" in prompts[2]
    assert result["segments"][0]["turns"][0]["text"] == "空堆疊不可 pop。"
    assert result["segments"][0]["claim_id"] == "synthetic-claim"


def test_repeated_review_failure_does_not_publish_or_retry_forever(monkeypatch):
    answers = iter([candidate("空堆疊可以 pop。"), check(False)] * 2)
    calls = []
    def luna(prompt, schema):
        calls.append(schema)
        return next(answers)
    monkeypatch.setattr(provider, "luna", luna)
    with pytest.raises(RuntimeError, match="PODCAST_SCRIPT_NEEDS_REVIEW"):
        provider.script(body())
    assert len(calls) == 4


def test_structured_json_with_foreign_claim_is_not_accepted(monkeypatch):
    wrong = candidate("空堆疊不可 pop。")
    wrong["segments"][0]["source_index"] = 99
    monkeypatch.setattr(provider, "luna", lambda *_: wrong)
    with pytest.raises(RuntimeError, match="PODCAST_SCRIPT_INVALID"):
        provider.script(body())


def test_repeated_claim_ids_remain_separate_source_positions(monkeypatch):
    request = body()
    request["claims"].append(dict(request["claims"][0], label="第二個概念"))
    replies = iter([
        {"segments": [{"source_index": i, "turns": [{"speaker": "host", "text": "空堆疊不可 pop。"}]} for i in range(2)]},
        {"checks": [{"source_index": i, "supported": True, "reason": "來源支持"} for i in range(2)]},
    ])
    def luna(prompt, schema):
        rows = next(iter(schema["properties"].values()))
        assert rows["minItems"] == rows["maxItems"] == 2
        return next(replies)
    monkeypatch.setattr(provider, "luna", luna)
    result = provider.script(request)
    assert [s["claim_id"] for s in result["segments"]] == ["synthetic-claim", "synthetic-claim"]


def test_same_page_table_context_reaches_generation_and_review(monkeypatch):
    request = body()
    request["claims"][0]["evidence"][0].update(evidence_id="cell", page_ref="same-page")
    request["source_context"] = {"same-page": {"blocks": [{"evidence_id": "header", "text": "合成表格標題"}]}}
    replies = iter([candidate("空堆疊不可 pop。"), check(True)])
    prompts = []
    def luna(prompt, schema):
        prompts.append(prompt)
        return next(replies)
    monkeypatch.setattr(provider, "luna", luna)
    result = provider.script(request)
    assert all("合成表格標題" in prompt and "same-page" in prompt for prompt in prompts)
    assert result["segments"][0]["claim_id"] == "synthetic-claim"


def test_dialogue_preserves_source_and_requires_two_distinct_speakers(monkeypatch):
    request = {**body(), "delivery": "dialogue"}
    turns = [{"speaker": "host", "text": "如果堆疊空了，還能取出東西嗎？"},
             {"speaker": "guest", "text": "不行，空堆疊不可 pop，要先確認裡面有元素。"}]
    responses = iter([{"segments": [{"source_index": 0, "turns": turns}]}, check(True)])
    monkeypatch.setattr(provider, "luna", lambda *_: next(responses))
    result = provider.script(request)
    assert result["segments"] == [{"claim_id": "synthetic-claim", "turns": turns}]
    wrong = {"segments": [{"source_index": 0, "turns": [turns[0], turns[0]]}]}
    monkeypatch.setattr(provider, "luna", lambda *_: wrong)
    with pytest.raises(RuntimeError, match="PODCAST_SCRIPT_INVALID"):
        provider.script(request)


def test_audio_failure_never_returns_partial_file_or_falls_back(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setenv("STUDYDY_PODCAST_TTS_PYTHON", "/isolated/python")
    def failed(args, **kwargs):
        Path(args[-1]).write_bytes(b"RIFFpartial")
        assert kwargs["timeout"] == 540
        assert "STUDYDY_PODCAST_PROVIDER_TOKEN" not in kwargs["env"]
        return SimpleNamespace(returncode=1)
    monkeypatch.setattr(provider.subprocess, "run", failed)
    with pytest.raises(RuntimeError, match="PODCAST_AUDIO_INVALID"):
        provider.audio({"script": candidate("空堆疊不可 pop。")})


def test_dialogue_allows_one_speaker_per_source_when_episode_has_both(monkeypatch):
    request = {**body(), 'delivery': 'dialogue'}
    request['claims'].append(dict(request['claims'][0], claim_id='second-claim'))
    segments = [{'source_index': 0, 'turns': [{'speaker': 'guest', 'text': '先確認堆疊不是空的，才能取出元素。'}]},
                {'source_index': 1, 'turns': [{'speaker': 'host', 'text': '原來要先檢查，不能直接取出。'}]}]
    replies = iter([{'segments': segments}, {'checks': [{'source_index': i, 'supported': True, 'reason': '來源支持'} for i in range(2)]}])
    monkeypatch.setattr(provider, 'luna', lambda *_: next(replies))
    assert [s['claim_id'] for s in provider.script(request)['segments']] == ['synthetic-claim', 'second-claim']
