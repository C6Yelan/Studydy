from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from material_runtime_files import canonical_json_bytes
from handoff_contract_hashing import (
    HandoffDraftUnserializable,
    PKG_DRAFT_UNSERIALIZABLE,
    canonical_sha256,
    package_content_sha256,
    package_envelope_sha256,
    record_canonical_sha256,
)
from handoff_contract_schema import (
    COLLECTION_ID_FIELDS,
    COLLECTION_KEYS,
    CONTEXT_POLICY_VERSION,
    FIELD_METADATA,
    FIELD_METADATA_ROWS,
    PACKAGE_SCHEMA_VERSION,
    RECORD_HASH_MISMATCH,
    RESERVED_NON_EMITTED_CODES,
    RUNTIME_FORBIDDEN_FIELDS,
    UNKNOWN_FIELD_CODES,
    VALIDATOR_VERSION,
    _string_or_empty,
    _validate_candidate_lifecycle,
    _validate_package_fields,
    _validate_record_fields,
)
from handoff_contract_validation import (
    _generated_invalid_records,
    _record_indexes,
    _validate_context_boundaries,
    _validate_cross_references,
    _validate_input_hashes,
    _validate_literal_and_source_bindings,
    _validate_materials,
    _validated_source_units,
    _validation_summary,
)


def seal_handoff_draft(
    draft: Mapping[str, Any],
    *,
    normalized_source: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        canonical_json_bytes(draft)
        sealed = copy.deepcopy(draft)
    except (TypeError, ValueError, RecursionError):
        raise HandoffDraftUnserializable() from None

    if not isinstance(sealed, dict):
        raise TypeError("handoff draft must be a Mapping")

    failures: list[tuple[str, str, str, str]] = []
    _validate_package_fields(sealed, normalized_source, failures)
    _validate_record_fields(sealed, failures)
    _validate_candidate_lifecycle(sealed, failures)
    indexes = _record_indexes(sealed)
    source_units = _validated_source_units(sealed, normalized_source)
    _validate_cross_references(sealed, indexes, failures)
    _validate_materials(sealed, indexes, failures)
    _validate_literal_and_source_bindings(
        sealed,
        indexes,
        source_units,
        failures,
    )
    _validate_context_boundaries(sealed, source_units, failures)
    _validate_input_hashes(sealed, failures)

    for collection, package_key in COLLECTION_KEYS.items():
        records = sealed.get(package_key)
        if not isinstance(records, list):
            continue
        for record in records:
            if isinstance(record, dict):
                record["canonical_sha256"] = record_canonical_sha256(record)

    existing_invalid_records = sealed.get("invalid_records")
    if not isinstance(existing_invalid_records, list):
        existing_invalid_records = []
    generated_invalid_records = _generated_invalid_records(failures)
    sealed["invalid_records"] = [
        *existing_invalid_records,
        *generated_invalid_records,
    ]
    sealed["invalid_records"].sort(
        key=lambda record: (
            _string_or_empty(record.get("invalid_record_id"))
            if isinstance(record, Mapping)
            else ""
        )
    )
    for record in sealed["invalid_records"]:
        if isinstance(record, dict):
            record["canonical_sha256"] = record_canonical_sha256(record)

    sealed["status"] = "FAIL" if sealed["invalid_records"] else "PASS"
    sealed["content_sha256"] = package_content_sha256(sealed)
    sealed["validation_summary"] = _validation_summary(
        sealed,
        input_summary=draft.get("validation_summary"),
    )
    sealed["canonical_sha256"] = package_envelope_sha256(sealed)
    return sealed

def is_handoff_consumer_eligible_package(
    package: Mapping[str, Any],
    *,
    normalized_source: Mapping[str, Any],
) -> bool:
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
        and canonical_json_bytes(resealed) == canonical_json_bytes(package)
    )
