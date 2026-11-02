from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from contextlib import nullcontext
from contextvars import ContextVar
import json
import os
import re
import socket
import time
from threading import Event, Lock, Thread
from typing import Any
from urllib.parse import urlsplit

import httpx

from content_identity import canonical_bytes


_request_slots = ContextVar("podcast_request_slots", default=None)

API_KEY_ENV = "VLLM_API_KEY"
SERVICE_URL_ENV = "STUDYDY_SEMANTIC_BASE_URL"
CHAT_PATH = "/v1/chat/completions"
TOKENIZE_PATH = "/tokenize"
PREFLIGHT_TIMEOUT_SECONDS = 5
MAX_RESPONSE_BYTES = 1024 * 1024
INFERENCE_TIMEOUT = httpx.Timeout(None, connect=PREFLIGHT_TIMEOUT_SECONDS)
_CANCELLATION_POLL_SECONDS = 0.1
MIN_OUTPUT_TOKENS = {
    "material_semantics": 8192,
    "material_review": 8192,
    "assessment": 16384,
    "assessment_check": 16384,
    "concept_card": 4096,
}
# 候選目錄最多占 8K tokens；原始來源與最低輸出空間仍各自檢查。
MAX_MATERIAL_CATALOG_TOKENS = 8192
# 截斷多半是模型在字串內重複到上限；重試一次並縮小該次輸出上限，讓重複較早停止。
TRUNCATION_RETRY_MAX_TOKENS = 8192
# 模型服務暫時中斷（連線失敗或 5xx）時原地等待，總時長有上限，不讓整份工作立即失敗。
SERVICE_WAIT_SECONDS = 600
SERVICE_WAIT_FIRST_DELAY = 5
SERVICE_WAIT_MAX_DELAY = 120


class SemanticServiceError(RuntimeError):
    """只攜帶固定 reason code，不攜帶教材或模型回應。"""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.request_metadata: dict[str, Any] | None = None


def _invalid_candidate(error, content, reason, diagnostic):
    if isinstance(content, str):
        from . import model_calls

        error.request_metadata = {**(error.request_metadata or {}), "response_validation": reason}
        model_calls.capture(content, request_metadata={
            **error.request_metadata,
            **{key: value for key, value in diagnostic.items() if key != "candidate"},
        })
        model_calls.rejected(reason, candidate=content)
    return error


def _output_budget(task: str, ceiling: int, input_tokens: int, context: int) -> int:
    # 輸出可使用剩餘上下文；輸入打包仍須保留各任務的最低輸出空間。
    budget = min(ceiling, context - input_tokens)
    if budget < min(ceiling, MIN_OUTPUT_TOKENS[task]):
        raise SemanticServiceError("SEMANTIC_INPUT_TOO_LARGE")
    return budget


def _origin(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value
        or any(ord(char) <= 32 or ord(char) == 127 for char in value)
    ):
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
    if (
        not value
        or len(value) > 4096
        or any(character in value for character in "\x00\r\n")
    ):
        raise SemanticServiceError("SEMANTIC_SERVICE_CONFIG_INVALID")
    return {"Authorization": f"Bearer {value}"}


def semantic_client(*, environment: Mapping[str, str] | None = None) -> httpx.Client:
    return httpx.Client(
        headers=_headers(environment),
        trust_env=False,
        follow_redirects=False,
        timeout=INFERENCE_TIMEOUT,
    )


def _wait_for_service(send, cancellation_check=None):
    """連線失敗或 5xx 時指數退避重送；4xx 與其他錯誤照原樣交給呼叫端。"""
    deadline = time.monotonic() + SERVICE_WAIT_SECONDS
    delay = SERVICE_WAIT_FIRST_DELAY
    while True:
        failure = None
        try:
            response = send()
            if response.status_code < 500:
                return response
        except httpx.TransportError as error:
            failure = error
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            if failure is not None:
                raise failure
            return response
        wake = time.monotonic() + min(delay, remaining)
        while (left := wake - time.monotonic()) > 0:
            if cancellation_check is not None:
                cancellation_check()
            time.sleep(min(left, 1))
        delay = min(delay * 2, SERVICE_WAIT_MAX_DELAY)


def _post(client, url, *, json, cancellation_check=None):
    return _wait_for_service(
        lambda: _post_once(client, url, json=json, cancellation_check=cancellation_check),
        cancellation_check,
    )


def _post_once(client, url, *, json, cancellation_check=None):
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
        if event in {
            "connection.connect_tcp.complete",
            "connection.start_tls.complete",
        }:
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
        response = client.post(
            url, json=json, headers={"Connection": "close"}, extensions={"trace": trace}
        )
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
            or service["api_protocol"] != "openai-chat-completions/v1"
            or not isinstance(service["model_id"], str)
            or not service["model_id"].strip()
            or not isinstance(service["revision"], str)
            or not service["revision"].strip()
            or origin != "http://127.0.0.1:18000"
            or service["max_model_len"] not in (32768, 65536)
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
    """核對 vLLM 版本、模型及 runtime lock 的 tokenizer 容量。"""

    service = _service(runtime_lock)
    owned = client is None
    http = semantic_client() if client is None else client
    try:
        # 服務重啟中先等 /health 恢復，其餘身分檢查仍各送一次。
        health = _wait_for_service(lambda: http.get(
            f"{service['base_url']}/health", timeout=PREFLIGHT_TIMEOUT_SECONDS
        ))
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
    response = _post(
        client,
        f"{service['base_url']}{TOKENIZE_PATH}",
        json={
            "model": service["model_id"],
            "messages": messages,
            "add_generation_prompt": True,
            "add_special_tokens": False,
            "chat_template_kwargs": {"enable_thinking": True},
            **(
                {"chat_template_kwargs": chat_template_kwargs}
                if chat_template_kwargs is not None
                else {}
            ),
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


# 失控輸出的特徵：同一個詞相鄰重複三次以上（「分布分布分布」「matching matching matching」）。
# 兩次相鄰可能是正常中文斷詞（「可以使用使用者」），「一個一個」這類也正常；原文本來就有的寫法也不算。
# 思考殘字由 vLLM reasoning parser 分離，開機健康檢查會拒絕外露，不在這裡逐字過濾。
_REPEATED_TEXT = re.compile(
    r"(?<![A-Za-z])([A-Za-z]{2,30})(?:\s+\1){2,}(?![A-Za-z])"
    r"|((?!一)[\u3400-\u9fff]{2,12})\2{2,}",
    re.IGNORECASE,
)


def _degenerate_text(value: Any, source: str) -> bool:
    """模型產出中有原文沒有的失控疊字時回傳 True。"""
    if isinstance(value, dict):
        return any(_degenerate_text(item, source) for item in value.values())
    if isinstance(value, list):
        return any(_degenerate_text(item, source) for item in value)
    if not isinstance(value, str):
        return False
    for match in _REPEATED_TEXT.finditer(value):
        if match.group(0).strip().casefold() not in source.casefold():
            return True
    return False


def request_semantics(client: httpx.Client, **arguments: Any) -> dict[str, Any]:
    """截斷或疊字最多重試一次；每個候選只保存一次並附上對應請求診斷。"""
    from . import model_calls

    arguments.setdefault("diagnostics", [])

    def metadata():
        diagnostics = arguments.get("diagnostics")
        return {k: v for k, v in diagnostics[-1].items() if k != "candidate"} if diagnostics else {}

    with _request_slots.get() or nullcontext():
        source = canonical_bytes(arguments["request"]).decode("utf-8")
        retried = False
        try:
            result = _request_semantics_once(client, **arguments)
        except SemanticServiceError as error:
            if error.reason_code != "SEMANTIC_OUTPUT_TRUNCATED":
                raise
            result = _request_semantics_once(client, **{**arguments, "max_tokens_cap": TRUNCATION_RETRY_MAX_TOKENS})
            retried = True
        if _degenerate_text(result, source):
            model_calls.capture(result, request_metadata=metadata())
            model_calls.rejected("candidate_degenerate_text")
            if not retried:
                result = _request_semantics_once(client, **arguments)
                if not _degenerate_text(result, source):
                    model_calls.capture(deepcopy(result), request_metadata=metadata())
                    return result
                model_calls.capture(result, request_metadata=metadata())
            model_calls.rejected("SEMANTIC_GENERATION_FAILED")
            raise SemanticServiceError("SEMANTIC_GENERATION_FAILED")
        model_calls.capture(deepcopy(result), request_metadata=metadata())
        return result


def _request_semantics_once(
    client: httpx.Client,
    *,
    runtime_lock: dict[str, Any],
    task: str,
    request: dict[str, Any],
    response_schema: dict[str, Any],
    cancellation_check: Callable[[], None] | None = None,
    usage: dict[str, int] | None = None,
    before_request: Callable[[], None] | None = None,
    tags: dict | None = None,
    max_tokens_cap: int | None = None,
    diagnostics: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    diagnostic = {
        "task": task, "request": deepcopy(request), "input_tokens": None,
        "max_tokens": None, "finish_reason": None, "usage": {},
        "candidate": None, "request_id": None,
    }
    if diagnostics is not None:
        diagnostics.append(diagnostic)
    # read 每次約 6000 字，最多 4000 字說明與有界索引，8192 tokens 足以容納；不沿用整份審查的 32K。
    read_cap = 8192 if task == "material_review" and request.get("task") == "read" else None
    service = _service(runtime_lock)
    try:
        task_lock = runtime_lock["assessment" if task == "assessment_check" else task]
        prefix = "check_" if task == "assessment_check" else ""
        prompt = task_lock[prefix + "prompt"]
        max_tokens = task_lock[prefix + "max_tokens"]
        if (
            task not in MIN_OUTPUT_TOKENS
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
        input_tokens = _token_count(
            client,
            service,
            messages,
            generation.get("chat_template_kwargs"),
            cancellation_check,
        )
        max_tokens = _output_budget(
            task, min(max_tokens, read_cap or max_tokens), input_tokens, service["max_model_len"]
        )
        if max_tokens_cap is not None:
            max_tokens = min(max_tokens, max_tokens_cap)
        diagnostic.update(input_tokens=input_tokens, max_tokens=max_tokens)
        if before_request is not None:
            before_request()
        response = _post(
            client,
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
    content = None
    try:
        api_body = json.loads(
            response.content.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
        if usage is not None and isinstance(api_body.get("usage"), dict):
            usage.update(
                {
                    key: api_body["usage"][key]
                    for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                    if type(api_body["usage"].get(key)) is int
                    and api_body["usage"][key] >= 0
                }
            )
        choice = api_body["choices"][0]
        content = choice["message"]["content"]
        diagnostic.update(
            finish_reason=choice.get("finish_reason"),
            usage=deepcopy(api_body.get("usage") or {}),
            candidate=content, request_id=api_body.get("id"),
        )
        if choice.get("finish_reason") != "stop":
            error = SemanticServiceError("SEMANTIC_OUTPUT_TRUNCATED")
            finish_reason = choice.get("finish_reason")
            error.request_metadata = {
                "task": task,
                "input_tokens": input_tokens,
                "max_tokens": max_tokens,
                "finish_reason": finish_reason
                if finish_reason
                in (
                    "length",
                    "content_filter",
                    "tool_calls",
                    "function_call",
                )
                else "other",
            }
            usage = api_body.get("usage")
            if isinstance(usage, dict):
                error.request_metadata.update(
                    {
                        key: usage[key]
                        for key in (
                            "prompt_tokens",
                            "completion_tokens",
                            "total_tokens",
                        )
                        if type(usage.get(key)) is int and usage[key] >= 0
                    }
                )
            raise _invalid_candidate(error, content, "candidate_output_truncated", diagnostic)
        result = json.loads(
            content,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except SemanticServiceError as error:
        if error.reason_code == "SEMANTIC_RESPONSE_INVALID":
            raise _invalid_candidate(error, content, "candidate_json_invalid", diagnostic)
        raise
    except (IndexError, KeyError, TypeError, UnicodeError, ValueError) as error:
        raise _invalid_candidate(
            SemanticServiceError("SEMANTIC_RESPONSE_INVALID"), content, "candidate_json_invalid", diagnostic
        ) from error
    if not isinstance(result, dict):
        raise _invalid_candidate(
            SemanticServiceError("SEMANTIC_RESPONSE_INVALID"), content, "candidate_not_object", diagnostic
        )
    return result


def material_request_fits(
    client: httpx.Client,
    runtime_lock: dict[str, Any],
    request: dict[str, Any],
    *,
    cancellation_check: Callable[[], None] | None = None,
) -> bool:
    """以模型服務 tokenizer 計算分批容量；不截斷單一來源區塊。"""

    task = runtime_lock["material_semantics"]
    if task["bundle_policy"] == "independent-evidence-blocks/v1":
        from knowledge_map.independent_blocks import request_view

        request = request_view(request)
        fixed = {**request, "sections": [], "source_pages": []}
        if cancellation_check:
            cancellation_check()
        service = _service(runtime_lock)
        try:
            count = _token_count(
                client,
                service,
                _messages(task["prompt"], request),
                task["generation"]["chat_template_kwargs"],
                cancellation_check,
            )
            base_count = _token_count(
                client,
                service,
                _messages(task["prompt"], fixed),
                task["generation"]["chat_template_kwargs"],
                cancellation_check,
            )
        except httpx.TimeoutException:
            raise SemanticServiceError("SEMANTIC_SERVICE_TIMEOUT") from None
        except (httpx.HTTPError, UnicodeError, ValueError):
            raise SemanticServiceError("SEMANTIC_SERVICE_UNAVAILABLE") from None
        context_limit = service["max_model_len"]
        single = sum(len(section["evidence"]) for section in request["sections"]) == 1
        # 不截斷單一 Evidence；保留完整輸出預算及包裝餘裕。
        reserve = min(
            task["max_tokens"], context_limit - MIN_OUTPUT_TOKENS["material_semantics"]
        )
        return (
            single or count - base_count <= task["max_new_input_tokens"]
        ) and count + reserve + 4096 <= context_limit
    service = _service(runtime_lock)
    task = runtime_lock["material_semantics"]
    try:
        count = _token_count(
            client,
            service,
            _messages(task["prompt"], request),
            task["generation"]["chat_template_kwargs"],
            cancellation_check,
        )
        try:
            _output_budget(
                "material_semantics",
                task["max_tokens"],
                count,
                service["max_model_len"],
            )
        except SemanticServiceError:
            return False
        new_count = count
        if request.get("existing_concepts"):
            fresh_request = {**request, "existing_concepts": []}
            new_count = _token_count(
                client,
                service,
                _messages(task["prompt"], fresh_request),
                task["generation"]["chat_template_kwargs"],
                cancellation_check,
            )
            if count - new_count > MAX_MATERIAL_CATALOG_TOKENS:
                return False
        # 原始 block 不截斷；單一區塊仍必須符合總容量與目錄預算。
        if sum(len(section["evidence"]) for section in request["sections"]) == 1:
            return True
    except httpx.TimeoutException as error:
        raise SemanticServiceError("SEMANTIC_SERVICE_TIMEOUT") from error
    except (httpx.HTTPError, UnicodeError, ValueError) as error:
        raise SemanticServiceError("SEMANTIC_SERVICE_UNAVAILABLE") from error
    return new_count <= task["max_new_input_tokens"]


def generate(prompt, schema, *, request=None, cancellation_check=None, cancelled=None):
    """共用具來源核對的文字任務，沿用模型設定、輸出預算與取消邊界。"""
    from .local_ai_config import configured_lock
    from . import podcast_process
    from .provider_http import _cancellation

    lock = deepcopy(configured_lock())
    lock["material_review"] = {**lock["material_review"], "prompt": prompt}

    active = _cancellation.get()

    def check():
        if active is not None:
            active.check()
        podcast_process.check(cancelled)
        if cancellation_check is not None:
            cancellation_check()

    diagnostics = []
    with semantic_client() as client:
        result = request_semantics(
            client, runtime_lock=lock, task="material_review",
            request=request or {}, response_schema=schema, cancellation_check=check,
            diagnostics=diagnostics,
        )
    return result


def text_model_label():
    from .local_ai_config import configured_lock

    return f"vllm:{configured_lock()['semantic_service']['model_id']}"
