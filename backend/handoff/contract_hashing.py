from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from material_runtime_files import canonical_json_bytes


PKG_DRAFT_UNSERIALIZABLE = "PKG_DRAFT_UNSERIALIZABLE"

class HandoffDraftUnserializable(ValueError):
    code = PKG_DRAFT_UNSERIALIZABLE

    def __init__(self) -> None:
        """建立固定錯誤代碼的例外，表示 handoff draft 無法轉成 canonical JSON。"""
        super().__init__(self.code)

def canonical_sha256(value: Any) -> str:
    """將資料轉成穩定的 canonical JSON bytes，再計算 SHA-256 內容指紋。"""
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()

def record_canonical_sha256(record: Mapping[str, Any]) -> str:
    """計算單筆 record 的內容指紋，排除 record 自己保存的 canonical_sha256。"""
    projection = dict(record)
    projection.pop("canonical_sha256", None)
    return canonical_sha256(projection)

def package_content_sha256(package: Mapping[str, Any]) -> str:
    """計算 package 核心內容指紋，排除自身 hashes 與封存後才產生的驗證摘要。"""
    projection = dict(package)
    projection.pop("content_sha256", None)
    projection.pop("canonical_sha256", None)
    projection.pop("validation_summary", None)
    return canonical_sha256(projection)

def package_envelope_sha256(package: Mapping[str, Any]) -> str:
    """計算完整 sealed package 指紋，只排除用來存放這個指紋的欄位本身。"""
    projection = dict(package)
    projection.pop("canonical_sha256", None)
    return canonical_sha256(projection)


def _canonical_sha256(value: Any) -> str:
    """計算內部輸入指紋，並將無法 canonicalize 的資料轉為固定 ValueError。"""
    try:
        return hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    except (TypeError, ValueError, RecursionError):
        raise ValueError("invalid package input") from None

def _stable_id(prefix: str, value: Any) -> str:
    """以內容指紋前 24 碼建立可重播且固定的帶前綴 ID。"""
    return f"{prefix}:{_canonical_sha256(value)[:24]}"

def _valid_input_record_hash(record: Mapping[str, Any]) -> bool:
    """確認輸入 record 保存的 canonical_sha256 與目前內容重新計算後一致。"""
    try:
        return (
            record.get("canonical_sha256")
            == record_canonical_sha256(record)
        )
    except (TypeError, ValueError, RecursionError):
        return False
