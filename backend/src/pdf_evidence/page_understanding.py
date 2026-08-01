from __future__ import annotations

import base64
import hashlib
import json
import math
import os
from pathlib import Path
import socket
import tempfile
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from .page_structure import PAGE_STRUCTURE_SCHEMA, validate_page_structure


PAGE_STRUCTURE_PROMPT_VERSION = "s1-page-structure-prompt/v2"
PAGE_STRUCTURE_PROMPT = (
    "Inspect the supplied page image and describe its visible structure. Ground every element in "
    "visible page evidence. Every bbox uses normalized_render_1000 coordinates ordered [x0, y0, x1, y1] "
    "with values from 0 to 1000; x increases rightward, y increases downward, x0 < x1, and y0 < y1. Preserve reading and spatial relationships, mark uncertain regions explicitly, and do not invent content."
)
PAGE_STRUCTURE_BODY_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["elements", "reading_order", "spatial_relations"],
    "$defs": {"nonempty_string": {"type": "string", "minLength": 1, "pattern": "^[\\s\\S]*\\S[\\s\\S]*$"},
        "bbox": {
            "type": "array", "items": {"type": "number", "minimum": 0, "maximum": 1000},
            "minItems": 4, "maxItems": 4,
        },
        "matrix_cell": {"type": "object", "additionalProperties": False, "required": ["row", "column", "text"],
            "properties": {
                "row": {"type": "integer", "minimum": 1}, "column": {"type": "integer", "minimum": 1},
                "text": {"type": "string"},
            },
        },
        "table_cell": {"type": "object", "additionalProperties": False, "required": ["row", "column", "row_span", "column_span", "role", "text"],
            "properties": {
                "row": {"type": "integer", "minimum": 1}, "column": {"type": "integer", "minimum": 1},
                "row_span": {"type": "integer", "minimum": 1}, "column_span": {"type": "integer", "minimum": 1},
                "role": {"enum": ["header", "data"]},
                "text": {"type": "string"},
            },
        },
    },
    "properties": {
        "elements": {"type": "array", "items": {"oneOf": [
                    {
                        "type": "object", "additionalProperties": False, "required": ["id", "type", "bbox", "text"],
                        "properties": {
                            "id": {"$ref": "#/$defs/nonempty_string"}, "type": {"enum": ["heading", "paragraph", "code"]},
                            "bbox": {"$ref": "#/$defs/bbox"},
                            "text": {"$ref": "#/$defs/nonempty_string"},
                        },
                    },
                    {
                        "type": "object", "additionalProperties": False, "required": ["id", "type", "bbox", "items"],
                        "properties": {
                            "id": {"$ref": "#/$defs/nonempty_string"}, "type": {"const": "list"},
                            "bbox": {"$ref": "#/$defs/bbox"},
                            "items": {"type": "array", "items": {"$ref": "#/$defs/nonempty_string"}, "minItems": 1},
                        },
                    },
                    {
                        "type": "object", "additionalProperties": False, "required": ["id", "type", "bbox", "latex"],
                        "properties": {
                            "id": {"$ref": "#/$defs/nonempty_string"}, "type": {"const": "formula"},
                            "bbox": {"$ref": "#/$defs/bbox"}, "latex": {"$ref": "#/$defs/nonempty_string"},
                        },
                    },
                    {
                        "type": "object", "additionalProperties": False, "required": ["id", "type", "bbox", "row_count", "column_count", "cells"],
                        "properties": {
                            "id": {"$ref": "#/$defs/nonempty_string"}, "type": {"const": "matrix"},
                            "bbox": {"$ref": "#/$defs/bbox"},
                            "row_count": {"type": "integer", "minimum": 1}, "column_count": {"type": "integer", "minimum": 1},
                            "cells": {"type": "array", "items": {"$ref": "#/$defs/matrix_cell"}, "minItems": 1},
                        },
                    },
                    {
                        "type": "object", "additionalProperties": False, "required": ["id", "type", "bbox", "row_count", "column_count", "cells"],
                        "properties": {
                            "id": {"$ref": "#/$defs/nonempty_string"}, "type": {"const": "table"},
                            "bbox": {"$ref": "#/$defs/bbox"},
                            "row_count": {"type": "integer", "minimum": 1}, "column_count": {"type": "integer", "minimum": 1},
                            "cells": {"type": "array", "items": {"$ref": "#/$defs/table_cell"}, "minItems": 1},
                        },
                    },
                    {
                        "type": "object", "additionalProperties": False, "required": ["id", "type", "bbox"],
                        "properties": {
                            "id": {"$ref": "#/$defs/nonempty_string"}, "type": {"enum": ["diagram_node", "arrow"]},
                            "bbox": {"$ref": "#/$defs/bbox"},
                        },
                    },
                    {
                        "type": "object", "additionalProperties": False, "required": ["id", "type", "bbox", "text"],
                        "properties": {
                            "id": {"$ref": "#/$defs/nonempty_string"}, "type": {"const": "diagram_label"},
                            "bbox": {"$ref": "#/$defs/bbox"}, "text": {"$ref": "#/$defs/nonempty_string"},
                            "node_id": {"$ref": "#/$defs/nonempty_string"},
                        },
                    },
                    {
                        "type": "object", "additionalProperties": False, "required": ["id", "type", "bbox", "uncertainty_kind"],
                        "properties": {
                            "id": {"$ref": "#/$defs/nonempty_string"}, "type": {"const": "other_visible_region"},
                            "bbox": {"$ref": "#/$defs/bbox"},
                            "uncertainty_kind": {"enum": ["uncertain", "cropped", "unreadable", "conflicting"]},
                        },
                    },
                ]
            },
        },
        "reading_order": {"type": "array", "items": {"type": "string"}},
        "spatial_relations": {"type": "array", "items": {"oneOf": [
                    {
                        "type": "object", "additionalProperties": False, "required": ["type", "source_id", "target_id"],
                        "properties": {
                            "type": {"enum": ["left_of", "above", "contains"]},
                            "source_id": {"$ref": "#/$defs/nonempty_string"},
                            "target_id": {"$ref": "#/$defs/nonempty_string"},
                        },
                    },
                    {
                        "type": "object", "additionalProperties": False, "required": ["type", "source_id", "target_id", "arrow_id"],
                        "properties": {
                            "type": {"const": "directed_arrow"},
                            "source_id": {"$ref": "#/$defs/nonempty_string"},
                            "target_id": {"$ref": "#/$defs/nonempty_string"},
                            "arrow_id": {"$ref": "#/$defs/nonempty_string"},
                        },
                    },
                ]
            },
        },
    },
}

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
_MODEL_BODY_KEYS = {"elements", "reading_order", "spatial_relations"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """拒絕 redirect，避免頁面資料離開已驗證的 loopback 位址。"""

    def _reject(self, request, response, code, message, headers):
        raise urllib.error.HTTPError(
            request.full_url, code, message, headers, response
        )

    http_error_301 = _reject
    http_error_302 = _reject
    http_error_303 = _reject
    http_error_307 = _reject
    http_error_308 = _reject


def _valid_sha256(value: Any) -> bool:
    """檢查值是否為小寫 SHA-256 字串。"""
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _valid_reference(value: Any, prefix: str) -> bool:
    """檢查 evidence identity 的前綴與雜湊。"""
    return isinstance(value, str) and value.startswith(prefix) and _valid_sha256(
        value.removeprefix(prefix)
    )


def _finite_number(value: Any) -> bool:
    """排除 bool 並檢查有限的整數或浮點數。"""
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
    )


def _evidence_ref(page_evidence: Any) -> str | None:
    """只取出格式有效且可安全回傳的 evidence_ref。"""
    if not isinstance(page_evidence, dict):
        return None
    value = page_evidence.get("evidence_ref")
    return value if _valid_reference(value, "evidence:sha256:") else None


def _result(
    processing: str,
    reason_code: str,
    *,
    evidence_ref: str | None = None,
    cache_key: str | None = None,
    runtime_identity: dict[str, Any] | None = None,
    page_structure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """建立符合目前流程狀態的最小公開結果。"""
    result: dict[str, Any] = {
        "processing": processing,
        "reason_code": reason_code,
    }
    if evidence_ref is not None:
        result["input_evidence_ref"] = evidence_ref
    if cache_key is not None and runtime_identity is not None:
        result["cache_key"] = cache_key
        result["runtime_identity"] = runtime_identity
    if page_structure is not None:
        result["page_structure"] = page_structure
    return result


def _validate_page_evidence(page_evidence: Any) -> bool:
    """檢查 cache、座標轉換與 root binding 所需的 Page Evidence。"""
    if not isinstance(page_evidence, dict):
        return False
    if (
        page_evidence.get("schema") != "s1-page-evidence/v1"
        or page_evidence.get("status") != "succeeded"
    ):
        return False
    page_number = page_evidence.get("page_number")
    if isinstance(page_number, bool) or not isinstance(page_number, int) or page_number < 1:
        return False

    hashes = page_evidence.get("hashes")
    render = page_evidence.get("render")
    geometry = page_evidence.get("geometry")
    transform = page_evidence.get("coordinate_transform")
    if not all(isinstance(value, dict) for value in (hashes, render, geometry, transform)):
        return False
    source_sha256 = hashes.get("source_sha256")
    native_sha256 = hashes.get("native_sha256")
    render_sha256 = hashes.get("render_sha256")
    if not all(
        _valid_sha256(value)
        for value in (source_sha256, native_sha256, render_sha256)
    ):
        return False
    page_ref_hash = hashlib.sha256(
        f"{source_sha256}:{page_number}".encode("ascii")
    ).hexdigest()
    evidence_hash = hashlib.sha256(
        f"{source_sha256}:{page_number}:{native_sha256}:{render_sha256}".encode(
            "ascii"
        )
    ).hexdigest()
    if (
        page_evidence.get("material_ref")
        != f"material:sha256:{source_sha256}"
        or page_evidence.get("page_ref") != f"page:sha256:{page_ref_hash}"
        or page_evidence.get("evidence_ref")
        != f"evidence:sha256:{evidence_hash}"
    ):
        return False
    if not isinstance(render.get("schema"), str) or not render["schema"]:
        return False
    if not _finite_number(render.get("width_pixels")) or render["width_pixels"] <= 0:
        return False
    if not _finite_number(render.get("height_pixels")) or render["height_pixels"] <= 0:
        return False

    visible = geometry.get("visible_points")
    matrix = transform.get("rotated_to_point")
    if (
        transform.get("native_coordinate_space") != "unrotated_page_points"
        or not isinstance(visible, list)
        or len(visible) != 4
        or not isinstance(matrix, list)
        or len(matrix) != 6
        or not all(_finite_number(value) for value in [*visible, *matrix])
    ):
        return False
    return visible[2] > visible[0] and visible[3] > visible[1]


def _valid_config(local_config: Any) -> bool:
    """檢查 local_config 的完整 key set 與固定型別。"""
    if not isinstance(local_config, dict) or set(local_config) != _CONFIG_KEYS:
        return False
    string_keys = {
        "endpoint_url",
        "cache_dir",
        "model_id",
        "model_revision",
        "runtime_id",
        "processing_policy_version",
    }
    if any(
        not isinstance(local_config[key], str) or not local_config[key].strip()
        for key in string_keys
    ):
        return False
    if not _valid_sha256(local_config["model_artifact_sha256"]) or not _valid_sha256(
        local_config["projector_sha256"]
    ):
        return False
    deadline = local_config["deadline_seconds"]
    backoff = local_config["retry_backoff_seconds"]
    attempts = local_config["max_attempts"]
    return (
        _finite_number(deadline)
        and deadline > 0
        and isinstance(attempts, int)
        and not isinstance(attempts, bool)
        and attempts >= 1
        and _finite_number(backoff)
        and backoff >= 0
    )


def _loopback_endpoint(endpoint_url: str) -> str | None:
    """驗證 endpoint 並回傳不需 DNS 的 numeric loopback base URL。"""
    if "?" in endpoint_url or "#" in endpoint_url:
        return None
    try:
        parsed = urllib.parse.urlsplit(endpoint_url)
        port = parsed.port
    except ValueError:
        return None
    if port is None or not 1 <= port <= 65535:
        return None
    expected_netloc = f"{parsed.hostname}:{port}"
    if not (
        parsed.scheme == "http"
        and parsed.hostname in {"localhost", "127.0.0.1"}
        and parsed.netloc == expected_netloc
        and parsed.username is None
        and parsed.password is None
        and parsed.path == ""
        and parsed.query == ""
        and parsed.fragment == ""
    ):
        return None
    return f"http://127.0.0.1:{port}"


def _runtime_identity(local_config: dict[str, Any]) -> dict[str, Any]:
    """建立不含 endpoint、硬體或 secret 的 runtime identity。"""
    return {
        "model_id": local_config["model_id"],
        "model_revision": local_config["model_revision"],
        "model_artifact_sha256": local_config["model_artifact_sha256"],
        "projector_sha256": local_config["projector_sha256"],
        "runtime_id": local_config["runtime_id"],
        "prompt_version": PAGE_STRUCTURE_PROMPT_VERSION,
        "processing_policy_version": local_config["processing_policy_version"],
    }


def _canonical_json(value: Any) -> bytes:
    """將資料編碼為 deterministic JSON bytes。"""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _cache_key(page_evidence: dict[str, Any], local_config: dict[str, Any]) -> str:
    """綁定所有會影響 Page Structure 的輸入與版本。"""
    identity = {
        "source_sha256": page_evidence["hashes"]["source_sha256"],
        "page_number": page_evidence["page_number"],
        "evidence_ref": page_evidence["evidence_ref"],
        "render_schema": page_evidence["render"]["schema"],
        "render_sha256": page_evidence["hashes"]["render_sha256"],
        "model_id": local_config["model_id"],
        "model_revision": local_config["model_revision"],
        "model_artifact_sha256": local_config["model_artifact_sha256"],
        "projector_sha256": local_config["projector_sha256"],
        "runtime_id": local_config["runtime_id"],
        "prompt_version": PAGE_STRUCTURE_PROMPT_VERSION,
        "prompt_sha256": hashlib.sha256(PAGE_STRUCTURE_PROMPT.encode("utf-8")).hexdigest(),
        "page_structure_body_schema_sha256": hashlib.sha256(_canonical_json(PAGE_STRUCTURE_BODY_SCHEMA)).hexdigest(),
        "page_structure_schema": PAGE_STRUCTURE_SCHEMA,
        "processing_policy_version": local_config["processing_policy_version"],
    }
    return hashlib.sha256(_canonical_json(identity)).hexdigest()


def _read_cache(
    cache_path: Path,
    cache_key: str,
    evidence_ref: str,
    runtime_identity: dict[str, Any],
    page_evidence: dict[str, Any],
) -> dict[str, Any] | None:
    """讀取 identity 完全相同且仍通過 validator 的 cache。"""
    try:
        record = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(record, dict) or set(record) != {
        "cache_key",
        "input_evidence_ref",
        "runtime_identity",
        "page_structure",
    }:
        return None
    if (
        record["cache_key"] != cache_key
        or record["input_evidence_ref"] != evidence_ref
        or record["runtime_identity"] != runtime_identity
        or validate_page_structure(record["page_structure"], page_evidence) is not None
    ):
        return None
    return record["page_structure"]


def _write_cache(cache_path: Path, record: dict[str, Any]) -> bool:
    """在同目錄同步暫存檔後，以 replace 發布 cache。"""
    temporary_path: Path | None = None
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb", delete=False, dir=cache_path.parent
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(_canonical_json(record))
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, cache_path)
        temporary_path = None
        return True
    except OSError:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        return False


def _request_payload(render_bytes: bytes, model_id: str) -> bytes:
    """建立只含固定 prompt、PNG data URI 與 model ID 的 request。"""
    image = base64.b64encode(render_bytes).decode("ascii")
    return _canonical_json(
        {
            "model": model_id,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": PAGE_STRUCTURE_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{image}"},
                        },
                    ],
                }
            ],
            "temperature": 0,
            "stream": False,
            "max_tokens": 4096,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "page_structure_body",
                    "strict": True,
                    "schema": PAGE_STRUCTURE_BODY_SCHEMA,
                },
            },
        }
    )


def _decode_response(response_bytes: bytes) -> tuple[dict[str, Any] | None, str | None]:
    """解析固定 chat completion envelope 與 Page Structure body。"""
    try:
        outer = json.loads(response_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, "MODEL_RESPONSE_INVALID_JSON"
    if not isinstance(outer, dict):
        return None, "MODEL_RESPONSE_INVALID_JSON"
    choices = outer.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None, "MODEL_RESPONSE_INVALID_JSON"
    choice = choices[0]
    if choice.get("finish_reason") == "length":
        return None, "MODEL_RESPONSE_TRUNCATED"
    message = choice.get("message")
    if (
        choice.get("finish_reason") != "stop"
        or not isinstance(message, dict)
        or not isinstance(message.get("content"), str)
    ):
        return None, "MODEL_RESPONSE_INVALID_JSON"
    try:
        body = json.loads(message["content"])
    except json.JSONDecodeError:
        return None, "MODEL_RESPONSE_INVALID_JSON"
    if not isinstance(body, dict) or set(body) != _MODEL_BODY_KEYS:
        return None, "MODEL_RESPONSE_INVALID_JSON"
    return body, None


def _post_once(
    opener: urllib.request.OpenerDirector,
    endpoint_url: str,
    request_body: bytes,
    timeout: float,
) -> tuple[dict[str, Any] | None, str | None, bool]:
    """送出一次 loopback request，並標記失敗是否允許 retry。"""
    request = urllib.request.Request(
        f"{endpoint_url}/v1/chat/completions",
        data=request_body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            if response.getcode() != 200:
                return None, "LOCAL_PROVIDER_AUTH_OR_CONFIG_ERROR", False
            body = response.read()
    except urllib.error.HTTPError as error:
        if error.code == 429:
            return None, "LOCAL_PROVIDER_RATE_LIMITED", True
        if 500 <= error.code <= 599:
            return None, "LOCAL_PROVIDER_TRANSIENT_ERROR", True
        return None, "LOCAL_PROVIDER_AUTH_OR_CONFIG_ERROR", False
    except (socket.timeout, TimeoutError):
        return None, "LOCAL_PROVIDER_TIMEOUT", True
    except urllib.error.URLError as error:
        if isinstance(error.reason, (socket.timeout, TimeoutError)):
            return None, "LOCAL_PROVIDER_TIMEOUT", True
        return None, "LOCAL_PROVIDER_TRANSIENT_ERROR", True
    except ConnectionError:
        return None, "LOCAL_PROVIDER_TRANSIENT_ERROR", True
    model_body, reason = _decode_response(body)
    return model_body, reason, False


def _request_with_retry(
    opener: urllib.request.OpenerDirector,
    endpoint_url: str,
    request_body: bytes,
    local_config: dict[str, Any],
    deadline_at: float,
) -> tuple[dict[str, Any] | None, str | None, bool]:
    """依 attempts、deadline 與 backoff 執行有限次重試。"""
    last_reason = "LOCAL_PROVIDER_TIMEOUT"
    for attempt in range(local_config["max_attempts"]):
        remaining = deadline_at - time.monotonic()
        if remaining <= 0:
            return None, last_reason, True
        body, reason, retryable = _post_once(
            opener, endpoint_url, request_body, remaining
        )
        if reason is None:
            return body, None, False
        last_reason = reason
        if not retryable:
            return None, reason, False
        if attempt + 1 >= local_config["max_attempts"]:
            return None, reason, True
        remaining = deadline_at - time.monotonic()
        if remaining <= 0:
            return None, reason, True
        time.sleep(min(local_config["retry_backoff_seconds"], remaining))


def _build_page_structure(
    model_body: dict[str, Any], page_evidence: dict[str, Any]
) -> dict[str, Any] | None:
    """將 normalized bbox 轉為 native 座標並補上可信 root binding。"""
    elements = model_body["elements"]
    if not isinstance(elements, list):
        return None
    render = page_evidence["render"]
    visible = page_evidence["geometry"]["visible_points"]
    matrix = page_evidence["coordinate_transform"]["rotated_to_point"]
    width = render["width_pixels"]
    height = render["height_pixels"]
    visible_x0, visible_y0, visible_x1, visible_y1 = visible
    a, b, c, d, e, f = matrix

    for element in elements:
        if not isinstance(element, dict):
            return None
        bbox = element.get("bbox")
        if (
            not isinstance(bbox, list)
            or len(bbox) != 4
            or not all(_finite_number(value) and 0 <= value <= 1000 for value in bbox)
            or bbox[2] <= bbox[0]
            or bbox[3] <= bbox[1]
        ):
            return None
        native_corners = []
        for normalized_x, normalized_y in (
            (bbox[0], bbox[1]),
            (bbox[2], bbox[1]),
            (bbox[0], bbox[3]),
            (bbox[2], bbox[3]),
        ):
            pixel_x = normalized_x / 1000 * width
            pixel_y = normalized_y / 1000 * height
            rotated_x = visible_x0 + pixel_x / width * (visible_x1 - visible_x0)
            rotated_y = visible_y0 + pixel_y / height * (visible_y1 - visible_y0)
            native_corners.append(
                (
                    rotated_x * a + rotated_y * c + e,
                    rotated_x * b + rotated_y * d + f,
                )
            )
        if not all(
            math.isfinite(value) for point in native_corners for value in point
        ):
            return None
        element["bbox"] = [
            min(point[0] for point in native_corners),
            min(point[1] for point in native_corners),
            max(point[0] for point in native_corners),
            max(point[1] for point in native_corners),
        ]

    return {
        "schema": PAGE_STRUCTURE_SCHEMA,
        "material_ref": page_evidence["material_ref"],
        "page_ref": page_evidence["page_ref"],
        "page_number": page_evidence["page_number"],
        "input_evidence_ref": page_evidence["evidence_ref"],
        "coordinate_space": "unrotated_page_points",
        "elements": elements,
        "reading_order": model_body["reading_order"],
        "spatial_relations": model_body["spatial_relations"],
    }


def process_page_evidence(
    page_evidence: Any, render_bytes: Any, local_config: Any
) -> dict[str, Any]:
    """以本機 loopback 模型理解單頁畫面，並回傳已驗證的 Page Structure。"""
    started_at = time.monotonic()
    evidence_ref = _evidence_ref(page_evidence)
    if not isinstance(render_bytes, bytes):
        return _result("failed", "RENDER_HASH_MISMATCH", evidence_ref=evidence_ref)
    if (
        not isinstance(page_evidence, dict)
        or not isinstance(page_evidence.get("hashes"), dict)
        or not _valid_sha256(page_evidence["hashes"].get("render_sha256"))
    ):
        return _result(
            "failed", "PAGE_EVIDENCE_BINDING_INVALID", evidence_ref=evidence_ref
        )
    if (
        hashlib.sha256(render_bytes).hexdigest()
        != page_evidence["hashes"]["render_sha256"]
    ):
        return _result("failed", "RENDER_HASH_MISMATCH", evidence_ref=evidence_ref)
    if not _validate_page_evidence(page_evidence):
        return _result(
            "failed", "PAGE_EVIDENCE_BINDING_INVALID", evidence_ref=evidence_ref
        )
    if not _valid_config(local_config):
        return _result("failed", "LOCAL_CONFIG_INVALID", evidence_ref=evidence_ref)
    endpoint_url = _loopback_endpoint(local_config["endpoint_url"])
    if endpoint_url is None:
        return _result(
            "failed", "LOCAL_ENDPOINT_NOT_LOOPBACK", evidence_ref=evidence_ref
        )

    runtime_identity = _runtime_identity(local_config)
    cache_key = _cache_key(page_evidence, local_config)
    cache_path = Path(local_config["cache_dir"]) / f"{cache_key}.json"
    cached = _read_cache(
        cache_path, cache_key, evidence_ref, runtime_identity, page_evidence
    )
    if cached is not None:
        return _result(
            "succeeded",
            "PAGE_STRUCTURE_CACHE_HIT",
            evidence_ref=evidence_ref,
            cache_key=cache_key,
            runtime_identity=runtime_identity,
            page_structure=cached,
        )

    request_body = _request_payload(render_bytes, local_config["model_id"])
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _NoRedirect()
    )
    model_body, reason, partial = _request_with_retry(
        opener,
        endpoint_url,
        request_body,
        local_config,
        started_at + local_config["deadline_seconds"],
    )
    if reason is not None:
        return _result(
            "partial" if partial else "failed",
            reason,
            evidence_ref=evidence_ref,
            cache_key=cache_key,
            runtime_identity=runtime_identity,
        )

    page_structure = _build_page_structure(model_body, page_evidence)
    if page_structure is None:
        reason = "PAGE_STRUCTURE_INVALID"
    else:
        reason = validate_page_structure(page_structure, page_evidence)
    if reason is not None:
        return _result(
            "failed",
            reason,
            evidence_ref=evidence_ref,
            cache_key=cache_key,
            runtime_identity=runtime_identity,
        )

    record = {
        "cache_key": cache_key,
        "input_evidence_ref": evidence_ref,
        "runtime_identity": runtime_identity,
        "page_structure": page_structure,
    }
    if not _write_cache(cache_path, record):
        return _result(
            "failed",
            "PAGE_STRUCTURE_CACHE_WRITE_FAILED",
            evidence_ref=evidence_ref,
            cache_key=cache_key,
            runtime_identity=runtime_identity,
        )
    return _result(
        "succeeded",
        "PAGE_STRUCTURE_READY",
        evidence_ref=evidence_ref,
        cache_key=cache_key,
        runtime_identity=runtime_identity,
        page_structure=page_structure,
    )
