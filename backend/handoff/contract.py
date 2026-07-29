from __future__ import annotations

import copy as _copy
from collections.abc import Mapping as _Mapping
from typing import Any as _Any

from material_runtime_files import canonical_json_bytes as _canonical_json_bytes
from . import (
    contract_hashing as _hashing,
    contract_schema_fields as _fields,
    contract_schema_metadata as _metadata,
    contract_schema_values as _values,
    contract_validation as _validation,
)

HandoffDraftUnserializable = _hashing.HandoffDraftUnserializable

__all__ = (
    "HandoffDraftUnserializable",
    "is_handoff_consumer_eligible_package",
    "seal_handoff_draft",
)


def seal_handoff_draft(
    draft: _Mapping[str, _Any],
    *,
    normalized_source: _Mapping[str, _Any],
) -> dict[str, _Any]:
    """驗收並封存 builder 建立的交接資料。

    它會先把 draft 最外層的 Mapping 複製成一般 dict，確認整份內容能轉成
    固定格式的 JSON，再用深層複製建立獨立副本。後續新增的驗收結果、
    PASS／FAIL 狀態與內容指紋都只寫入副本，不會回寫呼叫端持有的原始
    draft。接著檢查格式、來源與各筆資料的關聯，把發現的問題收進
    invalid_records，再依是否有問題決定封存結果。最後會為每筆資料及整個
    package 產生內容指紋，供下游確認資料在交接後沒有被改動。
    """
    if not isinstance(draft, _Mapping):
        raise TypeError("handoff draft must be a Mapping")

    try:
        draft_dict = dict(draft)
        _canonical_json_bytes(draft_dict)
        sealed = _copy.deepcopy(draft_dict)
    except (TypeError, ValueError, RecursionError):
        raise HandoffDraftUnserializable() from None

    failures: list[tuple[str, str, str, str]] = []
    _fields._validate_package_fields(sealed, normalized_source, failures)
    _fields._validate_record_fields(sealed, failures)
    _fields._validate_candidate_lifecycle(sealed, failures)
    indexes = _validation._record_indexes(sealed)
    source_units = _validation._validated_source_units(
        sealed,
        normalized_source,
    )
    _validation._validate_cross_references(sealed, indexes, failures)
    _validation._validate_materials(sealed, indexes, failures)
    _validation._validate_literal_and_source_bindings(
        sealed,
        indexes,
        source_units,
        failures,
    )
    _validation._validate_context_boundaries(
        sealed,
        source_units,
        failures,
    )
    _validation._validate_input_hashes(sealed, failures)

    for collection, package_key in _metadata.COLLECTION_KEYS.items():
        records = sealed.get(package_key)
        if not isinstance(records, list):
            continue
        for record in records:
            if isinstance(record, dict):
                record["canonical_sha256"] = (
                    _hashing.record_canonical_sha256(record)
                )

    existing_invalid_records = sealed.get("invalid_records")
    if not isinstance(existing_invalid_records, list):
        existing_invalid_records = []
    generated_invalid_records = _validation._generated_invalid_records(
        failures
    )
    sealed["invalid_records"] = [
        *existing_invalid_records,
        *generated_invalid_records,
    ]
    sealed["invalid_records"].sort(
        key=lambda record: (
            _values._string_or_empty(record.get("invalid_record_id"))
            if isinstance(record, _Mapping)
            else ""
        )
    )
    for record in sealed["invalid_records"]:
        if isinstance(record, dict):
            record["canonical_sha256"] = (
                _hashing.record_canonical_sha256(record)
            )

    sealed["status"] = "FAIL" if sealed["invalid_records"] else "PASS"
    sealed["content_sha256"] = _hashing.package_content_sha256(sealed)
    sealed["validation_summary"] = _validation._validation_summary(
        sealed,
        input_summary=draft.get("validation_summary"),
    )
    sealed["canonical_sha256"] = _hashing.package_envelope_sha256(sealed)
    return sealed

def is_handoff_consumer_eligible_package(
    package: _Mapping[str, _Any],
    *,
    normalized_source: _Mapping[str, _Any],
) -> bool:
    """重新封存 PASS package，僅接受 canonical bytes 完全一致的輸入。"""
    if package.get("status") != "PASS":
        return False
    try:
        resealed = seal_handoff_draft(
            package,
            normalized_source=normalized_source,
        )
    except (HandoffDraftUnserializable, TypeError):
        return False
    return (
        resealed.get("status") == "PASS"
        and _canonical_json_bytes(resealed)
        == _canonical_json_bytes(package)
    )
