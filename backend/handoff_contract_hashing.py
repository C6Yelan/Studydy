from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from material_runtime_files import canonical_json_bytes


PKG_DRAFT_UNSERIALIZABLE = "PKG_DRAFT_UNSERIALIZABLE"

class HandoffDraftUnserializable(ValueError):
    code = PKG_DRAFT_UNSERIALIZABLE

    def __init__(self) -> None:
        super().__init__(self.code)

def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()

def record_canonical_sha256(record: Mapping[str, Any]) -> str:
    projection = dict(record)
    projection.pop("canonical_sha256", None)
    return canonical_sha256(projection)

def package_content_sha256(package: Mapping[str, Any]) -> str:
    projection = dict(package)
    projection.pop("content_sha256", None)
    projection.pop("canonical_sha256", None)
    projection.pop("validation_summary", None)
    return canonical_sha256(projection)

def package_envelope_sha256(package: Mapping[str, Any]) -> str:
    projection = dict(package)
    projection.pop("canonical_sha256", None)
    return canonical_sha256(projection)


def _canonical_sha256(value: Any) -> str:
    try:
        return hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    except (TypeError, ValueError, RecursionError):
        raise ValueError("invalid package input") from None

def _stable_id(prefix: str, value: Any) -> str:
    return f"{prefix}:{_canonical_sha256(value)[:24]}"

def _valid_input_record_hash(record: Mapping[str, Any]) -> bool:
    try:
        return (
            record.get("canonical_sha256")
            == record_canonical_sha256(record)
        )
    except (TypeError, ValueError, RecursionError):
        return False
