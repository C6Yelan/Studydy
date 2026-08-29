from __future__ import annotations

from copy import deepcopy
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from .ocr_page_evidence import canonical_bytes, canonical_sha256


CHAT_COMPLETIONS_PATH = "/v1/chat/completions"
TOKENIZE_PATH = "/tokenize"
MAX_TOKENS = 1_536
TEMPERATURE = 0
MAX_API_RESPONSE_BYTES = 128 * 1_024
CONCEPT_SERVER_READY_TIMEOUT_SECONDS = 300
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
                        "required": [
                            "label", "definition", "key_points"
                        ],
                        "properties": {
                            "label": {"type": "string", "minLength": 1},
                            "definition": {
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
                            "key_points": {
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
_VLLM_ENVIRONMENT = {
    "DO_NOT_TRACK": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "HF_HUB_OFFLINE": "1",
    "PATH": "/usr/bin:/bin",
    "TRANSFORMERS_OFFLINE": "1",
    "VLLM_NO_USAGE_STATS": "1",
    "VLLM_USE_FLASHINFER_SAMPLER": "0",
    "VLLM_USE_V2_MODEL_RUNNER": "0",
}


class ConceptAPIError(RuntimeError):
    """只保存固定 reason code，不保存教材或 API 回應。"""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class LocalConceptServer:
    """關閉 runner 啟動的 vLLM process group，避免模型留在 GPU。"""

    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        self._process = process
        self._is_closed = False

    def close(self) -> None:
        if self._is_closed:
            return
        try:
            os.killpg(self._process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError as error:
            raise ConceptAPIError("CONCEPT_API_UNAVAILABLE") from error
        try:
            self._process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(self._process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError as error:
                raise ConceptAPIError("CONCEPT_API_UNAVAILABLE") from error
            try:
                self._process.wait(timeout=30)
            except (OSError, subprocess.TimeoutExpired) as error:
                raise ConceptAPIError("CONCEPT_API_UNAVAILABLE") from error
        self._is_closed = True


def start_concept_server(settings: dict[str, Any]) -> LocalConceptServer:
    """以固定 vLLM CLI 啟動 loopback server，ready 前不送教材。"""

    base_url = settings["concept_api_base_url"]
    chat_completions_url(base_url)
    parsed = urlsplit(base_url)
    port = parsed.port or 80
    model_command = [
        settings["concept_server_executable"],
        "serve",
        settings["concept_model_root"],
        "--served-model-name",
        settings["concept_model"],
        "--host",
        parsed.hostname or "127.0.0.1",
        "--port",
        str(port),
        "--kv-cache-memory-bytes",
        str(settings["concept_kv_cache_bytes"]),
        "--max-num-seqs",
        str(settings["concept_max_concurrency"]),
        "--max-model-len",
        str(settings["concept_max_model_len"]),
        "--generation-config",
        "vllm",
        "--enforce-eager",
    ]
    command = [
        sys.executable,
        str(Path(__file__).with_name("process_guard.py")),
        str(os.getpid()),
        *model_command,
    ]
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=_VLLM_ENVIRONMENT,
        )
    except OSError as error:
        raise ConceptAPIError("CONCEPT_API_UNAVAILABLE") from error
    server = LocalConceptServer(process)
    deadline = time.monotonic() + CONCEPT_SERVER_READY_TIMEOUT_SECONDS
    try:
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            while True:
                if process.poll() is not None:
                    raise ConceptAPIError("CONCEPT_API_UNAVAILABLE")
                try:
                    response = client.get(f"{base_url.rstrip('/')}/health", timeout=1)
                    if response.status_code == 200:
                        return server
                except httpx.RequestError:
                    pass
                if time.monotonic() >= deadline:
                    raise ConceptAPIError("CONCEPT_API_TIMEOUT")
                time.sleep(0.1)
    except BaseException:
        server.close()
        raise


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
        or parsed.hostname not in {"127.0.0.1", "localhost"}
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
