import json

import httpx
import pytest

from pdf_evidence.concept_api import (
    ConceptAPIError,
    chat_completions_url,
    request_concept_text,
)
from pdf_evidence.concept_generation import PROMPT_TEMPLATE


def _semantic_request():
    return {
        "schema": "semantic-qualification-input/v1",
        "material_id": "material-public",
        "material_revision": "revision-public",
        "section_id": "section-public",
        "evidence": [],
    }


def test_chat_completion_uses_exact_loopback_request_and_returns_content():
    observed = []

    def respond(request):
        observed.append(request)
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
        model_text = request_concept_text(
            client,
            base_url="http://127.0.0.1:8101",
            model="fixed-model",
            semantic_request=_semantic_request(),
            timeout_seconds=300,
        )

    assert model_text == '{"concepts":[]}'
    assert observed[0].url == "http://127.0.0.1:8101/v1/chat/completions"
    assert "authorization" not in observed[0].headers
    body = json.loads(observed[0].content)
    assert body == {
        "model": "fixed-model",
        "messages": [
            {
                "role": "user",
                "content": f"{PROMPT_TEMPLATE}\nINPUT:\n"
                '{"evidence":[],"material_id":"material-public",'
                '"material_revision":"revision-public",'
                '"schema":"semantic-qualification-input/v1",'
                '"section_id":"section-public"}',
            }
        ],
        "temperature": 0,
        "max_tokens": 1536,
    }


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
            request_concept_text(
                client,
                base_url="http://localhost:8101",
                model="fixed-model",
                semantic_request=_semantic_request(),
                timeout_seconds=300,
            )


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
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=content))
    with httpx.Client(transport=transport) as client:
        with pytest.raises(ConceptAPIError, match=reason_code):
            request_concept_text(
                client,
                base_url="http://localhost:8101",
                model="fixed-model",
                semantic_request=_semantic_request(),
                timeout_seconds=300,
            )
