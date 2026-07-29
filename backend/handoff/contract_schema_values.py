from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from typing import Any

from .contract_schema_metadata import RUNTIME_FORBIDDEN_FIELDS


_HEX_DIGITS = frozenset("0123456789abcdef")

def _valid_artifact_binding(value: Any) -> bool:
    """確認來源綁定具備 artifact 身分、版本、教材、內容 hash 與回查位置。"""
    if not isinstance(value, Mapping):
        return False

    required_fields = (
        "artifact_id",
        "schema_version",
        "material_id",
        "raw_sha256",
        "locator",
    )
    for field in required_fields:
        if field not in value:
            return False

    identity_fields = (
        "artifact_id",
        "schema_version",
        "material_id",
    )
    for field in identity_fields:
        if not _non_empty_string(value.get(field)):
            return False

    if not _sha256_hex(value.get("raw_sha256")):
        return False

    if not _valid_locator(value.get("locator")):
        return False

    if _contains_forbidden_field(value):
        return False

    return True

def _valid_policy_binding(value: Any) -> bool:
    """確認規則綁定有版本與內容 hash，且沒有混入禁止欄位。"""
    if not isinstance(value, Mapping):
        return False
    if not _non_empty_string(value.get("policy_version")):
        return False
    if not _sha256_hex(value.get("canonical_sha256")):
        return False
    if _contains_forbidden_field(value):
        return False
    return True

def _valid_normalized_source_mapping(value: Any) -> bool:
    """確認 normalized source 綁定有效，且每個 layout unit 都有不重複的 ID。"""
    if not isinstance(value, Mapping):
        return False
    if not _valid_artifact_binding(value):
        return False
    units = value.get("layout_units")
    if not isinstance(units, list):
        return False
    unit_ids = [
        unit.get("layout_unit_id")
        for unit in units
        if isinstance(unit, Mapping)
    ]
    if len(unit_ids) != len(units):
        return False
    if not all(_non_empty_string(unit_id) for unit_id in unit_ids):
        return False
    if len(unit_ids) != len(set(unit_ids)):
        return False
    return True

def _binding_matches_source(
    binding: Mapping[str, Any],
    source: Any,
) -> bool:
    """確認綁定記錄的身分、版本、教材、hash 與位置都和實際來源相同。"""
    if not isinstance(source, Mapping):
        return False
    for field in (
        "artifact_id",
        "schema_version",
        "material_id",
        "raw_sha256",
        "locator",
    ):
        if binding.get(field) != source.get(field):
            return False
    return True

def _valid_support(value: Any, record: Mapping[str, Any]) -> bool:
    """確認 candidate 的支持摘要完整，且來源與 context 數量符合實際連結。"""
    if not isinstance(value, Mapping):
        return False
    if set(value) != {
        "flags",
        "origin_count",
        "context_count",
        "hard_negative_gate",
    }:
        return False
    if not isinstance(value.get("flags"), Mapping):
        return False
    if not _integer(value.get("origin_count")):
        return False
    if value.get("origin_count") != _list_length(record.get("origin_ids")):
        return False
    if not _integer(value.get("context_count")):
        return False
    if value.get("context_count") != _list_length(record.get("context_ids")):
        return False
    if not isinstance(value.get("hard_negative_gate"), bool):
        return False
    if _contains_forbidden_field(value):
        return False
    return True

def _valid_input_bindings(value: Any) -> bool:
    """確認 builder 輸入綁定有效、沒有重複，並以固定順序排列。"""
    if not isinstance(value, list) or not value:
        return False
    if not all(_valid_artifact_binding(binding) for binding in value):
        return False
    identities = [
        (binding["artifact_id"], binding["schema_version"], binding["raw_sha256"])
        for binding in value
    ]
    if identities != sorted(identities):
        return False
    if len(identities) != len(set(identities)):
        return False
    return True

def _valid_record_counts(value: Any, package: Mapping[str, Any]) -> bool:
    """確認 attestation 記錄的各類資料數量與 package 實際內容相同。"""
    expected = {
        "candidates": _list_length(package.get("candidates")),
        "origins": _list_length(package.get("origins")),
        "contexts": _list_length(package.get("contexts")),
        "evidence_records": _list_length(package.get("evidence_records")),
        "projection_records": _list_length(package.get("projection_records")),
    }
    return value == expected

def _valid_boundary_reason(value: Any) -> bool:
    """確認 context 邊界原因包含前後狀態與排列固定的限制清單。"""
    if not isinstance(value, Mapping):
        return False
    if set(value) != {"previous", "next", "limits"}:
        return False
    if not isinstance(value.get("previous"), str):
        return False
    if not isinstance(value.get("next"), str):
        return False
    if not _sorted_unique_strings(value.get("limits")):
        return False
    return True

def _valid_locator(value: Any) -> bool:
    """確認回查位置是非空字串或不含禁止欄位的非空物件。"""
    if _non_empty_string(value):
        return True
    if not isinstance(value, Mapping):
        return False
    if not bool(value):
        return False
    if _contains_forbidden_field(value):
        return False
    return True

def _layout_unit_id(value: Any) -> str | None:
    """從字串或 layout unit reference 中取出可用的 layout unit ID。"""
    if _non_empty_string(value):
        return value
    if isinstance(value, Mapping) and _non_empty_string(value.get("layout_unit_id")):
        return value["layout_unit_id"]
    return None

def _valid_bbox(value: Any) -> bool:
    """確認 bbox 有四個有限座標，並形成寬與高都大於零的範圍。"""
    if not isinstance(value, list) or len(value) != 4:
        return False
    if not all(_finite_number(item) for item in value):
        return False
    x0, y0, x1, y1 = value
    return x1 > x0 and y1 > y0

def _valid_literal_span(value: Any) -> bool:
    """確認文字範圍使用有效的 start、end，且 end 位於 start 之後。"""
    if not isinstance(value, Mapping):
        return False
    if set(value) != {"start", "end"}:
        return False
    if not _integer(value.get("start")):
        return False
    if not _integer(value.get("end")):
        return False
    if not 0 <= value["start"] < value["end"]:
        return False
    return True

def _sorted_unique_strings(value: Any, *, non_empty: bool = False) -> bool:
    """確認內容是已排序、沒有重複且每項皆非空的字串清單。"""
    if not isinstance(value, list):
        return False
    if not value and non_empty:
        return False
    if not all(_non_empty_string(item) for item in value):
        return False
    if value != sorted(value):
        return False
    if len(value) != len(set(value)):
        return False
    return True

def _sha256_hex(value: Any) -> bool:
    """確認內容是 64 個小寫十六進位字元組成的 SHA-256。"""
    return (
        isinstance(value, str)
        and len(value) == 64
        and set(value) <= _HEX_DIGITS
    )

def _non_empty_string(value: Any) -> bool:
    """確認內容是非空字串。"""
    return isinstance(value, str) and bool(value)

def _integer(value: Any) -> bool:
    """確認內容是整數，並排除 Python 會當成整數的布林值。"""
    return isinstance(value, int) and not isinstance(value, bool)

def _finite_number(value: Any) -> bool:
    """確認內容是可正常比較的有限數字，並排除布林值與負零。"""
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
    ):
        return False
    if (
        isinstance(value, float)
        and value == 0.0
        and math.copysign(1.0, value) < 0
    ):
        return False
    return True

def _list_length(value: Any) -> int:
    """取得清單長度；不是清單時回傳 -1，讓後續比對直接失敗。"""
    return len(value) if isinstance(value, list) else -1

def _string_or_empty(value: Any) -> str:
    """保留字串值；其他型別轉成空字串供固定排序使用。"""
    return value if isinstance(value, str) else ""

def _text_sha256(text: str) -> str:
    """計算 UTF-8 文字的 SHA-256，供來源文字一致性檢查。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def _contains_forbidden_field(value: Any) -> bool:
    """逐層尋找禁止進入正式 package 的測試答案或評分欄位。"""
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in RUNTIME_FORBIDDEN_FIELDS:
                return True
            if _contains_forbidden_field(item):
                return True
        return False
    if isinstance(value, list):
        for item in value:
            if _contains_forbidden_field(item):
                return True
        return False
    return False
