from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any


PACKAGE_SCHEMA_VERSION = "task11a-task11b-handoff-package/v1"

CONTEXT_POLICY_VERSION = "same-material-local-layout-v1"

VALIDATOR_VERSION = "task11-handoff-contract/v1"

RECORD_HASH_MISMATCH = "RECORD_HASH_MISMATCH"

_RAW_FIELD_METADATA_ROWS = (
    ("package", "schema_version", "const task11a-task11b-handoff-package/v1", True, "PKG_SCHEMA_VERSION_INVALID"),
    ("package", "package_id", "string", True, "PKG_ID_INVALID"),
    ("package", "material_id", "string", True, "PKG_MATERIAL_INVALID"),
    ("package", "status", "enum built|PASS|FAIL", True, "PKG_STATUS_INVALID"),
    ("package", "normalized_source_binding", "artifact_binding", True, "PKG_NORMALIZED_SOURCE_BINDING_INVALID"),
    ("package", "candidate_source_binding", "artifact_binding", True, "PKG_CANDIDATE_BINDING_INVALID"),
    ("package", "context_policy_binding", "policy_binding", True, "PKG_CONTEXT_POLICY_BINDING_INVALID"),
    ("package", "projection_policy_binding", "policy_binding", True, "PKG_PROJECTION_POLICY_BINDING_INVALID"),
    ("package", "candidates", "array<candidate_record>", True, "PKG_CANDIDATES_INVALID"),
    ("package", "origins", "array<origin_record>", True, "PKG_ORIGINS_INVALID"),
    ("package", "contexts", "array<context_record>", True, "PKG_CONTEXTS_INVALID"),
    ("package", "evidence_records", "array<evidence_record>", True, "PKG_EVIDENCE_INVALID"),
    ("package", "projection_records", "array<projection_record>", True, "PKG_PROJECTIONS_INVALID"),
    ("package", "build_attestations", "array<build_attestation_record>", True, "PKG_BUILD_ATTESTATION_INVALID"),
    ("package", "invalid_records", "array<invalid_record>", True, "PKG_INVALID_RECORDS_INVALID"),
    ("package", "content_sha256", "sha256_hex", True, "PKG_CONTENT_HASH_MISMATCH"),
    ("package", "validation_summary", "validation_summary_record", True, "PKG_VALIDATION_SUMMARY_INVALID"),
    ("package", "canonical_sha256", "sha256_hex", True, "PKG_ENVELOPE_HASH_MISMATCH"),
    ("candidate", "candidate_id", "string", True, "CANDIDATE_ID_INVALID"),
    ("candidate", "material_id", "string", True, "CANDIDATE_MATERIAL_MISMATCH"),
    ("candidate", "surface", "non_empty_string", True, "CANDIDATE_SURFACE_INVALID"),
    ("candidate", "normalized_surface", "non_empty_string", True, "CANDIDATE_NORMALIZED_SURFACE_INVALID"),
    ("candidate", "generator_kinds", "array<enum>", True, "CANDIDATE_GENERATOR_KINDS_INVALID"),
    ("candidate", "origin_ids", "array<string>", True, "CANDIDATE_ORIGIN_REFS_INVALID"),
    ("candidate", "context_ids", "array<string>", True, "CANDIDATE_CONTEXT_REFS_INVALID"),
    ("candidate", "evidence_refs", "array<string>", True, "CANDIDATE_EVIDENCE_REFS_INVALID"),
    ("candidate", "projection_ids", "array<string>", True, "CANDIDATE_PROJECTION_REFS_INVALID"),
    ("candidate", "support", "object{flags,origin_count,context_count,hard_negative_gate}", True, "CANDIDATE_SUPPORT_INVALID"),
    ("candidate", "construction_status", "enum valid|invalid", True, "CANDIDATE_CONSTRUCTION_STATUS_INVALID"),
    ("candidate", "failure_codes", "array<failure_code>", True, "CANDIDATE_FAILURE_CODES_INVALID"),
    ("candidate", "canonical_sha256", "sha256_hex", True, "CANDIDATE_HASH_MISMATCH"),
    ("origin", "origin_id", "string", True, "ORIGIN_ID_INVALID"),
    ("origin", "candidate_id", "string", True, "ORIGIN_CANDIDATE_REF_INVALID"),
    ("origin", "material_id", "string", True, "ORIGIN_MATERIAL_MISMATCH"),
    ("origin", "block_id", "string", True, "ORIGIN_BLOCK_REF_INVALID"),
    ("origin", "layout_unit_id", "string", True, "ORIGIN_LAYOUT_UNIT_REF_INVALID"),
    ("origin", "source_ref", "string", True, "ORIGIN_SOURCE_REF_INVALID"),
    ("origin", "pdf_page", "integer>=1", False, "ORIGIN_PAGE_INVALID"),
    ("origin", "reading_order", "integer>=0", True, "ORIGIN_READING_ORDER_INVALID"),
    ("origin", "bbox", "array<number>[4]", True, "ORIGIN_BBOX_INVALID"),
    ("origin", "literal_span", "object{start:int,end:int}", True, "ORIGIN_LITERAL_SPAN_INVALID"),
    ("origin", "safe_context_id", "string", True, "ORIGIN_CONTEXT_REF_INVALID"),
    ("origin", "layout_unit_text_sha256", "sha256_hex", True, "ORIGIN_TEXT_HASH_MISMATCH"),
    ("origin", "canonical_sha256", "sha256_hex", True, "ORIGIN_HASH_MISMATCH"),
    ("context", "context_id", "string", True, "CONTEXT_ID_INVALID"),
    ("context", "material_id", "string", True, "CONTEXT_MATERIAL_MISMATCH"),
    ("context", "text", "non_empty_string", True, "CONTEXT_TEXT_INVALID"),
    ("context", "normalized_text", "non_empty_string", True, "CONTEXT_NORMALIZED_TEXT_INVALID"),
    ("context", "layout_unit_refs", "array<layout_unit_ref>", True, "CONTEXT_LAYOUT_REFS_INVALID"),
    ("context", "primary_candidate_ids", "array<string>", True, "CONTEXT_PRIMARY_CANDIDATES_INVALID"),
    ("context", "context_scope", "const same-material-local-layout-v1", True, "CONTEXT_SCOPE_INVALID"),
    ("context", "start_locator", "layout_locator", True, "CONTEXT_START_LOCATOR_INVALID"),
    ("context", "end_locator", "layout_locator", True, "CONTEXT_END_LOCATOR_INVALID"),
    ("context", "boundary_reason", "object{previous:string,next:string,limits:array<string>}", True, "CONTEXT_BOUNDARY_REASON_INVALID"),
    ("context", "evidence_refs", "array<string>", True, "CONTEXT_EVIDENCE_REFS_INVALID"),
    ("context", "code_point_count", "integer 1..1200", True, "CONTEXT_LENGTH_INVALID"),
    ("context", "canonical_sha256", "sha256_hex", True, "CONTEXT_HASH_MISMATCH"),
    ("evidence", "evidence_id", "string", True, "EVIDENCE_ID_INVALID"),
    ("evidence", "material_id", "string", True, "EVIDENCE_MATERIAL_MISMATCH"),
    ("evidence", "evidence_kind", "enum candidate_literal|explicit_alias|heading|definition|projection_literal", True, "EVIDENCE_KIND_INVALID"),
    ("evidence", "statement", "non_empty_string", True, "EVIDENCE_STATEMENT_INVALID"),
    ("evidence", "normalized_statement", "non_empty_string", True, "EVIDENCE_NORMALIZED_STATEMENT_INVALID"),
    ("evidence", "literal_surface", "non_empty_string", True, "EVIDENCE_LITERAL_SURFACE_INVALID"),
    ("evidence", "literal_span", "object{start:int,end:int}", True, "EVIDENCE_LITERAL_SPAN_INVALID"),
    ("evidence", "candidate_ids", "array<string>", True, "EVIDENCE_CANDIDATE_REFS_INVALID"),
    ("evidence", "context_ids", "array<string>", True, "EVIDENCE_CONTEXT_REFS_INVALID"),
    ("evidence", "origin_ids", "array<string>", True, "EVIDENCE_ORIGIN_REFS_INVALID"),
    ("evidence", "canonical_sha256", "sha256_hex", True, "EVIDENCE_HASH_MISMATCH"),
    ("projection", "projection_id", "string", True, "PROJECTION_ID_INVALID"),
    ("projection", "material_id", "string", True, "PROJECTION_MATERIAL_MISMATCH"),
    ("projection", "projection_kind", "enum longer_literal_substring|explicit_alias|heading_definition", True, "PROJECTION_KIND_INVALID"),
    ("projection", "source_candidate_ids", "array<string>", True, "PROJECTION_CANDIDATE_REFS_INVALID"),
    ("projection", "source_context_ids", "array<string>", True, "PROJECTION_CONTEXT_REFS_INVALID"),
    ("projection", "source_evidence_refs", "array<string>", True, "PROJECTION_EVIDENCE_REFS_INVALID"),
    ("projection", "projected_surface", "non_empty_string", True, "PROJECTION_SURFACE_INVALID"),
    ("projection", "normalized_projected_surface", "non_empty_string", True, "PROJECTION_NORMALIZED_SURFACE_INVALID"),
    ("projection", "literal_span", "object{start:int,end:int}", True, "PROJECTION_LITERAL_SPAN_INVALID"),
    ("projection", "algorithm_version", "string", True, "PROJECTION_ALGORITHM_INVALID"),
    ("projection", "canonical_sha256", "sha256_hex", True, "PROJECTION_HASH_MISMATCH"),
    ("build_attestation", "attestation_id", "string", True, "BUILD_ATTESTATION_ID_INVALID"),
    ("build_attestation", "package_id", "string", True, "BUILD_PACKAGE_REF_INVALID"),
    ("build_attestation", "builder_component", "string", True, "BUILD_COMPONENT_INVALID"),
    ("build_attestation", "builder_version", "string", True, "BUILD_VERSION_INVALID"),
    ("build_attestation", "input_bindings", "array<artifact_binding>", True, "BUILD_INPUT_BINDINGS_INVALID"),
    ("build_attestation", "replay_count", "const 0", True, "BUILD_REPLAY_COUNT_INVALID"),
    ("build_attestation", "replay_content_sha256s", "const []", True, "BUILD_REPLAY_HASH_DRIFT"),
    ("build_attestation", "deterministic_replay_pass", "const false", True, "BUILD_REPLAY_FAILED"),
    ("build_attestation", "record_counts", "object", True, "BUILD_RECORD_COUNTS_MISMATCH"),
    ("build_attestation", "canonical_sha256", "sha256_hex", True, "BUILD_ATTESTATION_HASH_MISMATCH"),
    ("invalid_record", "invalid_record_id", "string", True, "INVALID_RECORD_ID_INVALID"),
    ("invalid_record", "collection", "enum candidates|origins|contexts|evidence_records|projection_records|build_attestations|package", True, "INVALID_RECORD_COLLECTION_INVALID"),
    ("invalid_record", "record_id", "string", True, "INVALID_RECORD_TARGET_INVALID"),
    ("invalid_record", "failure_codes", "array<failure_code>", True, "INVALID_RECORD_CODES_INVALID"),
    ("invalid_record", "reason", "non_empty_string", True, "INVALID_RECORD_REASON_INVALID"),
    ("invalid_record", "canonical_sha256", "sha256_hex", True, "INVALID_RECORD_HASH_MISMATCH"),
    ("validation_summary", "validation_run_id", "string", True, "VALIDATION_RUN_ID_INVALID"),
    ("validation_summary", "validator_version", "string", True, "VALIDATOR_VERSION_INVALID"),
    ("validation_summary", "validated_content_sha256", "sha256_hex", True, "VALIDATED_CONTENT_HASH_MISMATCH"),
    ("validation_summary", "status", "enum PASS|FAIL", True, "VALIDATION_STATUS_INVALID"),
    ("validation_summary", "failure_count", "integer>=0", True, "VALIDATION_FAILURE_COUNT_MISMATCH"),
    ("validation_summary", "failure_code_counts", "object<string,integer>=0>", True, "VALIDATION_FAILURE_AGGREGATE_MISMATCH"),
)

_ACTIVE_METADATA_INVARIANTS = MappingProxyType(
    {
        ("build_attestation", "replay_count"): (
            "Production sealed packages require 0; isolated replay counts remain "
            "package-external."
        ),
        ("build_attestation", "replay_content_sha256s"): (
            "Production sealed packages require []; replay hashes remain "
            "package-external and cannot create a runtime hash self-reference."
        ),
        ("build_attestation", "deterministic_replay_pass"): (
            "Production sealed packages require false; replay pass/fail is "
            "determined only by package-external comparison."
        ),
    }
)

FIELD_METADATA_ROWS = tuple(
    MappingProxyType(
        {
            "collection": collection,
            "path": path,
            "type": field_type,
            "required": required,
            "validation_failure_code": failure_code,
            **(
                {"invariant": _ACTIVE_METADATA_INVARIANTS[(collection, path)]}
                if (collection, path) in _ACTIVE_METADATA_INVARIANTS
                else {}
            ),
        }
    )
    for collection, path, field_type, required, failure_code
    in _RAW_FIELD_METADATA_ROWS
)

FIELD_METADATA = MappingProxyType(
    {
        collection: MappingProxyType(
            {
                row["path"]: row
                for row in FIELD_METADATA_ROWS
                if row["collection"] == collection
            }
        )
        for collection in {
            row["collection"]
            for row in FIELD_METADATA_ROWS
        }
    }
)

COLLECTION_KEYS = MappingProxyType(
    {
        "candidate": "candidates",
        "origin": "origins",
        "context": "contexts",
        "evidence": "evidence_records",
        "projection": "projection_records",
        "build_attestation": "build_attestations",
        "invalid_record": "invalid_records",
    }
)

COLLECTION_ID_FIELDS = MappingProxyType(
    {
        "candidate": "candidate_id",
        "origin": "origin_id",
        "context": "context_id",
        "evidence": "evidence_id",
        "projection": "projection_id",
        "build_attestation": "attestation_id",
        "invalid_record": "invalid_record_id",
    }
)

UNKNOWN_FIELD_CODES = MappingProxyType(
    {
        "package": "PKG_FIELD_INVALID",
        "candidate": "CANDIDATE_FIELD_INVALID",
        "origin": "ORIGIN_FIELD_INVALID",
        "context": "CONTEXT_FIELD_INVALID",
        "evidence": "EVIDENCE_FIELD_INVALID",
        "projection": "PROJECTION_FIELD_INVALID",
        "build_attestation": "BUILD_ATTESTATION_FIELD_INVALID",
        "invalid_record": "INVALID_RECORD_FIELD_INVALID",
        "validation_summary": "VALIDATION_SUMMARY_FIELD_INVALID",
    }
)

RUNTIME_FORBIDDEN_FIELDS = frozenset(
    {
        "gold_slot_id",
        "gold_name",
        "gold_aliases",
        "coverage",
        "coverage_rate",
        "miss_name",
        "miss_reason",
        "quality_label",
        "evaluation_label",
        "material_specific_mapping",
        "expected_concept",
    }
)

RESERVED_NON_EMITTED_CODES = frozenset({RECORD_HASH_MISMATCH})

_HEX_DIGITS = frozenset("0123456789abcdef")

_HASH_FIELDS = {
    "candidate": "CANDIDATE_HASH_MISMATCH",
    "origin": "ORIGIN_HASH_MISMATCH",
    "context": "CONTEXT_HASH_MISMATCH",
    "evidence": "EVIDENCE_HASH_MISMATCH",
    "projection": "PROJECTION_HASH_MISMATCH",
    "build_attestation": "BUILD_ATTESTATION_HASH_MISMATCH",
    "invalid_record": "INVALID_RECORD_HASH_MISMATCH",
}

_INVALID_RECORD_COLLECTION = {
    "candidate": "candidates",
    "origin": "origins",
    "context": "contexts",
    "evidence": "evidence_records",
    "projection": "projection_records",
    "build_attestation": "build_attestations",
    "invalid_record": "package",
    "validation_summary": "package",
    "package": "package",
}

_LIST_FIELD_COLLECTION = {
    "candidates": "candidate",
    "origins": "origin",
    "contexts": "context",
    "evidence_records": "evidence",
    "projection_records": "projection",
    "build_attestations": "build_attestation",
    "invalid_records": "invalid_record",
}

def _validate_package_fields(
    package: Mapping[str, Any],
    normalized_source: Mapping[str, Any],
    failures: list[tuple[str, str, str, str]],
) -> None:
    package_id = _record_id(package, "package", 0)
    _validate_closed_keys(package, "package", package_id, failures)
    for field, metadata in FIELD_METADATA["package"].items():
        if field not in package:
            if metadata["required"]:
                _add_failure(failures, "package", package_id, metadata["validation_failure_code"], field)
            continue
        if not _valid_package_field(field, package[field], package, normalized_source):
            _add_failure(failures, "package", package_id, metadata["validation_failure_code"], field)
    if _contains_forbidden_field(package):
        _add_failure(failures, "package", package_id, "PKG_FIELD_INVALID", "runtime_forbidden_field")

def _valid_package_field(
    field: str,
    value: Any,
    package: Mapping[str, Any],
    normalized_source: Mapping[str, Any] | None,
) -> bool:
    if field == "schema_version":
        return value == PACKAGE_SCHEMA_VERSION
    if field in {"package_id", "material_id"}:
        return _non_empty_string(value)
    if field == "status":
        return value in {"built", "PASS", "FAIL"}
    if field in {"normalized_source_binding", "candidate_source_binding"}:
        if not _valid_artifact_binding(value):
            return False
        if value.get("material_id") != package.get("material_id"):
            return False
        if field == "normalized_source_binding":
            return (
                _valid_normalized_source_mapping(normalized_source)
                and _binding_matches_source(value, normalized_source)
            )
        return True
    if field == "context_policy_binding":
        return (
            _valid_policy_binding(value)
            and value.get("policy_version") == CONTEXT_POLICY_VERSION
        )
    if field == "projection_policy_binding":
        return _valid_policy_binding(value)
    if field in _LIST_FIELD_COLLECTION:
        if not isinstance(value, list):
            return False
        if field == "candidates" and not value:
            return False
        collection = _LIST_FIELD_COLLECTION[field]
        id_field = COLLECTION_ID_FIELDS[collection]
        ids = [
            record.get(id_field)
            for record in value
            if isinstance(record, Mapping)
        ]
        if len(ids) != len(value):
            return False
        if all(_non_empty_string(item) for item in ids):
            if ids != sorted(ids) or len(ids) != len(set(ids)):
                return False
        if field == "build_attestations" and len(value) != 1:
            return False
        return True
    if field == "content_sha256":
        return _sha256_hex(value)
    if field == "validation_summary":
        return isinstance(value, Mapping)
    if field == "canonical_sha256":
        return _sha256_hex(value)
    return False

def _validate_record_fields(
    package: Mapping[str, Any],
    failures: list[tuple[str, str, str, str]],
) -> None:
    for collection, package_key in COLLECTION_KEYS.items():
        records = package.get(package_key)
        if not isinstance(records, list):
            continue
        for index, raw_record in enumerate(records):
            if not isinstance(raw_record, Mapping):
                continue
            record_id = _record_id(raw_record, collection, index)
            _validate_closed_keys(raw_record, collection, record_id, failures)
            for field, metadata in FIELD_METADATA[collection].items():
                if field not in raw_record:
                    if metadata["required"]:
                        _add_failure(
                            failures,
                            collection,
                            record_id,
                            metadata["validation_failure_code"],
                            field,
                        )
                    continue
                if not _valid_record_field(
                    collection,
                    field,
                    raw_record[field],
                    raw_record,
                    package,
                ):
                    _add_failure(
                        failures,
                        collection,
                        record_id,
                        metadata["validation_failure_code"],
                        field,
                    )

    summary = package.get("validation_summary")
    if not isinstance(summary, Mapping):
        return
    package_id = _record_id(package, "package", 0)
    _validate_closed_keys(
        summary,
        "validation_summary",
        package_id,
        failures,
    )
    for field, metadata in FIELD_METADATA["validation_summary"].items():
        if field not in summary:
            if metadata["required"]:
                _add_failure(
                    failures,
                    "validation_summary",
                    package_id,
                    metadata["validation_failure_code"],
                    field,
                )
            continue
        if not _valid_summary_field(field, summary[field]):
            _add_failure(
                failures,
                "validation_summary",
                package_id,
                metadata["validation_failure_code"],
                field,
            )

def _valid_record_field(
    collection: str,
    field: str,
    value: Any,
    record: Mapping[str, Any],
    package: Mapping[str, Any],
) -> bool:
    if field == "canonical_sha256":
        return _sha256_hex(value)
    if field in COLLECTION_ID_FIELDS.values():
        return _non_empty_string(value)
    if field == "material_id":
        return _non_empty_string(value)

    if collection == "candidate":
        if field in {"surface", "normalized_surface"}:
            return _non_empty_string(value)
        if field == "generator_kinds":
            return _sorted_unique_strings(value, non_empty=True)
        if field in {"origin_ids", "context_ids", "evidence_refs"}:
            return _sorted_unique_strings(value, non_empty=True)
        if field == "projection_ids":
            return _sorted_unique_strings(value)
        if field == "support":
            return _valid_support(value, record)
        if field == "construction_status":
            return value in {"valid", "invalid"}
        if field == "failure_codes":
            if not _sorted_unique_strings(value):
                return False
            status = record.get("construction_status")
            return (status == "valid" and not value) or (
                status == "invalid" and bool(value)
            )

    if collection == "origin":
        if field in {
            "candidate_id",
            "block_id",
            "layout_unit_id",
            "source_ref",
            "safe_context_id",
        }:
            return _non_empty_string(value)
        if field == "pdf_page":
            return _integer(value) and value >= 1
        if field == "reading_order":
            return _integer(value) and value >= 0
        if field == "bbox":
            return _valid_bbox(value)
        if field == "literal_span":
            return _valid_literal_span(value)
        if field == "layout_unit_text_sha256":
            return _sha256_hex(value)

    if collection == "context":
        if field in {"text", "normalized_text"}:
            return _non_empty_string(value)
        if field == "layout_unit_refs":
            return (
                isinstance(value, list)
                and 1 <= len(value) <= 3
                and all(_layout_unit_id(item) is not None for item in value)
            )
        if field in {"primary_candidate_ids", "evidence_refs"}:
            return _sorted_unique_strings(value, non_empty=True)
        if field == "context_scope":
            return value == CONTEXT_POLICY_VERSION
        if field in {"start_locator", "end_locator"}:
            return _valid_locator(value)
        if field == "boundary_reason":
            return _valid_boundary_reason(value)
        if field == "code_point_count":
            return (
                _integer(value)
                and 1 <= value <= 1200
                and value == len(record.get("text", ""))
            )

    if collection == "evidence":
        if field == "evidence_kind":
            return value in {
                "candidate_literal",
                "explicit_alias",
                "heading",
                "definition",
                "projection_literal",
            }
        if field in {
            "statement",
            "normalized_statement",
            "literal_surface",
        }:
            return _non_empty_string(value)
        if field == "literal_span":
            return _valid_literal_span(value)
        if field in {"candidate_ids", "context_ids", "origin_ids"}:
            return _sorted_unique_strings(value, non_empty=True)

    if collection == "projection":
        if field == "projection_kind":
            return value in {
                "longer_literal_substring",
                "explicit_alias",
                "heading_definition",
            }
        if field in {
            "source_candidate_ids",
            "source_context_ids",
            "source_evidence_refs",
        }:
            return _sorted_unique_strings(value, non_empty=True)
        if field in {
            "projected_surface",
            "normalized_projected_surface",
            "algorithm_version",
        }:
            return _non_empty_string(value)
        if field == "literal_span":
            return _valid_literal_span(value)

    if collection == "build_attestation":
        if field in {
            "package_id",
            "builder_component",
            "builder_version",
        }:
            return _non_empty_string(value)
        if field == "input_bindings":
            return _valid_input_bindings(value)
        if field == "replay_count":
            return value == 0 and not isinstance(value, bool)
        if field == "replay_content_sha256s":
            return value == []
        if field == "deterministic_replay_pass":
            return value is False
        if field == "record_counts":
            return _valid_record_counts(value, package)

    if collection == "invalid_record":
        if field == "collection":
            return value in {
                "candidates",
                "origins",
                "contexts",
                "evidence_records",
                "projection_records",
                "build_attestations",
                "package",
            }
        if field == "record_id":
            return _non_empty_string(value)
        if field == "failure_codes":
            return (
                _sorted_unique_strings(value, non_empty=True)
                and RECORD_HASH_MISMATCH not in value
            )
        if field == "reason":
            return _non_empty_string(value) and len(value) <= 512
    return False

def _valid_summary_field(field: str, value: Any) -> bool:
    if field in {"validation_run_id", "validator_version"}:
        return _non_empty_string(value)
    if field == "validated_content_sha256":
        return _sha256_hex(value)
    if field == "status":
        return value in {"PASS", "FAIL"}
    if field == "failure_count":
        return _integer(value) and value >= 0
    if field == "failure_code_counts":
        return (
            isinstance(value, Mapping)
            and all(
                _non_empty_string(code)
                and _integer(count)
                and count >= 0
                and code not in RESERVED_NON_EMITTED_CODES
                for code, count in value.items()
            )
        )
    return False

def _validate_closed_keys(
    record: Mapping[str, Any],
    collection: str,
    record_id: str,
    failures: list[tuple[str, str, str, str]],
) -> None:
    unknown = sorted(set(record) - set(FIELD_METADATA[collection]))
    if unknown:
        _add_failure(
            failures,
            collection,
            record_id,
            UNKNOWN_FIELD_CODES[collection],
            ",".join(unknown),
        )

def _validate_candidate_lifecycle(
    package: Mapping[str, Any],
    failures: list[tuple[str, str, str, str]],
) -> None:
    candidates = package.get("candidates")
    if not isinstance(candidates, list):
        return
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, Mapping):
            continue
        if candidate.get("construction_status") != "invalid":
            continue
        failure_codes = candidate.get("failure_codes")
        if not _sorted_unique_strings(failure_codes, non_empty=True):
            continue
        candidate_id = _record_id(candidate, "candidate", index)
        for failure_code in failure_codes:
            _add_failure(
                failures,
                "candidate",
                candidate_id,
                failure_code,
                "candidate.failure_codes",
            )

def _add_failure(
    failures: list[tuple[str, str, str, str]],
    collection: str,
    record_id: str,
    code: str,
    detail: str,
) -> None:
    failure = (collection, record_id, code, detail)
    if failure not in failures:
        failures.append(failure)

def _record_id(record: Mapping[str, Any], collection: str, index: int) -> str:
    if collection == "package":
        value = record.get("package_id")
        return value if _non_empty_string(value) else "package"
    field = COLLECTION_ID_FIELDS.get(collection)
    value = record.get(field) if field is not None else None
    return value if _non_empty_string(value) else f"{collection}[{index}]"

def _valid_artifact_binding(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    required = {
        "artifact_id",
        "schema_version",
        "material_id",
        "raw_sha256",
        "locator",
    }
    return (
        required <= set(value)
        and all(
            _non_empty_string(value.get(field))
            for field in ("artifact_id", "schema_version", "material_id")
        )
        and _sha256_hex(value.get("raw_sha256"))
        and _valid_locator(value.get("locator"))
        and not _contains_forbidden_field(value)
    )

def _valid_policy_binding(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and _non_empty_string(value.get("policy_version"))
        and _sha256_hex(value.get("canonical_sha256"))
        and not _contains_forbidden_field(value)
    )

def _valid_normalized_source_mapping(value: Any) -> bool:
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
    return (
        len(unit_ids) == len(units)
        and all(_non_empty_string(unit_id) for unit_id in unit_ids)
        and len(unit_ids) == len(set(unit_ids))
    )

def _binding_matches_source(
    binding: Mapping[str, Any],
    source: Any,
) -> bool:
    return isinstance(source, Mapping) and all(
        binding.get(field) == source.get(field)
        for field in (
            "artifact_id",
            "schema_version",
            "material_id",
            "raw_sha256",
            "locator",
        )
    )

def _valid_support(value: Any, record: Mapping[str, Any]) -> bool:
    if not isinstance(value, Mapping):
        return False
    if set(value) != {
        "flags",
        "origin_count",
        "context_count",
        "hard_negative_gate",
    }:
        return False
    return (
        isinstance(value.get("flags"), Mapping)
        and _integer(value.get("origin_count"))
        and value.get("origin_count") == _list_length(record.get("origin_ids"))
        and _integer(value.get("context_count"))
        and value.get("context_count") == _list_length(record.get("context_ids"))
        and isinstance(value.get("hard_negative_gate"), bool)
        and not _contains_forbidden_field(value)
    )

def _valid_input_bindings(value: Any) -> bool:
    if not isinstance(value, list) or not value:
        return False
    if not all(_valid_artifact_binding(binding) for binding in value):
        return False
    identities = [
        (binding["artifact_id"], binding["schema_version"], binding["raw_sha256"])
        for binding in value
    ]
    return identities == sorted(identities) and len(identities) == len(set(identities))

def _valid_record_counts(value: Any, package: Mapping[str, Any]) -> bool:
    expected = {
        "candidates": _list_length(package.get("candidates")),
        "origins": _list_length(package.get("origins")),
        "contexts": _list_length(package.get("contexts")),
        "evidence_records": _list_length(package.get("evidence_records")),
        "projection_records": _list_length(package.get("projection_records")),
    }
    return value == expected

def _valid_boundary_reason(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and set(value) == {"previous", "next", "limits"}
        and isinstance(value.get("previous"), str)
        and isinstance(value.get("next"), str)
        and _sorted_unique_strings(value.get("limits"))
    )

def _valid_locator(value: Any) -> bool:
    return _non_empty_string(value) or (
        isinstance(value, Mapping)
        and bool(value)
        and not _contains_forbidden_field(value)
    )

def _layout_unit_id(value: Any) -> str | None:
    if _non_empty_string(value):
        return value
    if isinstance(value, Mapping) and _non_empty_string(value.get("layout_unit_id")):
        return value["layout_unit_id"]
    return None

def _valid_bbox(value: Any) -> bool:
    if not isinstance(value, list) or len(value) != 4:
        return False
    if not all(_finite_number(item) for item in value):
        return False
    x0, y0, x1, y1 = value
    return x1 > x0 and y1 > y0

def _valid_literal_span(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and set(value) == {"start", "end"}
        and _integer(value.get("start"))
        and _integer(value.get("end"))
        and 0 <= value["start"] < value["end"]
    )

def _sorted_unique_strings(value: Any, *, non_empty: bool = False) -> bool:
    return (
        isinstance(value, list)
        and (bool(value) or not non_empty)
        and all(_non_empty_string(item) for item in value)
        and value == sorted(value)
        and len(value) == len(set(value))
    )

def _sha256_hex(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and set(value) <= _HEX_DIGITS
    )

def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value)

def _integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)

def _finite_number(value: Any) -> bool:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
    ):
        return False
    return not (
        isinstance(value, float)
        and value == 0.0
        and math.copysign(1.0, value) < 0
    )

def _list_length(value: Any) -> int:
    return len(value) if isinstance(value, list) else -1

def _string_or_empty(value: Any) -> str:
    return value if isinstance(value, str) else ""

def _text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def _contains_forbidden_field(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            key in RUNTIME_FORBIDDEN_FIELDS
            or _contains_forbidden_field(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_forbidden_field(item) for item in value)
    return False
