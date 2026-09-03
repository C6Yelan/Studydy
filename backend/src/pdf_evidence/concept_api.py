from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import json
import os
from typing import Any
from urllib.parse import urlsplit

import httpx

from .ocr_page_evidence import canonical_bytes, canonical_sha256


CHAT_COMPLETIONS_PATH = "/v1/chat/completions"
HEALTH_PATH = "/health"
MODELS_PATH = "/v1/models"
TOKENIZE_PATH = "/tokenize"
VERSION_PATH = "/version"
MAX_TOKENS = 1_536
TEMPERATURE = 0
MAX_API_RESPONSE_BYTES = 128 * 1_024
SEMANTIC_SERVICE_PREFLIGHT_TIMEOUT_SECONDS = 5
SEMANTIC_API_KEY_ENVIRONMENT = "VLLM_API_KEY"
CONCEPT_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "studydy_page_concepts",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["concepts"],
            "properties": {
                "concepts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["label", "claims"],
                        "properties": {
                            "label": {"type": "string", "minLength": 1},
                            "claims": {
                                "type": "array",
                                "minItems": 1,
                                "items": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": ["text", "evidence_ids"],
                                    "properties": {
                                        "text": {"type": "string", "minLength": 1},
                                        "evidence_ids": {
                                            "type": "array",
                                            "minItems": 1,
                                            "items": {"type": "string", "minLength": 1},
                                        },
                                    },
                                },
                            },
                        },
                    },
                }
            },
        },
    },
}


class ConceptAPIError(RuntimeError):
    """只保存固定 reason code，不保存教材或 API 回應。"""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def _semantic_service_headers(
    environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Bearer 只從 process environment 讀取，不進設定、binding 或 log。"""

    values = os.environ if environment is None else environment
    api_key = values.get(SEMANTIC_API_KEY_ENVIRONMENT)
    if api_key is None:
        return {}
    if (
        not isinstance(api_key, str)
        or not api_key
        or len(api_key) > 4_096
        or any(character in api_key for character in "\x00\r\n")
    ):
        raise ConceptAPIError("CONCEPT_API_CONFIG_INVALID")
    return {"Authorization": f"Bearer {api_key}"}


def semantic_service_client(
    *, environment: Mapping[str, str] | None = None
) -> httpx.Client:
    """建立只連固定 loopback contract 的短生命週期 HTTP client。"""

    return httpx.Client(
        headers=_semantic_service_headers(environment),
        trust_env=False,
        follow_redirects=False,
    )


def preflight_semantic_service(
    settings: dict[str, Any], *, client: httpx.Client | None = None
) -> None:
    """驗證既有 vLLM readiness、版本、模型 identity 與 32K tokenizer contract。"""

    try:
        base_url = settings["concept_api_base_url"]
        model = settings["concept_model"]
        max_model_len = settings["concept_max_model_len"]
        semantic_lock = settings["runtime_lock"]["semantic"]
        server_version = semantic_lock["server"]["version"]
        if (
            chat_completions_url(base_url)
            != f"{base_url}{semantic_lock['api_path']}"
            or base_url != "http://127.0.0.1:8000"
            or model != semantic_lock["model_id"]
            or max_model_len != semantic_lock["service"]["max_model_len"]
            or semantic_lock["input_token_budget"]["api_path"] != TOKENIZE_PATH
        ):
            raise ConceptAPIError("CONCEPT_API_CONFIG_INVALID")
    except (KeyError, TypeError):
        raise ConceptAPIError("CONCEPT_API_CONFIG_INVALID") from None

    owned_client = client is None
    semantic_client = semantic_service_client() if client is None else client
    try:
        health = semantic_client.get(
            f"{base_url}{HEALTH_PATH}",
            timeout=SEMANTIC_SERVICE_PREFLIGHT_TIMEOUT_SECONDS,
        )
        health.raise_for_status()
        version_response = semantic_client.get(
            f"{base_url}{VERSION_PATH}",
            timeout=SEMANTIC_SERVICE_PREFLIGHT_TIMEOUT_SECONDS,
        )
        version_response.raise_for_status()
        models_response = semantic_client.get(
            f"{base_url}{MODELS_PATH}",
            timeout=SEMANTIC_SERVICE_PREFLIGHT_TIMEOUT_SECONDS,
        )
        models_response.raise_for_status()
        chat_response = semantic_client.options(
            chat_completions_url(base_url),
            timeout=SEMANTIC_SERVICE_PREFLIGHT_TIMEOUT_SECONDS,
        )
        if chat_response.status_code not in {200, 405}:
            chat_response.raise_for_status()
        tokenize_response = semantic_client.post(
            f"{base_url}{TOKENIZE_PATH}",
            json={
                "model": model,
                "messages": [{"role": "user", "content": "ready"}],
                "add_generation_prompt": True,
                "add_special_tokens": False,
            },
            timeout=SEMANTIC_SERVICE_PREFLIGHT_TIMEOUT_SECONDS,
        )
        tokenize_response.raise_for_status()
        version = version_response.json()
        models = models_response.json()
        tokenized = tokenize_response.json()
    except httpx.TimeoutException as error:
        raise ConceptAPIError("CONCEPT_API_TIMEOUT") from error
    except (httpx.HTTPError, RecursionError, UnicodeDecodeError, ValueError) as error:
        raise ConceptAPIError("CONCEPT_API_UNAVAILABLE") from error
    finally:
        if owned_client:
            semantic_client.close()

    served_models = models.get("data") if isinstance(models, dict) else None
    matching_model = (
        next(
            (
                served
                for served in served_models
                if isinstance(served, dict) and served.get("id") == model
            ),
            None,
        )
        if isinstance(served_models, list)
        else None
    )
    if (
        not isinstance(version, dict)
        or version.get("version") != server_version
        or matching_model is None
        or matching_model.get("max_model_len") != max_model_len
        or not isinstance(tokenized, dict)
        or type(tokenized.get("count")) is not int
        or tokenized["count"] < 1
        or tokenized.get("max_model_len") != max_model_len
    ):
        raise ConceptAPIError("CONCEPT_API_IDENTITY_MISMATCH")


def chat_completions_url(base_url: Any) -> str:
    """只接受無 credentials 的本機 HTTP origin。"""

    if not isinstance(base_url, str) or not base_url or "\x00" in base_url:
        raise ConceptAPIError("CONCEPT_API_CONFIG_INVALID")
    parsed = urlsplit(base_url)
    try:
        port = parsed.port
    except ValueError:
        raise ConceptAPIError("CONCEPT_API_CONFIG_INVALID") from None
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or (port is not None and not 1 <= port <= 65_535)
    ):
        raise ConceptAPIError("CONCEPT_API_CONFIG_INVALID")
    return f"{base_url.rstrip('/')}{CHAT_COMPLETIONS_PATH}"


def _tokenize_url(base_url: str) -> str:
    chat_completions_url(base_url)
    return f"{base_url.rstrip('/')}{TOKENIZE_PATH}"


def _without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ConceptAPIError("CONCEPT_API_RESPONSE_INVALID")
        value[key] = item
    return value


def _reject_constant(_: str) -> None:
    raise ConceptAPIError("CONCEPT_API_RESPONSE_INVALID")


def request_concept_text(
    client: httpx.Client,
    *,
    base_url: str,
    model: str,
    prompt_template: str,
    semantic_request: dict[str, Any],
    max_model_len: int,
    timeout_seconds: float,
    already_fitted: bool = False,
) -> str:
    return request_structured_text(
        client,
        base_url=base_url,
        model=model,
        prompt_template=prompt_template,
        request_document=semantic_request,
        response_format=CONCEPT_RESPONSE_FORMAT,
        max_model_len=max_model_len,
        max_tokens=MAX_TOKENS,
        timeout_seconds=timeout_seconds,
        request_is_fitted=already_fitted,
    )


def fit_concept_request(
    client: httpx.Client,
    *,
    base_url: str,
    model: str,
    prompt_template: str,
    semantic_request: dict[str, Any],
    max_model_len: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    """以 server tokenizer 移除 optional context，保留完整 envelope。"""

    try:
        return _fit_request_document(
            client,
            base_url=base_url,
            model=model,
            prompt_template=prompt_template,
            request_document=semantic_request,
            max_model_len=max_model_len,
            max_tokens=MAX_TOKENS,
            timeout_seconds=timeout_seconds,
        )
    except httpx.TimeoutException as error:
        raise ConceptAPIError("CONCEPT_API_TIMEOUT") from error
    except (httpx.HTTPStatusError, httpx.RequestError) as error:
        raise ConceptAPIError("CONCEPT_API_UNAVAILABLE") from error
    except (RecursionError, UnicodeDecodeError, ValueError):
        raise ConceptAPIError("CONCEPT_API_RESPONSE_INVALID") from None


def request_structured_text(
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
    enable_thinking: bool | None = None,
    request_is_fitted: bool = False,
) -> str:
    """以同一個本機 server tokenizer 驗證 budget，再取得固定 schema JSON。"""

    if (
        not isinstance(model, str)
        or not model
        or len(model) > 256
        or not isinstance(prompt_template, str)
        or not prompt_template
        or type(max_model_len) is not int
        or type(max_tokens) is not int
        or not 1 <= max_tokens < max_model_len
        or not isinstance(response_format, dict)
        or (enable_thinking is not None and type(enable_thinking) is not bool)
        or type(request_is_fitted) is not bool
    ):
        raise ConceptAPIError("CONCEPT_API_CONFIG_INVALID")
    try:
        fitted_request = (
            deepcopy(request_document)
            if request_is_fitted
            else _fit_request_document(
                client,
                base_url=base_url,
                model=model,
                prompt_template=prompt_template,
                request_document=request_document,
                max_model_len=max_model_len,
                max_tokens=max_tokens,
                timeout_seconds=timeout_seconds,
            )
        )
        messages = _request_messages(prompt_template, fitted_request)
        request_body = {
            "model": model,
            "messages": messages,
            "temperature": TEMPERATURE,
            "max_tokens": max_tokens,
            "response_format": response_format,
        }
        if enable_thinking is not None:
            request_body["chat_template_kwargs"] = {
                "enable_thinking": enable_thinking
            }
        response = client.post(
            chat_completions_url(base_url),
            json=request_body,
            timeout=timeout_seconds,
        )
        response.raise_for_status()
    except httpx.TimeoutException as error:
        raise ConceptAPIError("CONCEPT_API_TIMEOUT") from error
    except (httpx.HTTPStatusError, httpx.RequestError) as error:
        raise ConceptAPIError("CONCEPT_API_UNAVAILABLE") from error
    except (RecursionError, UnicodeDecodeError, ValueError):
        raise ConceptAPIError("CONCEPT_API_RESPONSE_INVALID") from None
    if not response.content or len(response.content) > MAX_API_RESPONSE_BYTES:
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
    if not isinstance(api_response, dict):
        raise ConceptAPIError("CONCEPT_API_RESPONSE_INVALID")
    choices = api_response.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ConceptAPIError("CONCEPT_API_RESPONSE_INVALID")
    first_choice = choices[0]
    if first_choice.get("finish_reason") != "stop":
        raise ConceptAPIError("MODEL_OUTPUT_TRUNCATED")
    message = first_choice.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise ConceptAPIError("CONCEPT_API_RESPONSE_INVALID")
    return message["content"]


def _request_messages(
    prompt_template: str,
    request_document: dict[str, Any],
) -> list[dict[str, str]]:
    prompt = (
        f"{prompt_template}\nINPUT:\n"
        f"{canonical_bytes(request_document).decode('utf-8')}"
    )
    return [{"role": "user", "content": prompt}]


def _fit_request_document(
    client: httpx.Client,
    *,
    base_url: str,
    model: str,
    prompt_template: str,
    request_document: dict[str, Any],
    max_model_len: int,
    max_tokens: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    """只移除低優先 context；current Evidence 的 batch 邊界不變。"""

    candidate = deepcopy(request_document)
    while True:
        messages = _request_messages(prompt_template, candidate)
        count = _token_count(
            client,
            base_url=base_url,
            model=model,
            messages=messages,
            max_model_len=max_model_len,
            timeout_seconds=timeout_seconds,
        )
        if count + max_tokens <= max_model_len:
            return candidate
        context = candidate.get("document_context")
        context_blocks = (
            context.get("context_blocks") if isinstance(context, dict) else None
        )
        if isinstance(context_blocks, list) and context_blocks:
            removed_id = context_blocks.pop(
                _optional_context_removal_index(context_blocks)
            )["id"]
            for block in context["current_blocks"]:
                block["heading_ancestry_ids"] = [
                    reference
                    for reference in block["heading_ancestry_ids"]
                    if reference != removed_id
                ]
                block["continuation_ids"] = [
                    reference
                    for reference in block["continuation_ids"]
                    if reference != removed_id
                ]
            _reidentify_context_envelope(context)
            continue
        raise ConceptAPIError("MODEL_INPUT_TOO_LARGE")


def _optional_context_removal_index(
    context_blocks: list[dict[str, Any]],
) -> int:
    """依 role priority 移除，previous/next 內再移除離 current 較遠者。"""

    role_rank = {
        "previous_page": 0,
        "next_page": 0,
        "continuation": 1,
        "heading_ancestry": 2,
    }
    return min(
        range(len(context_blocks)),
        key=lambda index: (
            role_rank[context_blocks[index]["role"]],
            (
                index
                if context_blocks[index]["role"] == "previous_page"
                else -index
            ),
        ),
    )


def _reidentify_context_envelope(context: dict[str, Any]) -> None:
    identity = {
        key: value
        for key, value in context.items()
        if key != "document_context_id"
    }
    context["document_context_id"] = (
        "concept-context:sha256:" + canonical_sha256(identity)
    )


def _token_count(
    client: httpx.Client,
    *,
    base_url: str,
    model: str,
    messages: list[dict[str, str]],
    max_model_len: int,
    timeout_seconds: float,
) -> int:
    tokenized = client.post(
        _tokenize_url(base_url),
        json={
            "model": model,
            "messages": messages,
            "add_generation_prompt": True,
            "add_special_tokens": False,
        },
        timeout=timeout_seconds,
    )
    tokenized.raise_for_status()
    if not tokenized.content or len(tokenized.content) > MAX_API_RESPONSE_BYTES:
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
    return token_count["count"]
