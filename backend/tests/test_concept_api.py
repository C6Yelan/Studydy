import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

import pdf_evidence.concept_api as concept_api_module
from pdf_evidence.concept_api import (
    CONCEPT_RESPONSE_FORMAT,
    ConceptAPIError,
    chat_completions_url,
    request_concept_text,
    request_structured_text,
    start_concept_server,
)
from pdf_evidence.ocr_page_evidence import canonical_sha256


RUNTIME_LOCK = json.loads(
    (Path(__file__).parents[2] / "local_ai" / "runtime-lock.json").read_text(
        encoding="utf-8"
    )
)


def _semantic_request():
    return {
        "schema": "concept-generation-input/v2",
        "evidence": [],
    }


def _request(client, *, base_url="http://localhost:8101"):
    return request_concept_text(
        client,
        base_url=base_url,
        model="fixed-model",
        prompt_template=RUNTIME_LOCK["semantic"]["prompt"],
        semantic_request=_semantic_request(),
        max_model_len=8_192,
        timeout_seconds=300,
    )


def _server_settings():
    return {
        "concept_api_base_url": "http://127.0.0.1:8101",
        "concept_model": "Qwen/Qwen3-14B-AWQ",
        "concept_server_executable": "/runtime/bin/vllm",
        "concept_model_root": "/models/qwen",
        "concept_kv_cache_bytes": 2_147_483_648,
        "concept_max_concurrency": 1,
        "concept_max_model_len": 8_192,
    }


def test_owned_vllm_server_uses_fixed_bounded_command_and_cleans_up(monkeypatch):
    process = MagicMock(pid=1234)
    process.poll.return_value = None
    health = MagicMock()
    health.__enter__.return_value = health
    health.get.return_value = SimpleNamespace(status_code=200)
    popen = MagicMock(return_value=process)
    killpg = MagicMock()
    monkeypatch.setattr(concept_api_module.subprocess, "Popen", popen)
    monkeypatch.setattr(concept_api_module.httpx, "Client", lambda **_: health)
    monkeypatch.setattr(concept_api_module.os, "killpg", killpg)

    server = start_concept_server(_server_settings())
    server.close()
    server.close()

    expected_command = (
        "/runtime/bin/vllm serve /models/qwen --served-model-name "
        "Qwen/Qwen3-14B-AWQ --host 127.0.0.1 --port 8101 "
        "--kv-cache-memory-bytes 2147483648 --max-num-seqs 1 --max-model-len 8192 "
        "--generation-config vllm --enforce-eager"
    ).split()
    assert popen.call_args.args[0] == expected_command
    assert "--enable-log-requests" not in expected_command
    process_options = popen.call_args.kwargs
    assert process_options.pop("start_new_session") is True
    assert process_options.pop("env") == {
        "DO_NOT_TRACK": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "HF_HUB_OFFLINE": "1",
        "PATH": "/usr/bin:/bin",
        "TRANSFORMERS_OFFLINE": "1",
        "VLLM_NO_USAGE_STATS": "1",
        "VLLM_USE_FLASHINFER_SAMPLER": "0",
        "VLLM_USE_V2_MODEL_RUNNER": "0",
    }
    assert set(process_options.values()) == {concept_api_module.subprocess.DEVNULL}
    health.get.assert_called_once_with("http://127.0.0.1:8101/health", timeout=1)
    killpg.assert_called_once_with(1234, concept_api_module.signal.SIGTERM)
    process.wait.assert_called_once_with(timeout=30)


@pytest.mark.parametrize(
    ("process_return_code", "health_error", "moments", "reason_code"),
    [
        (7, None, [0.0], "CONCEPT_API_UNAVAILABLE"),
        (None, httpx.ConnectError("public unavailable"), [0.0, 301.0], "CONCEPT_API_TIMEOUT"),
    ],
)
def test_owned_vllm_server_start_failures_always_cleanup(
    monkeypatch, process_return_code, health_error, moments, reason_code
):
    process = MagicMock(pid=1234)
    process.poll.return_value = process_return_code
    health = MagicMock()
    health.__enter__.return_value = health
    health.get.side_effect = health_error
    killpg = MagicMock()
    monkeypatch.setattr(concept_api_module.subprocess, "Popen", lambda *_, **__: process)
    monkeypatch.setattr(concept_api_module.httpx, "Client", lambda **_: health)
    monkeypatch.setattr(concept_api_module.time, "monotonic", lambda: moments.pop(0))
    monkeypatch.setattr(concept_api_module.time, "sleep", lambda _: None)
    monkeypatch.setattr(concept_api_module.os, "killpg", killpg)

    with pytest.raises(ConceptAPIError, match=reason_code):
        start_concept_server(_server_settings())

    killpg.assert_called_once()
    process.wait.assert_called_once_with(timeout=30)


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
        model_text = _request(client, base_url="http://127.0.0.1:8101")

    assert model_text == '{"concepts":[]}'
    assert observed[0].url == "http://127.0.0.1:8101/tokenize"
    assert json.loads(observed[0].content)["add_generation_prompt"] is True
    assert observed[1].url == "http://127.0.0.1:8101/v1/chat/completions"
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
                '{"evidence":[],"schema":"concept-generation-input/v2"}',
            }
        ],
        "temperature": 0,
        "max_tokens": 1536,
        "response_format": CONCEPT_RESPONSE_FORMAT,
    }


def test_structured_request_can_preserve_fixed_assessment_input_order():
    observed = []

    def respond(request):
        observed.append(request)
        if request.url.path == "/tokenize":
            return httpx.Response(200, json={"count": 10, "max_model_len": 100})
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "{}"},
                    }
                ]
            },
        )

    request_document = {"target_claim": "second", "evidence": "first"}
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        assert request_structured_text(
            client,
            base_url="http://127.0.0.1:8101",
            model="fixed-model",
            prompt_template="fixed prompt",
            request_document=request_document,
            response_format={"type": "json_schema"},
            max_model_len=100,
            max_tokens=20,
            timeout_seconds=30,
            enable_thinking=False,
            preserve_request_order=True,
        ) == "{}"

    body = json.loads(observed[1].content)
    assert body["messages"][0]["content"] == (
        'fixed prompt\nINPUT:\n{"target_claim":"second","evidence":"first"}'
    )
    assert body["chat_template_kwargs"] == {"enable_thinking": False}


def test_tokenizer_budget_rejects_before_generation_call():
    observed_paths = []

    def respond(request):
        observed_paths.append(request.url.path)
        return httpx.Response(200, json={"count": 6_657, "max_model_len": 8_192})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ConceptAPIError, match="MODEL_INPUT_TOO_LARGE"):
            _request(client)
    assert observed_paths == ["/tokenize"]


@pytest.mark.parametrize(
    "base_url",
    [
        "https://127.0.0.1:8101",
        "http://example.test:8101",
        "http://user@localhost:8101",
        "http://localhost:8101/path",
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
