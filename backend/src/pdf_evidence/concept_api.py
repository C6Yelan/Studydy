from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from .concept_generation import PROMPT_TEMPLATE
from .ocr_page_evidence import canonical_bytes


CHAT_COMPLETIONS_PATH = "/v1/chat/completions"
MAX_TOKENS = 1_536
TEMPERATURE = 0
MAX_API_RESPONSE_BYTES = 128 * 1_024
CONCEPT_SERVER_READY_TIMEOUT_SECONDS = 300
_VLLM_ENVIRONMENT = {
    "DO_NOT_TRACK": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "HF_HUB_OFFLINE": "1",
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
    command = [
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
    semantic_request: dict[str, Any],
    timeout_seconds: float,
) -> str:
    """呼叫單一 Chat Completions endpoint，只回傳待驗證的 model text。"""

    if not isinstance(model, str) or not model or len(model) > 256:
        raise ConceptAPIError("CONCEPT_API_CONFIG_INVALID")
    prompt = f"{PROMPT_TEMPLATE}\nINPUT:\n{canonical_bytes(semantic_request).decode('utf-8')}"
    try:
        response = client.post(
            chat_completions_url(base_url),
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": TEMPERATURE,
                "max_tokens": MAX_TOKENS,
            },
            timeout=timeout_seconds,
        )
        response.raise_for_status()
    except httpx.TimeoutException as error:
        raise ConceptAPIError("CONCEPT_API_TIMEOUT") from error
    except (httpx.HTTPStatusError, httpx.RequestError) as error:
        raise ConceptAPIError("CONCEPT_API_UNAVAILABLE") from error
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
