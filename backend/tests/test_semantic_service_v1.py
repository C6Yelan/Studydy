import json
from pathlib import Path

import httpx
import pytest

from runtime.semantic_service import (
    SemanticServiceError,
    material_request_fits,
    preflight_semantic_service,
    request_semantics,
)


def _lock() -> dict:
    return json.loads((Path(__file__).parents[2] / "local_ai/runtime-lock.json").read_text())


@pytest.mark.parametrize('task,budget', [
    ('material_semantics', 8192), ('material_review', 8192),
    ('assessment', 4096), ('assessment_check', 1536),
])
def test_tasks_share_configured_http_wire(task, budget):
    lock = _lock()
    lock['semantic_service'].update(model_id='example/semantic-model', revision='a' * 40)
    schema = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}},
              'required': ['ok'], 'additionalProperties': False}
    requests = []
    def respond(request):
        body = json.loads(request.content)
        requests.append((request.url.path, body))
        assert body['model'] == 'example/semantic-model'
        assert body['chat_template_kwargs'] == {'enable_thinking': True}
        if request.url.path == '/tokenize':
            return httpx.Response(200, json={'count': 50, 'max_model_len': 32768})
        assert body['max_tokens'] == budget
        assert [body[k] for k in ('temperature', 'top_p', 'top_k')] == [1.0, 0.95, 64]
        assert body['response_format']['json_schema']['name'] == task
        assert body['response_format']['json_schema']['schema'] == schema
        assert 'reasoning_effort' not in body
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
            'content': '{"ok":true}', 'reasoning_content': 'Separate reasoning field',
        }}]})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        response = request_semantics(
            client, runtime_lock=lock, task=task, request={}, response_schema=schema,
        )
        assert response == {"ok": True}
    assert [path for path, _ in requests] == ['/tokenize', '/v1/chat/completions']
    assert requests[0][1]['messages'] == requests[1][1]['messages']


@pytest.mark.parametrize('model,context,extra_model,accepted', [
    ('example/semantic-model', 32768, False, True),
    ('example/wrong-model', 32768, False, False),
    ('example/semantic-model', 16384, False, False),
    ('example/semantic-model', 32768, True, False),
])
def test_preflight_checks_configured_model_and_service_identity(model, context, extra_model, accepted):
    from pdf_evidence.material_pipeline import validate_runtime_lock

    lock = _lock()
    lock["semantic_service"].update(model_id="example/semantic-model", revision="a" * 40)
    assert validate_runtime_lock(lock) is lock

    def respond(request):
        if request.url.path == "/health":
            return httpx.Response(200)
        if request.url.path == "/version":
            return httpx.Response(200, json={"version": "0.28.0"})
        if request.url.path == "/v1/models":
            models = [{"id": model, "max_model_len": context}]
            if extra_model:
                models.append({"id": "example/second-model", "max_model_len": 32768})
            return httpx.Response(200, json={"data": models})
        assert request.url.path == "/tokenize"
        assert json.loads(request.content)["model"] == "example/semantic-model"
        return httpx.Response(200, json={"count": 1, "max_model_len": 32768})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        if accepted:
            preflight_semantic_service(lock, client=client)
        else:
            with pytest.raises(SemanticServiceError, match="SEMANTIC_SERVICE_IDENTITY_MISMATCH"):
                preflight_semantic_service(lock, client=client)


@pytest.mark.parametrize("count, fits", [(24576, True), (24577, False)])
def test_material_packing_and_generation_share_exact_token_budget(count, fits):
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append((request.url.path, body))
        if request.url.path == "/tokenize":
            return httpx.Response(200, json={"count": count, "max_model_len": 32768})
        assert body["max_tokens"] == 8192
        return httpx.Response(200, json={"choices": [{
            "finish_reason": "length", "message": {"content": '{"concepts":[]}'},
        }]})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        arguments = {
            "runtime_lock": _lock(),
            "task": "material_semantics",
            "request": {"sections": [{"evidence": [[0, 1, "code", "x"]]}]},
            "response_schema": {},
        }
        assert material_request_fits(client, _lock(), arguments["request"]) is fits
        reason = "SEMANTIC_OUTPUT_TRUNCATED" if fits else "SEMANTIC_INPUT_TOO_LARGE"
        with pytest.raises(SemanticServiceError, match=reason):
            request_semantics(client, **arguments)
    assert requests[0][1]["messages"] == requests[1][1]["messages"]
    template = {"enable_thinking": True}
    assert requests[0][1]["chat_template_kwargs"] == template
    assert requests[1][1]["chat_template_kwargs"] == template
    if fits:
        assert requests[2][1]["chat_template_kwargs"] == template
    assert sum(path == "/v1/chat/completions" for path, _ in requests) == int(fits)


@pytest.mark.parametrize("fresh_count,fits", [(1536, True), (1537, False)])
def test_material_bundle_budget_excludes_existing_catalog(fresh_count, fits):
    """舊概念目錄只佔 context；新增教材另有輸出容量預算。"""
    calls = []
    def respond(request):
        body = json.loads(request.content)
        material = json.loads(body["messages"][-1]["content"].split("\nINPUT:\n", 1)[1])
        calls.append(material)
        count = 6000 if material["existing_concepts"] else fresh_count
        return httpx.Response(200, json={"count": count, "max_model_len": 32768})
    material = {
        "existing_concepts": [{
            "k": "array", "l": "Array", "a": [], "c": ["Existing claim"], "e": [0],
        }],
        "sections": [{
            "title": "New material",
            "evidence": [[1, 1, "paragraph", "First"], [2, 2, "paragraph", "Second"]],
        }],
    }
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        assert material_request_fits(client, _lock(), material) is fits
    assert calls[0] == material
    assert calls[1]["existing_concepts"] == []
    assert calls[1]["sections"] == material["sections"]


@pytest.mark.parametrize(("failure", "reason"), [
    ("offline", "SEMANTIC_SERVICE_UNAVAILABLE"),
    ("timeout", "SEMANTIC_SERVICE_TIMEOUT"),
    ("http503", "SEMANTIC_SERVICE_UNAVAILABLE"),
])
def test_offline_ai_requests_report_fixed_service_error(failure, reason):
    def respond(request):
        if failure == "offline":
            raise httpx.ConnectError("offline", request=request)
        if failure == "timeout":
            raise httpx.ReadTimeout("timeout", request=request)
        return httpx.Response(503)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(SemanticServiceError) as caught:
            request_semantics(client, runtime_lock=_lock(), task="assessment", request={}, response_schema={})
    assert caught.value.reason_code == reason


@pytest.mark.parametrize("field,value", [
    ("model_id", ""),
    ("revision", " "),
    ("base_url", "http://127.0.0.1:18001"),
    ("base_url", "http://example.test:8000"),
    ("max_model_len", 16384),
    ("max_num_seqs", 2),
])
def test_wrong_contract_is_rejected_by_lock_and_client_before_network(field, value):
    """模型身分不可為空；服務網路位置與能力限制仍在呼叫前檢查。"""
    from pdf_evidence.material_pipeline import MaterialAnalysisError, validate_runtime_lock
    lock = _lock()
    validate_runtime_lock(lock)
    lock["semantic_service"][field] = value
    with pytest.raises(MaterialAnalysisError, match="RUNTIME_LOCK_INVALID"):
        validate_runtime_lock(lock)
    def forbidden(_request):
        pytest.fail("invalid lock must not contact the service")
    with httpx.Client(transport=httpx.MockTransport(forbidden)) as client:
        with pytest.raises(SemanticServiceError, match="SEMANTIC_SERVICE_CONFIG_INVALID"):
            preflight_semantic_service(lock, client=client)


@pytest.mark.parametrize('endpoint', ['http://gemma:8000', 'https://model.example.test'])
def test_deployment_endpoint_routes_all_requests_without_rewriting_snapshot(monkeypatch, endpoint):
    from copy import deepcopy
    from runtime.semantic_service import _headers

    lock = _lock()
    before = deepcopy(lock)
    monkeypatch.setenv('STUDYDY_SEMANTIC_BASE_URL', endpoint)
    requests = []

    def respond(request):
        requests.append(request)
        assert str(request.url).startswith(endpoint + '/')
        assert request.headers['Authorization'] == 'Bearer synthetic-key'
        if request.url.path == '/health':
            return httpx.Response(200)
        if request.url.path == '/version':
            return httpx.Response(200, json={'version': lock['semantic_service']['server']['version']})
        if request.url.path == '/v1/models':
            return httpx.Response(200, json={'data': [{'id': lock['semantic_service']['model_id'], 'max_model_len': 32768}]})
        if request.url.path == '/tokenize':
            return httpx.Response(200, json={'count': 50, 'max_model_len': 32768})
        assert request.url.path == '/v1/chat/completions'
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': '{"ok":true}'}}]})

    with httpx.Client(transport=httpx.MockTransport(respond), headers=_headers({'VLLM_API_KEY': 'synthetic-key'})) as client:
        preflight_semantic_service(lock, client=client)
        assert material_request_fits(client, lock, {'sections': []})
        assert request_semantics(client, runtime_lock=lock, task='assessment', request={}, response_schema={}) == {'ok': True}
    assert {request.url.path for request in requests} == {'/health', '/version', '/v1/models', '/tokenize', '/v1/chat/completions'}
    assert lock == before
    assert _headers({'VLLM_API_KEY': ''}) == {}


@pytest.mark.parametrize('endpoint', [
    '', 'file:///tmp/model', 'http://user:secret@model:8000',
    'http://model:8000/v1', 'http://model:8000?key=secret',
    'http://model:8000\n', 'http://model:99999',
])
def test_invalid_deployment_endpoint_fails_before_network(monkeypatch, endpoint):
    monkeypatch.setenv('STUDYDY_SEMANTIC_BASE_URL', endpoint)
    def forbidden(_request):
        pytest.fail('invalid deployment endpoint must not contact a service')
    with httpx.Client(transport=httpx.MockTransport(forbidden)) as client:
        with pytest.raises(SemanticServiceError, match='^SEMANTIC_SERVICE_CONFIG_INVALID$'):
            preflight_semantic_service(_lock(), client=client)
