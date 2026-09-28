from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
import json
import os
import socket
from threading import Event, Lock, Thread
from typing import Any
from urllib.parse import urlsplit

import httpx

from pdf_evidence.ocr_page_evidence import canonical_bytes


API_KEY_ENV = "VLLM_API_KEY"
SERVICE_URL_ENV = "STUDYDY_SEMANTIC_BASE_URL"
CHAT_PATH = "/v1/chat/completions"
TOKENIZE_PATH = "/tokenize"
PREFLIGHT_TIMEOUT_SECONDS = 5
MAX_RESPONSE_BYTES = 1024 * 1024
INFERENCE_TIMEOUT = httpx.Timeout(None, connect=PREFLIGHT_TIMEOUT_SECONDS)
_CANCELLATION_POLL_SECONDS = 0.1
MIN_OUTPUT_TOKENS = {
    "material_semantics": 8192, "material_review": 8192,
    "assessment": 16384, "assessment_check": 16384,
}


class SemanticServiceError(RuntimeError):
    """只攜帶固定 reason code，不攜帶教材或模型回應。"""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.request_metadata: dict[str, Any] | None = None


def _output_budget(task: str, ceiling: int, input_tokens: int, context: int) -> int:
    # 輸出可使用剩餘上下文；輸入打包仍須保留各任務的最低輸出空間。
    budget = min(ceiling, context - input_tokens)
    if budget < min(ceiling, MIN_OUTPUT_TOKENS[task]):
        raise SemanticServiceError("SEMANTIC_INPUT_TOO_LARGE")
    return budget


def _origin(value: Any) -> str:
    if not isinstance(value, str) or not value or any(ord(char) <= 32 or ord(char) == 127 for char in value):
        raise SemanticServiceError("SEMANTIC_SERVICE_CONFIG_INVALID")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise SemanticServiceError("SEMANTIC_SERVICE_CONFIG_INVALID") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or (port is not None and not 1 <= port <= 65_535)
    ):
        raise SemanticServiceError("SEMANTIC_SERVICE_CONFIG_INVALID")
    return value.rstrip("/")


def _headers(environment: Mapping[str, str] | None = None) -> dict[str, str]:
    value = (os.environ if environment is None else environment).get(API_KEY_ENV)
    if value is None or value == "":
        return {}
    if not value or len(value) > 4096 or any(character in value for character in "\x00\r\n"):
        raise SemanticServiceError("SEMANTIC_SERVICE_CONFIG_INVALID")
    return {"Authorization": f"Bearer {value}"}


def semantic_client(*, environment: Mapping[str, str] | None = None) -> httpx.Client:
    return httpx.Client(
        headers=_headers(environment),
        trust_env=False,
        follow_redirects=False,
        timeout=INFERENCE_TIMEOUT,
    )


def _post(client, url, *, json, cancellation_check=None):
    """取消時解除本地 socket wait；不宣稱遠端 generation 已停止。"""
    if cancellation_check is None:
        return client.post(url, json=json)
    cancellation_check()
    done = Event()
    lock = Lock()
    connections = []
    cancelled = []

    def interrupt():
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def trace(event, info):
        if event in {"connection.connect_tcp.complete", "connection.start_tls.complete"}:
            connection = info["return_value"].get_extra_info("socket")
            with lock:
                if connection is not None:
                    connections.append(connection)
                if cancelled:
                    interrupt()
                    raise cancelled[0]

    def watch():
        while not done.wait(_CANCELLATION_POLL_SECONDS):
            try:
                cancellation_check()
            except Exception as error:
                with lock:
                    cancelled.append(error)
                    interrupt()
                return

    watcher = Thread(target=watch, name="studydy-semantic-cancellation", daemon=True)
    watcher.start()
    try:
        # 每個 inference HTTP request 都結束其連線，下一次不會重用未經 trace 的 socket。
        response = client.post(url, json=json, headers={"Connection": "close"}, extensions={"trace": trace})
        cancellation_check()
        return response
    finally:
        done.set()
        watcher.join()
        if cancelled:
            raise cancelled[0]


def _service(lock: Any) -> dict[str, Any]:
    try:
        service = lock["semantic_service"]
        origin = _origin(service["base_url"])
        if (
            lock["python"] != "3.12"
            or not isinstance(service["model_id"], str) or not service["model_id"].strip()
            or not isinstance(service["revision"], str) or not service["revision"].strip()
            or origin != "http://127.0.0.1:18000"
            or service["max_model_len"] != 32768
            or service["max_num_seqs"] != 1
            or service["authentication"] != "environment-bearer:VLLM_API_KEY"
            or service["server"]["python"] != "3.12"
        ):
            raise SemanticServiceError("SEMANTIC_SERVICE_CONFIG_INVALID")
        # 位址是部署設定；不改寫已保存的 lock／binding 或其內容 hash。
        endpoint = _origin(os.environ.get(SERVICE_URL_ENV, origin))
        return {**service, "base_url": endpoint}
    except (KeyError, TypeError):
        raise SemanticServiceError("SEMANTIC_SERVICE_CONFIG_INVALID") from None


def preflight_semantic_service(
    runtime_lock: dict[str, Any], *, client: httpx.Client | None = None
) -> None:
    """核對 vLLM 版本、模型及 32K tokenizer 契約。"""

    service = _service(runtime_lock)
    owned = client is None
    http = semantic_client() if client is None else client
    try:
        health = http.get(
            f"{service['base_url']}/health", timeout=PREFLIGHT_TIMEOUT_SECONDS
        )
        version = http.get(
            f"{service['base_url']}/version", timeout=PREFLIGHT_TIMEOUT_SECONDS
        )
        models = http.get(
            f"{service['base_url']}/v1/models", timeout=PREFLIGHT_TIMEOUT_SECONDS
        )
        tokenized = http.post(
            f"{service['base_url']}{TOKENIZE_PATH}",
            json={
                "model": service["model_id"],
                "messages": [{"role": "user", "content": "ready"}],
                "add_generation_prompt": True,
                "add_special_tokens": False,
                "chat_template_kwargs": {"enable_thinking": True},
            },
            timeout=PREFLIGHT_TIMEOUT_SECONDS,
        )
        for response in (health, version, models, tokenized):
            response.raise_for_status()
        version_body = version.json()
        model_body = models.json()
        token_body = tokenized.json()
    except httpx.TimeoutException as error:
        raise SemanticServiceError("SEMANTIC_SERVICE_TIMEOUT") from error
    except (httpx.HTTPError, UnicodeError, ValueError) as error:
        raise SemanticServiceError("SEMANTIC_SERVICE_UNAVAILABLE") from error
    finally:
        if owned:
            http.close()
    served = model_body.get("data") if isinstance(model_body, dict) else None
    model = next(
        (
            item
            for item in served or []
            if isinstance(item, dict) and item.get("id") == service["model_id"]
        ),
        None,
    )
    if (
        not isinstance(version_body, dict)
        or version_body.get("version") != service["server"]["version"]
        or not isinstance(served, list)
        or len(served) != 1
        or model is None
        or model.get("max_model_len") != service["max_model_len"]
        or not isinstance(token_body, dict)
        or type(token_body.get("count")) is not int
        or token_body["count"] < 1
        or token_body.get("max_model_len") != service["max_model_len"]
    ):
        raise SemanticServiceError("SEMANTIC_SERVICE_IDENTITY_MISMATCH")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise SemanticServiceError("SEMANTIC_RESPONSE_INVALID")
        value[key] = item
    return value


def _reject_constant(_: str) -> None:
    raise SemanticServiceError("SEMANTIC_RESPONSE_INVALID")


def _messages(prompt: str, request: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "role": "user",
            "content": f"{prompt}\nINPUT:\n{canonical_bytes(request).decode('utf-8')}",
        }
    ]


def _token_count(
    client: httpx.Client,
    service: dict[str, Any],
    messages: list[dict[str, str]],
    chat_template_kwargs: dict[str, Any] | None = None,
    cancellation_check: Callable[[], None] | None = None,
) -> int:
    response = _post(client,
        f"{service['base_url']}{TOKENIZE_PATH}",
        json={
            "model": service["model_id"],
            "messages": messages,
            "add_generation_prompt": True,
            "add_special_tokens": False,
            "chat_template_kwargs": {"enable_thinking": True},
            **({"chat_template_kwargs": chat_template_kwargs} if chat_template_kwargs is not None else {}),
        },
        cancellation_check=cancellation_check,
    )
    response.raise_for_status()
    body = response.json()
    if (
        not isinstance(body, dict)
        or type(body.get("count")) is not int
        or body["count"] < 1
        or body.get("max_model_len") != service["max_model_len"]
    ):
        raise SemanticServiceError("SEMANTIC_RESPONSE_INVALID")
    return body["count"]


def request_semantics(
    client: httpx.Client,
    *,
    runtime_lock: dict[str, Any],
    task: str,
    request: dict[str, Any],
    response_schema: dict[str, Any],
    cancellation_check: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """透過共用 HTTP 邊界請求語意結果。"""

    service = _service(runtime_lock)
    try:
        task_lock = runtime_lock["assessment" if task == "assessment_check" else task]
        prefix = "check_" if task == "assessment_check" else ""
        prompt = task_lock[prefix + "prompt"]
        max_tokens = task_lock[prefix + "max_tokens"]
        if (
            task not in {"material_semantics", "material_review", "assessment", "assessment_check"}
            or not isinstance(prompt, str)
            or not prompt
            or type(max_tokens) is not int
            or not 1 <= max_tokens <= service["max_model_len"]
            or not isinstance(request, dict)
            or not isinstance(response_schema, dict)
        ):
            raise SemanticServiceError("SEMANTIC_SERVICE_CONFIG_INVALID")
        messages = _messages(prompt, request)
        generation = deepcopy(task_lock[prefix + "generation"])
        input_tokens = _token_count(client, service, messages, generation.get("chat_template_kwargs"), cancellation_check)
        max_tokens = _output_budget(task, max_tokens, input_tokens, service["max_model_len"])
        response = _post(client,
            f"{service['base_url']}{CHAT_PATH}",
            json={
                **generation,
                "model": service["model_id"],
                "messages": messages,
                "max_tokens": max_tokens,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": task,
                        "strict": True,
                        "schema": deepcopy(response_schema),
                    },
                },
            },
            cancellation_check=cancellation_check,
        )
        response.raise_for_status()
    except SemanticServiceError:
        raise
    except httpx.TimeoutException as error:
        raise SemanticServiceError("SEMANTIC_SERVICE_TIMEOUT") from error
    except (httpx.HTTPError, UnicodeError, ValueError) as error:
        raise SemanticServiceError("SEMANTIC_SERVICE_UNAVAILABLE") from error
    if not response.content or len(response.content) > MAX_RESPONSE_BYTES:
        raise SemanticServiceError("SEMANTIC_RESPONSE_INVALID")
    try:
        api_body = json.loads(
            response.content.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
        choice = api_body["choices"][0]
        if choice.get("finish_reason") != "stop":
            error = SemanticServiceError("SEMANTIC_OUTPUT_TRUNCATED")
            finish_reason = choice.get("finish_reason")
            error.request_metadata = {
                "task": task,
                "input_tokens": input_tokens,
                "max_tokens": max_tokens,
                "finish_reason": finish_reason if finish_reason in (
                    "length", "content_filter", "tool_calls", "function_call",
                ) else "other",
            }
            usage = api_body.get("usage")
            if isinstance(usage, dict):
                error.request_metadata.update({
                    key: usage[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                    if type(usage.get(key)) is int and usage[key] >= 0
                })
            raise error
        content = choice["message"]["content"]
        result = json.loads(
            content,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except SemanticServiceError:
        raise
    except (IndexError, KeyError, TypeError, UnicodeError, ValueError) as error:
        raise SemanticServiceError("SEMANTIC_RESPONSE_INVALID") from error
    if not isinstance(result, dict):
        raise SemanticServiceError("SEMANTIC_RESPONSE_INVALID")
    return result


def material_request_fits(
    client: httpx.Client, runtime_lock: dict[str, Any], request: dict[str, Any],
    *, cancellation_check: Callable[[], None] | None = None,
) -> bool:
    """以模型服務 tokenizer 計算分批容量；不截斷單一來源區塊。"""

    service = _service(runtime_lock)
    task = runtime_lock["material_semantics"]
    try:
        count = _token_count(client, service, _messages(task["prompt"], request), task["generation"]["chat_template_kwargs"], cancellation_check)
        try:
            _output_budget("material_semantics", task["max_tokens"], count, service["max_model_len"])
        except SemanticServiceError:
            return False
        # 原始 block 不截斷；多個 block 分批，避免新教材擠爆固定輸出預算。
        if sum(len(section["evidence"]) for section in request["sections"]) == 1:
            return True
        new_count = count
        if request.get("existing_concepts"):
            fresh_request = {**request, "existing_concepts": []}
            new_count = _token_count(client, service, _messages(task["prompt"], fresh_request), task["generation"]["chat_template_kwargs"], cancellation_check)
    except httpx.TimeoutException as error:
        raise SemanticServiceError("SEMANTIC_SERVICE_TIMEOUT") from error
    except (httpx.HTTPError, UnicodeError, ValueError) as error:
        raise SemanticServiceError("SEMANTIC_SERVICE_UNAVAILABLE") from error
    return new_count <= task["max_new_input_tokens"]
