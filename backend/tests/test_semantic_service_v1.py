from copy import deepcopy
import json
from pathlib import Path

import httpx
import pytest

from runtime.semantic_service import SemanticServiceError, material_request_fits, preflight_semantic_service, request_semantics
from knowledge_map.material_review import review_runtime_lock


def _lock() -> dict:
    return json.loads((Path(__file__).parents[2] / "local_ai/runtime-lock.json").read_text())


def test_material_review_uses_existing_gemma_transport_and_context_check():
    observed=[]
    def respond(request):
        body=json.loads(request.content);observed.append((request.url.path,body))
        if request.url.path=='/tokenize':
            return httpx.Response(200,json={'count':100,'max_model_len':32768})
        assert body['response_format']['json_schema']['name']=='material_review'
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'{"assignments":[],"alias_edits":[],"claim_edits":[],"relation_edits":[]}'}}]})
    lock=_lock();original=deepcopy(lock)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result=request_semantics(client,runtime_lock=review_runtime_lock(lock),task='material_review',request={},response_schema={})
    assert result['assignments']==[] and lock==original
    assert [path for path,_ in observed]==['/tokenize','/v1/chat/completions']


def test_preflight_and_both_tasks_use_the_same_resident_service():
    paths: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/health": return httpx.Response(200)
        if request.url.path == "/version": return httpx.Response(200, json={"version": "0.28.0"})
        if request.url.path == "/v1/models": return httpx.Response(200, json={"data": [{"id": "google/gemma-4-31B-it-qat-w4a16-ct", "max_model_len": 32768}]})
        if request.url.path == "/tokenize":
            assert json.loads(request.content)["chat_template_kwargs"] == {"enable_thinking": True}
            return httpx.Response(200, json={"count": 50, "max_model_len": 32768})
        task = json.loads(request.content)["response_format"]["json_schema"]["name"]
        body = json.loads(request.content)
        generation = _lock()["material_semantics"]["generation"]
        assert {key: body[key] for key in generation} == generation
        assert body["model"] == "google/gemma-4-31B-it-qat-w4a16-ct"
        assert "reasoning_effort" not in body
        assert body["chat_template_kwargs"] == {"enable_thinking": True}
        content = {"material_semantics": {"concepts": [], "relations": []}, "assessment": {"schema": "assessment-semantics-response/v1", "candidates": []}}[task]
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(content)}}]})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        preflight_semantic_service(_lock(), client=client)
        schema = {"type": "object"}
        for task in ("material_semantics", "assessment"):
            result = request_semantics(client, runtime_lock=_lock(), task=task, request={"schema": "x"}, response_schema=schema)
            assert ("concepts" if task == "material_semantics" else "candidates") in result
    assert paths.count("/v1/chat/completions") == 2
    assert set(paths) == {"/health", "/version", "/v1/models", "/tokenize", "/v1/chat/completions"}


def test_assessment_tokenizer_and_generation_both_use_qualified_thinking():
    """出題的 token 預算與實際推論使用相同的合格 thinking template。"""
    observed = []
    def respond(request):
        body = json.loads(request.content)
        observed.append((request.url.path, body["chat_template_kwargs"]))
        if request.url.path == "/tokenize":
            return httpx.Response(200, json={"count": 50, "max_model_len": 32768})
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": '{"schema":"assessment-semantics-response/v1","candidates":[]}'}}]})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        request_semantics(client, runtime_lock=_lock(), task="assessment", request={}, response_schema={})
    assert observed == [
        ("/tokenize", {"enable_thinking": True}),
        ("/v1/chat/completions", {"enable_thinking": True}),
    ]


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
        arguments = dict(runtime_lock=_lock(), task="material_semantics", request={"sections": [{"evidence": [[0, 1, "code", "x"]]}]}, response_schema={})
        assert material_request_fits(client, _lock(), arguments["request"]) is fits
        with pytest.raises(SemanticServiceError, match="SEMANTIC_OUTPUT_TRUNCATED" if fits else "SEMANTIC_INPUT_TOO_LARGE"):
            request_semantics(client, **arguments)
    assert requests[0][1]["messages"] == requests[1][1]["messages"]
    template = {"enable_thinking": True}
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
    second["semantic_service"]["model_id"] = "example/other-model"
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
                    {"id": "google/gemma-4-31B-it-qat-w4a16-ct", "max_model_len": 32768},
                    {"id": "example/other-model", "max_model_len": 32768},
                ]
            })
        return httpx.Response(200, json={"count": 1, "max_model_len": 32768})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(SemanticServiceError, match="SEMANTIC_SERVICE_IDENTITY_MISMATCH"):
            preflight_semantic_service(_lock(), client=client)


@pytest.mark.parametrize("fresh_count,fits", [(1536, True), (1537, False)])
def test_material_bundle_budget_excludes_existing_catalog(fresh_count, fits):
    """舊概念目錄只佔 context；新增教材另有輸出容量預算。"""
    calls = []
    def respond(request):
        body = json.loads(request.content)
        material = json.loads(body["messages"][-1]["content"].split("\nINPUT:\n", 1)[1])
        calls.append(material)
        return httpx.Response(200, json={"count": 6000 if material["existing_concepts"] else fresh_count, "max_model_len": 32768})
    material = {"existing_concepts": [{"k": "array", "l": "Array", "a": [], "c": ["Existing claim"], "e": [0]}],
                "sections": [{"title": "New material", "evidence": [[1, 1, "paragraph", "First"], [2, 2, "paragraph", "Second"]]}]}
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        assert material_request_fits(client, _lock(), material) is fits
    assert calls[0] == material
    assert calls[1]["existing_concepts"] == []
    assert calls[1]["sections"] == material["sections"]


@pytest.mark.parametrize("failure", ["offline", "timeout", "http503"])
def test_offline_ai_requests_keep_existing_retryable_api_error(failure):
    from runtime.api.app import _fixed_exception, _error_response
    from learning_adaptation.assessments import AssessmentError
    def respond(request):
        if failure == "offline": raise httpx.ConnectError("offline", request=request)
        if failure == "timeout": raise httpx.ReadTimeout("timeout", request=request)
        return httpx.Response(503)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(SemanticServiceError) as caught:
            request_semantics(client, runtime_lock=_lock(), task="assessment", request={}, response_schema={})
    response = _error_response(_fixed_exception(AssessmentError(caught.value.reason_code)))
    assert response.status_code == 503
    assert json.loads(response.body)["retryable"] is True


@pytest.mark.parametrize("field,value", [
    ("model_id", "example/other-model"),
    ("revision", "a" * 40),
    ("base_url", "http://127.0.0.1:18001"),
    ("max_model_len", 16384),
    ("max_num_seqs", 2),
])
def test_wrong_contract_is_rejected_by_lock_and_client_before_network(field, value):
    """即使 revision 格式合法，也只能接受已 qualification 的單一模型。"""
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


@pytest.mark.parametrize("model,context", [
    ("example/other-model", 32768),
    ("google/gemma-4-31B-it-qat-w4a16-ct", 16384),
])
def test_preflight_rejects_wrong_resident_identity(model, context):
    """服務有回應仍須符合指定模型與 context。"""
    def respond(request):
        if request.url.path == "/health":
            return httpx.Response(200)
        if request.url.path == "/version":
            return httpx.Response(200, json={"version": "0.28.0"})
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": model, "max_model_len": context}]})
        return httpx.Response(200, json={"count": 1, "max_model_len": 32768})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(SemanticServiceError, match="SEMANTIC_SERVICE_IDENTITY_MISMATCH"):
            preflight_semantic_service(_lock(), client=client)


@pytest.mark.parametrize("task,budget", [("assessment", 4096), ("assessment_check", 1536)])
def test_qualified_assessment_wire_keeps_schema_and_reasoning_separate(task, budget):
    """生成與解題各自遵循合格 budget，reasoning 不混入 JSON。"""
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}
    def respond(request):
        body = json.loads(request.content)
        assert body["chat_template_kwargs"] == {"enable_thinking": True}
        if request.url.path == "/tokenize":
            return httpx.Response(200, json={"count": 50, "max_model_len": 32768})
        assert body["max_tokens"] == budget
        assert [body[k] for k in ("temperature", "top_p", "top_k")] == [1.0, 0.95, 64]
        assert body["response_format"]["json_schema"]["schema"] == schema
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": '{"ok":true}', "reasoning_content": "Separate reasoning field",
        }}]})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        assert request_semantics(client, runtime_lock=_lock(), task=task, request={}, response_schema=schema) == {"ok": True}
