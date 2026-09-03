import json
from pathlib import Path

import httpx
import pytest

from pdf_evidence.concept_api import (
    CONCEPT_RESPONSE_FORMAT,
    ConceptAPIError,
    _semantic_service_headers,
    chat_completions_url,
    preflight_semantic_service,
    request_concept_text,
)
from pdf_evidence.ocr_page_evidence import canonical_sha256


RUNTIME_LOCK = json.loads(
    (Path(__file__).parents[2] / "local_ai" / "runtime-lock.json").read_text(
        encoding="utf-8"
    )
)


def _semantic_request():
    return json.loads(
        (
            Path(__file__).parents[2]
            / "local_ai"
            / "tests"
            / "fixtures"
            / "semantic_request.json"
        ).read_text(encoding="utf-8")
    )


def _request(
    client,
    *,
    base_url="http://127.0.0.1:8000",
    semantic_request=None,
):
    return request_concept_text(
        client,
        base_url=base_url,
        model="fixed-model",
        prompt_template=RUNTIME_LOCK["semantic"]["prompt"],
        semantic_request=semantic_request or _semantic_request(),
        max_model_len=8_192,
        timeout_seconds=300,
    )


def _server_settings():
    return {
        "runtime_lock": RUNTIME_LOCK,
        "concept_api_base_url": "http://127.0.0.1:8000",
        "concept_model": "Qwen/Qwen3.8-27B-FP8",
        "concept_max_concurrency": 1,
        "concept_max_model_len": 32_768,
    }


def _preflight_response(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/health":
        return httpx.Response(200)
    if request.url.path == "/version":
        return httpx.Response(200, json={"version": "0.28.0"})
    if request.url.path == "/v1/models":
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {
                        "id": "Qwen/Qwen3.8-27B-FP8",
                        "max_model_len": 32_768,
                    }
                ],
            },
        )
    return httpx.Response(200, json={"count": 2, "max_model_len": 32_768})


def test_preflight_verifies_resident_service_without_process_lifecycle():
    observed = []

    def respond(request):
        observed.append((request.method, request.url.path))
        return _preflight_response(request)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        preflight_semantic_service(_server_settings(), client=client)

    assert observed == [
        ("GET", "/health"),
        ("GET", "/version"),
        ("GET", "/v1/models"),
        ("OPTIONS", "/v1/chat/completions"),
        ("POST", "/tokenize"),
    ]


def test_semantic_service_client_uses_only_environment_bearer():
    assert _semantic_service_headers(
        {"VLLM_API_KEY": "test-only-value"}
    ) == {"Authorization": "Bearer test-only-value"}
    assert _semantic_service_headers({}) == {}


@pytest.mark.parametrize("api_key", ["", "line\nbreak", "nul\x00byte"])
def test_semantic_service_bearer_rejects_unsafe_environment_value(api_key):
    with pytest.raises(ConceptAPIError, match="CONCEPT_API_CONFIG_INVALID") as failure:
        _semantic_service_headers({"VLLM_API_KEY": api_key})
    if api_key:
        assert api_key not in str(failure.value)


@pytest.mark.parametrize(
    ("changed_path", "changed_value"),
    [
        (("concept_api_base_url",), "http://127.0.0.1:8001"),
        (("concept_model",), "wrong-model"),
        (("concept_max_model_len",), 8_192),
    ],
)
def test_preflight_rejects_noncanonical_contract(changed_path, changed_value):
    settings = _server_settings()
    settings[changed_path[0]] = changed_value
    with pytest.raises(ConceptAPIError, match="CONCEPT_API_CONFIG_INVALID"):
        preflight_semantic_service(settings)


@pytest.mark.parametrize("field", ["version", "model", "max_model_len"])
def test_preflight_rejects_wrong_service_identity(field):
    def respond(request):
        response = _preflight_response(request)
        if field == "version" and request.url.path == "/version":
            return httpx.Response(200, json={"version": "0.27.0"})
        if field == "model" and request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "wrong-model"}]})
        if field == "max_model_len" and request.url.path == "/tokenize":
            return httpx.Response(200, json={"count": 2, "max_model_len": 8_192})
        return response

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ConceptAPIError, match="CONCEPT_API_IDENTITY_MISMATCH"):
            preflight_semantic_service(_server_settings(), client=client)


def test_chat_completion_uses_exact_loopback_request_and_returns_content():
    observed = []

    def respond(request):
        observed.append(request)
        if request.url.path == "/tokenize":
            return httpx.Response(200, json={"count": 100, "max_model_len": 8_192})
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"concepts":[]}'},
                    }
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        model_text = _request(client, base_url="http://127.0.0.1:8000")

    assert model_text == '{"concepts":[]}'
    assert observed[0].url == "http://127.0.0.1:8000/tokenize"
    assert json.loads(observed[0].content)["add_generation_prompt"] is True
    assert observed[1].url == "http://127.0.0.1:8000/v1/chat/completions"
    assert "authorization" not in observed[1].headers
    body = json.loads(observed[1].content)
    assert "uniqueItems" not in json.dumps(body["response_format"])
    assert canonical_sha256(body["response_format"]["json_schema"]["schema"]) == (
        RUNTIME_LOCK["semantic"]["structured_output"]["schema_sha256"]
    )
    assert body == {
        "model": "fixed-model",
        "messages": [
            {
                "role": "user",
                "content": f"{RUNTIME_LOCK['semantic']['prompt']}\nINPUT:\n"
                + json.dumps(
                    _semantic_request(),
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            }
        ],
        "temperature": 0,
        "max_tokens": 1536,
        "response_format": CONCEPT_RESPONSE_FORMAT,
    }


def test_tokenizer_budget_rejects_before_generation_call():
    observed_paths = []

    def respond(request):
        observed_paths.append(request.url.path)
        return httpx.Response(200, json={"count": 6_657, "max_model_len": 8_192})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ConceptAPIError, match="MODEL_INPUT_TOO_LARGE"):
            _request(client)
    assert observed_paths == ["/tokenize"]


def test_optional_context_overflow_keeps_evidence_in_one_generation_call():
    semantic_request = _semantic_request()
    semantic_request["document_context"]["context_blocks"] = [
        {"id": "c1", "role": "heading_ancestry", "text": "High priority heading"},
        {"id": "c2", "role": "next_page", "text": "Low priority detail"},
    ]
    current = semantic_request["document_context"]["current_blocks"][0]
    current["heading_ancestry_ids"] = ["c1"]
    current["continuation_ids"] = ["c2"]
    context_identity = {
        key: value
        for key, value in semantic_request["document_context"].items()
        if key != "document_context_id"
    }
    semantic_request["document_context"]["document_context_id"] = (
        "concept-context:sha256:" + canonical_sha256(context_identity)
    )
    tokenized_documents = []
    generation_bodies = []

    def respond(request):
        body = json.loads(request.content)
        if request.url.path == "/tokenize":
            encoded = body["messages"][0]["content"].split("\nINPUT:\n", 1)[1]
            document = json.loads(encoded)
            tokenized_documents.append(document)
            context_count = len(document["document_context"]["context_blocks"])
            count = {2: 6_700, 1: 6_680, 0: 6_500}[context_count]
            return httpx.Response(200, json={"count": count, "max_model_len": 8_192})
        generation_bodies.append(body)
        return httpx.Response(
            200,
            json={
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": '{"concepts":[]}'},
                }]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        assert _request(client, semantic_request=semantic_request) == '{"concepts":[]}'

    assert len(generation_bodies) == 1
    assert [
        [block["id"] for block in document["document_context"]["context_blocks"]]
        if "document_context" in document
        else []
        for document in tokenized_documents
    ] == [["c1", "c2"], ["c1"], []]
    assert all(
        document["document_context"]["current_blocks"][0]["kind"]
        == "paragraph"
        for document in tokenized_documents
        if "document_context" in document
    )
    dispatched = json.loads(
        generation_bodies[0]["messages"][0]["content"].split("\nINPUT:\n", 1)[1]
    )
    assert dispatched["evidence"] == semantic_request["evidence"]
    dispatched_current = dispatched["document_context"]["current_blocks"][0]
    source_current = semantic_request["document_context"]["current_blocks"][0]
    assert {
        key: dispatched_current[key]
        for key in (
            "evidence_id", "kind", "previous_evidence_id", "next_evidence_id"
        )
    } == {
        key: source_current[key]
        for key in (
            "evidence_id", "kind", "previous_evidence_id", "next_evidence_id"
        )
    }
    assert dispatched["document_context"]["context_blocks"] == []
    assert dispatched["document_context"]["source_context_id"] == (
        semantic_request["document_context"]["source_context_id"]
    )
    assert len({
        document["document_context"]["document_context_id"]
        for document in tokenized_documents
    }) == 3


@pytest.mark.parametrize(
    "base_url",
    [
        "https://127.0.0.1:8000",
        "http://example.test:8000",
        "http://localhost:8000",
        "http://user@localhost:8000",
        "http://localhost:8000/path",
    ],
)
def test_chat_completion_rejects_non_loopback_origin(base_url):
    with pytest.raises(ConceptAPIError, match="CONCEPT_API_CONFIG_INVALID"):
        chat_completions_url(base_url)


@pytest.mark.parametrize(
    ("failure_type", "reason_code"),
    [
        (httpx.ConnectError, "CONCEPT_API_UNAVAILABLE"),
        (httpx.ReadTimeout, "CONCEPT_API_TIMEOUT"),
        (None, "CONCEPT_API_UNAVAILABLE"),
    ],
)
def test_chat_completion_reports_http_unavailable_and_timeout(failure_type, reason_code):
    def fail(request):
        if failure_type is None:
            return httpx.Response(503)
        raise failure_type("public failure", request=request)

    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        with pytest.raises(ConceptAPIError, match=reason_code):
            _request(client)


def test_failed_request_does_not_poison_next_resident_service_request():
    tokenize_calls = 0

    def respond(request):
        nonlocal tokenize_calls
        if request.url.path == "/tokenize":
            tokenize_calls += 1
            if tokenize_calls == 1:
                return httpx.Response(503)
            return httpx.Response(
                200, json={"count": 100, "max_model_len": 8_192}
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"concepts":[]}'},
                    }
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ConceptAPIError, match="CONCEPT_API_UNAVAILABLE"):
            _request(client)
        assert _request(client) == '{"concepts":[]}'

    assert tokenize_calls == 2


@pytest.mark.parametrize(
    ("content", "reason_code"),
    [
        (b'{"choices":', "CONCEPT_API_RESPONSE_INVALID"),
        (b'{"choices":NaN}', "CONCEPT_API_RESPONSE_INVALID"),
        (
            b'{"choices":[{"finish_reason":"length","message":{"content":"x"}}]}',
            "MODEL_OUTPUT_TRUNCATED",
        ),
        (
            b'{"choices":[{"finish_reason":"stop","message":{"content":1}}]}',
            "CONCEPT_API_RESPONSE_INVALID",
        ),
    ],
)
def test_chat_completion_rejects_malformed_or_truncated_response(content, reason_code):
    def respond(request):
        if request.url.path == "/tokenize":
            return httpx.Response(200, json={"count": 100, "max_model_len": 8_192})
        return httpx.Response(200, content=content)

    transport = httpx.MockTransport(respond)
    with httpx.Client(transport=transport) as client:
        with pytest.raises(ConceptAPIError, match=reason_code):
            _request(client)
