"""呼叫本機結構化生成端點並驗證回應。"""

from __future__ import annotations

import hashlib
import http.client
import json
import math
import os
import socket
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request


RESPONSE_SCHEMA = "structured-generation-response/v1"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_DEADLINE_SECONDS = 300
MAX_RETRY_BACKOFF_SECONDS = 30
RETRYABLE_HTTP_STATUSES = {429, 500, 502, 503, 504}

_CONFIG_KEYS = {
    "endpoint_url",
    "cache_dir",
    "deadline_seconds",
    "max_attempts",
    "retry_backoff_seconds",
    "model_id",
    "model_revision",
    "model_artifact_sha256",
    "projector_sha256",
    "runtime_id",
    "processing_policy_version",
}

_RESPONSE_FIELDS = {
    "schema",
    "request_id",
    "operation",
    "runtime_binding_sha256",
    "output",
}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """拒絕 redirect，避免教材內容離開已驗證的 loopback 位址。"""

    def _reject(self, request, response, code, message, headers):
        raise urllib.error.HTTPError(
            request.full_url, code, message, headers, response
        )

    http_error_301 = _reject
    http_error_302 = _reject
    http_error_303 = _reject
    http_error_307 = _reject
    http_error_308 = _reject


def _canonical_bytes(value: Any) -> bytes | None:
    """建立不允許 NaN 的固定 JSON bytes。"""
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError):
        return None


def _canonical_sha256(value: Any) -> str | None:
    """計算固定 JSON 的 SHA-256。"""
    encoded = _canonical_bytes(value)
    return hashlib.sha256(encoded).hexdigest() if encoded is not None else None


def _valid_sha256(value: Any) -> bool:
    """檢查小寫 SHA-256 字串。"""
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _finite_number(value: Any) -> bool:
    """排除 bool、NaN、Infinity 與超大整數。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _valid_config(local_config: Any) -> bool:
    """驗證目前 development loopback 所需的完整設定。"""
    if not isinstance(local_config, dict) or set(local_config) != _CONFIG_KEYS:
        return False
    text_fields = {
        "endpoint_url",
        "cache_dir",
        "model_id",
        "model_revision",
        "runtime_id",
        "processing_policy_version",
    }
    if any(
        not isinstance(local_config[field], str)
        or not local_config[field].strip()
        or "\x00" in local_config[field]
        for field in text_fields
    ):
        return False
    try:
        for field in text_fields:
            local_config[field].encode("utf-8")
        os.fsencode(local_config["cache_dir"])
    except (OSError, UnicodeError, ValueError):
        return False
    if not all(
        _valid_sha256(local_config[field])
        for field in ("model_artifact_sha256", "projector_sha256")
    ):
        return False
    attempts = local_config["max_attempts"]
    deadline = local_config["deadline_seconds"]
    backoff = local_config["retry_backoff_seconds"]
    return (
        isinstance(attempts, int)
        and not isinstance(attempts, bool)
        and 1 <= attempts <= 2
        and _finite_number(deadline)
        and 0 < deadline <= MAX_DEADLINE_SECONDS
        and _finite_number(backoff)
        and 0 <= backoff <= min(deadline, MAX_RETRY_BACKOFF_SECONDS)
    )


def _loopback_endpoint(endpoint_url: str) -> str | None:
    """只接受沒有 DNS、credentials 或額外路徑的 numeric loopback。"""
    if "?" in endpoint_url or "#" in endpoint_url:
        return None
    try:
        parsed = urllib.parse.urlsplit(endpoint_url)
        port = parsed.port
    except (UnicodeError, ValueError):
        return None
    if port is None or not 1 <= port <= 65535:
        return None
    if parsed.hostname == "127.0.0.1":
        expected_netloc = f"127.0.0.1:{port}"
        normalized = expected_netloc
    elif parsed.hostname == "::1":
        expected_netloc = f"[::1]:{port}"
        normalized = expected_netloc
    else:
        return None
    if (
        parsed.scheme != "http"
        or parsed.netloc != expected_netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != ""
        or parsed.query != ""
        or parsed.fragment != ""
    ):
        return None
    return f"http://{normalized}"


def _decode_response(
    response_bytes: bytes,
    request: dict[str, Any],
    runtime_binding_sha256: str,
) -> tuple[Any, str | None]:
    """驗證 frozen response envelope 與 echo binding。"""
    try:
        response = json.loads(response_bytes.decode("utf-8"))
    except (RecursionError, UnicodeDecodeError, ValueError):
        return None, "MODEL_RESPONSE_INVALID_JSON"
    if (
        not isinstance(response, dict)
        or set(response) != _RESPONSE_FIELDS
        or response.get("schema") != RESPONSE_SCHEMA
        or response.get("request_id") != request["request_id"]
        or response.get("operation") != request["operation"]
        or response.get("runtime_binding_sha256") != runtime_binding_sha256
        or not isinstance(response.get("output"), dict)
    ):
        return None, "MODEL_RESPONSE_INVALID_JSON"
    return response["output"], None


def _post_once(
    opener: urllib.request.OpenerDirector,
    endpoint: str,
    request_body: bytes,
    timeout: float,
) -> tuple[bytes | None, str | None, bool]:
    """送出一次 bounded request，並標記是否可重試。"""
    deadline_at = time.monotonic() + timeout
    request = urllib.request.Request(
        f"{endpoint}/v1/structured-generation",
        data=request_body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            if response.getcode() != 200:
                return None, "LOCAL_PROVIDER_AUTH_OR_CONFIG_ERROR", False
            headers = getattr(response, "headers", None)
            content_type = headers.get("Content-Type") if headers is not None else None
            if (
                not isinstance(content_type, str)
                or content_type.split(";", 1)[0].strip().lower()
                != "application/json"
            ):
                return None, "LOCAL_PROVIDER_AUTH_OR_CONFIG_ERROR", False
            content_length = headers.get("Content-Length")
            declared_length = None
            if content_length is not None:
                try:
                    declared_length = int(content_length)
                except (TypeError, ValueError):
                    return None, "LOCAL_PROVIDER_AUTH_OR_CONFIG_ERROR", False
                if declared_length < 0 or declared_length > MAX_RESPONSE_BYTES:
                    return None, "LOCAL_PROVIDER_AUTH_OR_CONFIG_ERROR", False
            chunks = []
            total = 0
            read_chunk = getattr(response, "read1", response.read)
            while True:
                if time.monotonic() >= deadline_at:
                    return None, "LOCAL_PROVIDER_TIMEOUT", True
                chunk = read_chunk(min(64 * 1024, MAX_RESPONSE_BYTES + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > MAX_RESPONSE_BYTES:
                    return None, "LOCAL_PROVIDER_AUTH_OR_CONFIG_ERROR", False
            response_bytes = b"".join(chunks)
            if declared_length is not None and total != declared_length:
                return None, "MODEL_RESPONSE_INVALID_JSON", False
    except urllib.error.HTTPError as error:
        if 300 <= error.code < 400:
            return None, "LOCAL_ENDPOINT_NOT_LOOPBACK", False
        if error.code == 429:
            return None, "LOCAL_PROVIDER_RATE_LIMITED", True
        if error.code in RETRYABLE_HTTP_STATUSES:
            return None, "LOCAL_PROVIDER_TRANSIENT_ERROR", True
        return None, "LOCAL_PROVIDER_AUTH_OR_CONFIG_ERROR", False
    except http.client.IncompleteRead:
        return None, "MODEL_RESPONSE_INVALID_JSON", False
    except http.client.HTTPException:
        return None, "MODEL_RESPONSE_INVALID_JSON", False
    except (socket.timeout, TimeoutError):
        return None, "LOCAL_PROVIDER_TIMEOUT", True
    except urllib.error.URLError as error:
        if isinstance(error.reason, (socket.timeout, TimeoutError)):
            return None, "LOCAL_PROVIDER_TIMEOUT", True
        return None, "LOCAL_PROVIDER_TRANSIENT_ERROR", True
    except OSError:
        return None, "LOCAL_PROVIDER_TRANSIENT_ERROR", True
    return response_bytes, None, False


def _post_with_retry(
    endpoint: str,
    request: dict[str, Any],
    request_body: bytes,
    runtime_binding_sha256: str,
    local_config: dict[str, Any],
) -> tuple[Any, str | None, int]:
    """在同一 deadline 內完成 bounded loopback request 與 response decode。"""
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _NoRedirect()
    )
    deadline_at = time.monotonic() + local_config["deadline_seconds"]
    provider_call_count = 0
    last_reason = "LOCAL_PROVIDER_TRANSIENT_ERROR"
    for attempt in range(local_config["max_attempts"]):
        remaining = deadline_at - time.monotonic()
        if remaining <= 0:
            last_reason = "LOCAL_PROVIDER_TIMEOUT"
            break
        provider_call_count += 1
        response_bytes, reason, retryable = _post_once(
            opener, endpoint, request_body, remaining
        )
        if reason is None:
            response_output, reason = _decode_response(
                response_bytes, request, runtime_binding_sha256
            )
            if reason is None:
                return response_output, None, provider_call_count
            retryable = False
        last_reason = reason
        if not retryable or attempt + 1 >= local_config["max_attempts"]:
            break
        remaining = deadline_at - time.monotonic()
        if remaining <= 0:
            last_reason = "LOCAL_PROVIDER_TIMEOUT"
            break
        time.sleep(min(local_config["retry_backoff_seconds"], remaining))
    return None, last_reason, provider_call_count
