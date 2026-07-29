from __future__ import annotations

import copy as _copy
from collections.abc import Mapping as _Mapping
from typing import Any as _Any

from material_runtime_files import canonical_json_bytes as _canonical_json_bytes
from . import (
    contract_hashing as _hashing,
    contract_schema as _schema,
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
    """驗證並封存 draft，將所有失敗轉為可追溯的 invalid records。"""
    try:
        _canonical_json_bytes(draft)
        sealed = _copy.deepcopy(draft)
    except (TypeError, ValueError, RecursionError):
        raise HandoffDraftUnserializable() from None

    if not isinstance(sealed, dict):
        raise TypeError("handoff draft must be a Mapping")

    failures: list[tuple[str, str, str, str]] = []
    _schema._validate_package_fields(sealed, normalized_source, failures)
    _schema._validate_record_fields(sealed, failures)
    _schema._validate_candidate_lifecycle(sealed, failures)
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

    for collection, package_key in _schema.COLLECTION_KEYS.items():
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
            _schema._string_or_empty(record.get("invalid_record_id"))
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
