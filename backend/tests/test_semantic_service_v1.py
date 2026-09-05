from copy import deepcopy
import json
from pathlib import Path

import httpx
import pytest

from runtime.semantic_service import SemanticServiceError, material_request_fits, preflight_semantic_service, request_semantics


def _lock() -> dict:
    return json.loads((Path(__file__).parents[2] / "local_ai/runtime-lock.json").read_text())


def test_preflight_and_both_tasks_use_the_same_resident_service():
    paths: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/health": return httpx.Response(200)
        if request.url.path == "/version": return httpx.Response(200, json={"version": "0.28.0"})
        if request.url.path == "/v1/models": return httpx.Response(200, json={"data": [{"id": "Qwen/Qwen3.8-27B-FP8", "max_model_len": 32768}]})
        if request.url.path == "/tokenize": return httpx.Response(200, json={"count": 50, "max_model_len": 32768})
        task = json.loads(request.content)["response_format"]["json_schema"]["name"]
        body = json.loads(request.content)
        generation = _lock()["material_semantics"]["generation"]
        if task == "material_semantics":
            assert {key: body[key] for key in generation} == generation
        else:
            assert body["chat_template_kwargs"] == {"enable_thinking": False}
            assert (set(generation) - {"chat_template_kwargs"}).isdisjoint(body)
        content = {"material_semantics": {"concepts": [], "relations": []}, "assessment": {"schema": "assessment-semantics-response/v2", "candidates": []}}[task]
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(content)}}]})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        preflight_semantic_service(_lock(), client=client)
        schema = {"type": "object"}
        for task in ("material_semantics", "assessment"):
            result = request_semantics(client, runtime_lock=_lock(), task=task, request={"schema": "x"}, response_schema=schema)
            assert ("concepts" if task == "material_semantics" else "candidates") in result
    assert paths.count("/v1/chat/completions") == 2
    assert set(paths) == {"/health", "/version", "/v1/models", "/tokenize", "/v1/chat/completions"}


def test_assessment_tokenizer_and_generation_both_disable_thinking():
    """出題的 token 預算與實際推論使用相同的非 thinking template。"""
    observed = []
    def respond(request):
        body = json.loads(request.content)
        observed.append((request.url.path, body["chat_template_kwargs"]))
        if request.url.path == "/tokenize":
            return httpx.Response(200, json={"count": 50, "max_model_len": 32768})
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": '{"schema":"assessment-semantics-response/v2","candidates":[]}'}}]})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        request_semantics(client, runtime_lock=_lock(), task="assessment", request={}, response_schema={})
    assert observed == [
        ("/tokenize", {"enable_thinking": False}),
        ("/v1/chat/completions", {"enable_thinking": False}),
    ]


@pytest.mark.parametrize("count, fits", [(28672, True), (28673, False)])
def test_material_packing_and_generation_share_exact_token_budget(count, fits):
    requests = []
    def respond(request):
        body = json.loads(request.content)
        requests.append((request.url.path, body))
        if request.url.path == "/tokenize":
            return httpx.Response(200, json={"count": count, "max_model_len": 32768})
        assert body["max_tokens"] == 4096
        return httpx.Response(200, json={"choices": [{
            "finish_reason": "length", "message": {"content": '{"concepts":[]}'},
        }]})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        arguments = dict(runtime_lock=_lock(), task="material_semantics", request={"sections": []}, response_schema={})
        assert material_request_fits(client, _lock(), arguments["request"]) is fits
        with pytest.raises(SemanticServiceError, match="SEMANTIC_OUTPUT_TRUNCATED" if fits else "SEMANTIC_INPUT_TOO_LARGE"):
            request_semantics(client, **arguments)
    assert requests[0][1]["messages"] == requests[1][1]["messages"]
    template = {"enable_thinking": True, "reasoning_effort": "xhigh"}
    assert requests[0][1]["chat_template_kwargs"] == template
    assert requests[1][1]["chat_template_kwargs"] == template
    if fits:
        assert requests[2][1]["chat_template_kwargs"] == template
    assert sum(path == "/v1/chat/completions" for path, _ in requests) == int(fits)


def test_non_loopback_or_second_runtime_contract_is_rejected_before_network():
    lock = _lock()
    lock["semantic_service"]["base_url"] = "http://example.test:8000"
    with httpx.Client(transport=httpx.MockTransport(lambda _request: (_ for _ in ()).throw(AssertionError()))) as client:
        with pytest.raises(SemanticServiceError, match="SEMANTIC_SERVICE_CONFIG_INVALID"):
            request_semantics(client, runtime_lock=lock, task="material_semantics", request={}, response_schema={})

    second = deepcopy(_lock())
    second["semantic_service"]["model_id"] = "Qwen/second-model"
    with httpx.Client(transport=httpx.MockTransport(lambda _request: (_ for _ in ()).throw(AssertionError()))) as client:
        with pytest.raises(SemanticServiceError):
            request_semantics(client, runtime_lock=second, task="assessment", request={}, response_schema={})


def test_preflight_rejects_more_than_one_served_model():
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200)
        if request.url.path == "/version":
            return httpx.Response(200, json={"version": "0.28.0"})
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={
                "data": [
                    {"id": "Qwen/Qwen3.8-27B-FP8", "max_model_len": 32768},
                    {"id": "second-model", "max_model_len": 32768},
                ]
            })
        return httpx.Response(200, json={"count": 1, "max_model_len": 32768})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(SemanticServiceError, match="SEMANTIC_SERVICE_IDENTITY_MISMATCH"):
            preflight_semantic_service(_lock(), client=client)
