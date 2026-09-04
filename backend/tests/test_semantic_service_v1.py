from copy import deepcopy
import json
from pathlib import Path

import httpx
import pytest

from runtime.semantic_service import SemanticServiceError, preflight_semantic_service, request_semantics


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
        content = {"material_semantics": {"schema": "material-semantics-response/v1", "concepts": [], "relations": []}, "assessment": {"schema": "assessment-semantics-response/v1", "candidates": []}}[task]
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(content)}}]})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        preflight_semantic_service(_lock(), client=client)
        schema = {"type": "object"}
        for task in ("material_semantics", "assessment"):
            assert request_semantics(client, runtime_lock=_lock(), task=task, request={"schema": "x"}, response_schema=schema)["schema"].endswith("response/v1")
    assert paths.count("/v1/chat/completions") == 2
    assert set(paths) == {"/health", "/version", "/v1/models", "/tokenize", "/v1/chat/completions"}


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
