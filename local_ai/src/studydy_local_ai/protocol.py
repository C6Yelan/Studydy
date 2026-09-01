"""本機 AI child 使用的 bounded NDJSON contract。"""

from __future__ import annotations

import base64
import json
import re
from typing import Any, BinaryIO


OCR_REQUEST_SCHEMA = "local-ocr-request/v1"
OCR_RESPONSE_SCHEMA = "local-ocr-response/v1"
MAX_OCR_REQUEST_BYTES = 96 * 1024 * 1024
MAX_OCR_RESPONSE_BYTES = 4 * 1024 * 1024

_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class ProtocolError(RuntimeError):
    """只攜帶固定 reason code，不包含 request 或模型內容。"""


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ProtocolError("CHILD_REQUEST_INVALID")
        value[key] = item
    return value


def _reject_constant(_: str) -> None:
    raise ProtocolError("CHILD_REQUEST_INVALID")


def _depth(value: Any, current: int = 0) -> int:
    if current > 32:
        raise ProtocolError("PROTOCOL_LIMIT_EXCEEDED")
    if isinstance(value, dict):
        return max((_depth(item, current + 1) for item in value.values()), default=current)
    if isinstance(value, list):
        return max((_depth(item, current + 1) for item in value), default=current)
    return current


def decode_json_object(encoded: bytes, max_bytes: int) -> dict[str, Any]:
    if not encoded or len(encoded) > max_bytes:
        raise ProtocolError("PROTOCOL_LIMIT_EXCEEDED")
    try:
        value = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_constant,
        )
    except ProtocolError:
        raise
    except (RecursionError, UnicodeDecodeError, ValueError) as error:
        raise ProtocolError("CHILD_REQUEST_INVALID") from error
    if not isinstance(value, dict):
        raise ProtocolError("CHILD_REQUEST_INVALID")
    _depth(value)
    return value


def read_ndjson(stream: BinaryIO, max_bytes: int) -> dict[str, Any] | None:
    encoded = stream.readline(max_bytes + 2)
    if not encoded:
        return None
    if len(encoded) > max_bytes + 1 or not encoded.endswith(b"\n"):
        raise ProtocolError("PROTOCOL_LIMIT_EXCEEDED")
    return decode_json_object(encoded[:-1], max_bytes)


def write_ndjson(stream: BinaryIO, value: dict[str, Any], max_bytes: int) -> None:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError) as error:
        raise ProtocolError("CHILD_RESPONSE_INVALID") from error
    if len(encoded) > max_bytes:
        raise ProtocolError("PROTOCOL_LIMIT_EXCEEDED")
    stream.write(encoded + b"\n")
    stream.flush()


def _request_id(value: Any) -> str:
    if not isinstance(value, str) or _REQUEST_ID.fullmatch(value) is None:
        raise ProtocolError("CHILD_REQUEST_INVALID")
    return value


def validate_ocr_request(request: Any) -> dict[str, Any]:
    if not isinstance(request, dict) or set(request) != {"schema", "request_id", "render"}:
        raise ProtocolError("CHILD_REQUEST_INVALID")
    if request["schema"] != OCR_REQUEST_SCHEMA:
        raise ProtocolError("CHILD_REQUEST_INVALID")
    render = request["render"]
    if not isinstance(render, dict) or set(render) != {"sha256", "width", "height", "png_base64"}:
        raise ProtocolError("CHILD_REQUEST_INVALID")
    width, height = render["width"], render["height"]
    if (
        type(width) is not int
        or type(height) is not int
        or width < 1
        or height < 1
        or width > 32_768
        or height > 32_768
        or width * height > 50_000_000
        or not isinstance(render["sha256"], str)
        or _SHA256.fullmatch(render["sha256"]) is None
        or not isinstance(render["png_base64"], str)
    ):
        raise ProtocolError("CHILD_REQUEST_INVALID")
    try:
        png_bytes = base64.b64decode(render["png_base64"], validate=True)
    except (ValueError, TypeError) as error:
        raise ProtocolError("CHILD_REQUEST_INVALID") from error
    if len(png_bytes) > 64 * 1024 * 1024 or not png_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ProtocolError("CHILD_REQUEST_INVALID")
    return {
        "request_id": _request_id(request["request_id"]),
        "render_sha256": render["sha256"],
        "width": width,
        "height": height,
        "png_bytes": png_bytes,
    }
