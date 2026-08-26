from __future__ import annotations

import json
from typing import Any

import httpx

from pdf_evidence.concept_api import ConceptAPIError, chat_completions_url


_MAX_RESPONSE_BYTES = 128 * 1024


def _without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ConceptAPIError("CONCEPT_API_RESPONSE_INVALID")
        value[key] = item
    return value


def _reject_constant(_: str) -> None:
    raise ConceptAPIError("CONCEPT_API_RESPONSE_INVALID")


def request_assessment_text(
    client: httpx.Client,
    *,
    base_url: str,
    model: str,
    prompt_template: str,
    request_document: dict[str, Any],
    response_format: dict[str, Any],
    max_model_len: int,
    max_tokens: int,
    timeout_seconds: float,
) -> str:
    """以固定insertion-order input呼叫Assessment structured generation。"""

    if (
        not isinstance(model, str)
        or not model
        or len(model) > 256
        or not isinstance(prompt_template, str)
        or not prompt_template
        or type(max_model_len) is not int
        or type(max_tokens) is not int
        or not 1 <= max_tokens < max_model_len
        or not isinstance(request_document, dict)
        or not isinstance(response_format, dict)
    ):
        raise ConceptAPIError("CONCEPT_API_CONFIG_INVALID")
    try:
        request_bytes = json.dumps(
            request_document,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError):
        raise ConceptAPIError("CONCEPT_API_CONFIG_INVALID") from None
    prompt = f"{prompt_template}\nINPUT:\n{request_bytes.decode('utf-8')}"
    messages = [{"role": "user", "content": prompt}]
    try:
        tokenized = client.post(
            f"{base_url.rstrip('/')}/tokenize",
            json={
                "model": model,
                "messages": messages,
                "add_generation_prompt": True,
                "add_special_tokens": False,
            },
            timeout=timeout_seconds,
        )
        tokenized.raise_for_status()
        if not tokenized.content or len(tokenized.content) > _MAX_RESPONSE_BYTES:
            raise ConceptAPIError("CONCEPT_API_RESPONSE_INVALID")
        token_count = json.loads(
            tokenized.content.decode("utf-8"),
            object_pairs_hook=_without_duplicates,
            parse_constant=_reject_constant,
        )
        if (
            not isinstance(token_count, dict)
            or type(token_count.get("count")) is not int
            or type(token_count.get("max_model_len")) is not int
            or token_count["count"] < 1
            or token_count["max_model_len"] != max_model_len
        ):
            raise ConceptAPIError("CONCEPT_API_RESPONSE_INVALID")
        if token_count["count"] + max_tokens > max_model_len:
            raise ConceptAPIError("MODEL_INPUT_TOO_LARGE")
        response = client.post(
            chat_completions_url(base_url),
            json={
                "model": model,
                "messages": messages,
                "temperature": 0,
                "max_tokens": max_tokens,
                "response_format": response_format,
                "chat_template_kwargs": {"enable_thinking": False},
            },
            timeout=timeout_seconds,
        )
        response.raise_for_status()
    except httpx.TimeoutException as error:
        raise ConceptAPIError("CONCEPT_API_TIMEOUT") from error
    except (httpx.HTTPStatusError, httpx.RequestError) as error:
        raise ConceptAPIError("CONCEPT_API_UNAVAILABLE") from error
    except (RecursionError, UnicodeDecodeError, ValueError):
        raise ConceptAPIError("CONCEPT_API_RESPONSE_INVALID") from None
    if not response.content or len(response.content) > _MAX_RESPONSE_BYTES:
        raise ConceptAPIError("MODEL_OUTPUT_TOO_LARGE")
    try:
        api_response = json.loads(
            response.content.decode("utf-8"),
            object_pairs_hook=_without_duplicates,
            parse_constant=_reject_constant,
        )
    except ConceptAPIError:
        raise
    except (RecursionError, UnicodeDecodeError, ValueError):
        raise ConceptAPIError("CONCEPT_API_RESPONSE_INVALID") from None
    choices = api_response.get("choices") if isinstance(api_response, dict) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ConceptAPIError("CONCEPT_API_RESPONSE_INVALID")
    first_choice = choices[0]
    if first_choice.get("finish_reason") != "stop":
        raise ConceptAPIError("MODEL_OUTPUT_TRUNCATED")
    message = first_choice.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise ConceptAPIError("CONCEPT_API_RESPONSE_INVALID")
    return message["content"]
