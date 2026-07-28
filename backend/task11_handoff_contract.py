from __future__ import annotations

import copy
import hashlib
import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any

from material_runtime_files import canonical_json_bytes


PACKAGE_SCHEMA_VERSION = "task11a-task11b-handoff-package/v1"
CONTEXT_POLICY_VERSION = "same-material-local-layout-v1"
VALIDATOR_VERSION = "task11-handoff-contract/v1"
PKG_DRAFT_UNSERIALIZABLE = "PKG_DRAFT_UNSERIALIZABLE"
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
_CONTEXT_FORBIDDEN_KINDS = {
    "caption": "CONTEXT_CROSSES_CAPTION",
    "figure": "CONTEXT_CROSSES_FIGURE",
    "table": "CONTEXT_CROSSES_TABLE",
    "omission": "CONTEXT_CROSSES_OMISSION",
    "image": "CONTEXT_CROSSES_IMAGE",
    "heading": "CONTEXT_CROSSES_HEADING",
}
_SENTENCE_TERMINALS = ("。", ".", "！", "!", "？", "?")


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


def is_task11b_pass_package(
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


def _validate_input_hashes(
    package: Mapping[str, Any],
    failures: list[tuple[str, str, str, str]],
) -> None:
    for collection, package_key in COLLECTION_KEYS.items():
        records = package.get(package_key)
        if not isinstance(records, list):
            continue
        for index, record in enumerate(records):
            if not isinstance(record, Mapping):
                continue
            if record.get("canonical_sha256") != record_canonical_sha256(record):
                _add_failure(
                    failures,
                    collection,
                    _record_id(record, collection, index),
                    _HASH_FIELDS[collection],
                    "canonical_sha256",
                )

    package_id = _record_id(package, "package", 0)
    content_valid = package.get("content_sha256") == package_content_sha256(package)
    if not content_valid:
        _add_failure(
            failures,
            "package",
            package_id,
            "PKG_CONTENT_HASH_MISMATCH",
            "content_sha256",
        )
    summary = package.get("validation_summary")
    if isinstance(summary, Mapping):
        if (
            content_valid
            and summary.get("validated_content_sha256")
            != package.get("content_sha256")
        ):
            _add_failure(
                failures,
                "validation_summary",
                package_id,
                "VALIDATED_CONTENT_HASH_MISMATCH",
                "validated_content_sha256",
            )
        invalid_records = package.get("invalid_records")
        if isinstance(invalid_records, list):
            if summary.get("failure_count") != len(invalid_records):
                _add_failure(
                    failures,
                    "validation_summary",
                    package_id,
                    "VALIDATION_FAILURE_COUNT_MISMATCH",
                    "failure_count",
                )
            expected_counts = _failure_code_counts(invalid_records)
            if summary.get("failure_code_counts") != expected_counts:
                _add_failure(
                    failures,
                    "validation_summary",
                    package_id,
                    "VALIDATION_FAILURE_AGGREGATE_MISMATCH",
                    "failure_code_counts",
                )
            expected_status = "FAIL" if invalid_records else "PASS"
            if summary.get("status") != expected_status:
                _add_failure(
                    failures,
                    "validation_summary",
                    package_id,
                    "VALIDATION_STATUS_INVALID",
                    "status",
                )
    if package.get("canonical_sha256") != package_envelope_sha256(package):
        _add_failure(
            failures,
            "package",
            package_id,
            "PKG_ENVELOPE_HASH_MISMATCH",
            "canonical_sha256",
        )


def _record_indexes(
    package: Mapping[str, Any],
) -> dict[str, dict[str, Mapping[str, Any]]]:
    indexes: dict[str, dict[str, Mapping[str, Any]]] = {}
    for collection, package_key in COLLECTION_KEYS.items():
        records = package.get(package_key)
        index: dict[str, Mapping[str, Any]] = {}
        if isinstance(records, list):
            id_field = COLLECTION_ID_FIELDS[collection]
            for record in records:
                if not isinstance(record, Mapping):
                    continue
                record_id = record.get(id_field)
                if _non_empty_string(record_id):
                    index[record_id] = record
        indexes[collection] = index
    return indexes


_CROSS_REFERENCES = (
    ("candidate", "origin_ids", "origin", "XREF_CANDIDATE_ORIGIN_DANGLING", "XREF_CANDIDATE_ORIGIN_CROSS_MATERIAL"),
    ("candidate", "context_ids", "context", "XREF_CANDIDATE_CONTEXT_DANGLING", "XREF_CANDIDATE_CONTEXT_CROSS_MATERIAL"),
    ("candidate", "evidence_refs", "evidence", "XREF_CANDIDATE_EVIDENCE_DANGLING", "XREF_CANDIDATE_EVIDENCE_CROSS_MATERIAL"),
    ("candidate", "projection_ids", "projection", "XREF_CANDIDATE_PROJECTION_DANGLING", "XREF_CANDIDATE_PROJECTION_CROSS_MATERIAL"),
    ("origin", "candidate_id", "candidate", "XREF_ORIGIN_CANDIDATE_DANGLING", "XREF_ORIGIN_CANDIDATE_CROSS_MATERIAL"),
    ("origin", "safe_context_id", "context", "XREF_ORIGIN_CONTEXT_DANGLING", "XREF_ORIGIN_CONTEXT_CROSS_MATERIAL"),
    ("context", "primary_candidate_ids", "candidate", "XREF_CONTEXT_CANDIDATE_DANGLING", "XREF_CONTEXT_CANDIDATE_CROSS_MATERIAL"),
    ("context", "evidence_refs", "evidence", "XREF_CONTEXT_EVIDENCE_DANGLING", "XREF_CONTEXT_EVIDENCE_CROSS_MATERIAL"),
    ("evidence", "candidate_ids", "candidate", "XREF_EVIDENCE_CANDIDATE_DANGLING", "XREF_EVIDENCE_CANDIDATE_CROSS_MATERIAL"),
    ("evidence", "context_ids", "context", "XREF_EVIDENCE_CONTEXT_DANGLING", "XREF_EVIDENCE_CONTEXT_CROSS_MATERIAL"),
    ("evidence", "origin_ids", "origin", "XREF_EVIDENCE_ORIGIN_DANGLING", "XREF_EVIDENCE_ORIGIN_CROSS_MATERIAL"),
    ("projection", "source_candidate_ids", "candidate", "XREF_PROJECTION_CANDIDATE_DANGLING", "XREF_PROJECTION_CANDIDATE_CROSS_MATERIAL"),
    ("projection", "source_context_ids", "context", "XREF_PROJECTION_CONTEXT_DANGLING", "XREF_PROJECTION_CONTEXT_CROSS_MATERIAL"),
    ("projection", "source_evidence_refs", "evidence", "XREF_PROJECTION_EVIDENCE_DANGLING", "XREF_PROJECTION_EVIDENCE_CROSS_MATERIAL"),
)


def _validate_cross_references(
    package: Mapping[str, Any],
    indexes: Mapping[str, Mapping[str, Mapping[str, Any]]],
    failures: list[tuple[str, str, str, str]],
) -> None:
    for source_collection, field, target_collection, dangling_code, material_code in _CROSS_REFERENCES:
        records = package.get(COLLECTION_KEYS[source_collection])
        if not isinstance(records, list):
            continue
        for index, record in enumerate(records):
            if not isinstance(record, Mapping):
                continue
            value = record.get(field)
            references = value if isinstance(value, list) else [value]
            if not all(_non_empty_string(reference) for reference in references):
                continue
            record_id = _record_id(record, source_collection, index)
            for reference in references:
                target = indexes[target_collection].get(reference)
                if target is None:
                    _add_failure(
                        failures,
                        source_collection,
                        record_id,
                        dangling_code,
                        field,
                    )
                elif (
                    _non_empty_string(record.get("material_id"))
                    and _non_empty_string(target.get("material_id"))
                    and record.get("material_id") != target.get("material_id")
                ):
                    _add_failure(
                        failures,
                        source_collection,
                        record_id,
                        material_code,
                        field,
                    )
    _validate_all_origins_are_referenced(package, indexes, failures)


def _validate_all_origins_are_referenced(
    package: Mapping[str, Any],
    indexes: Mapping[str, Mapping[str, Mapping[str, Any]]],
    failures: list[tuple[str, str, str, str]],
) -> None:
    candidates = package.get("candidates")
    if not isinstance(candidates, list):
        return
    referenced_origin_ids = {
        origin_id
        for candidate in candidates
        if isinstance(candidate, Mapping)
        and isinstance(candidate.get("origin_ids"), list)
        for origin_id in candidate["origin_ids"]
        if _non_empty_string(origin_id)
    }
    unreferenced = sorted(
        set(indexes["origin"]) - referenced_origin_ids
    )
    if unreferenced:
        _add_failure(
            failures,
            "package",
            _record_id(package, "package", 0),
            "PKG_ORIGINS_INVALID",
            f"unreferenced_origins:{','.join(unreferenced)}",
        )


def _validate_materials(
    package: Mapping[str, Any],
    indexes: Mapping[str, Mapping[str, Mapping[str, Any]]],
    failures: list[tuple[str, str, str, str]],
) -> None:
    package_material = package.get("material_id")
    if not _non_empty_string(package_material):
        return
    for collection in ("candidate", "origin", "context", "evidence", "projection"):
        code = FIELD_METADATA[collection]["material_id"]["validation_failure_code"]
        for index, record in enumerate(indexes[collection].values()):
            material_id = record.get("material_id")
            if _non_empty_string(material_id) and material_id != package_material:
                _add_failure(
                    failures,
                    collection,
                    _record_id(record, collection, index),
                    code,
                    "material_id",
                )


def _validated_source_units(
    package: Mapping[str, Any],
    normalized_source: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    binding = package.get("normalized_source_binding")
    if (
        not _valid_normalized_source_mapping(normalized_source)
        or not isinstance(binding, Mapping)
        or not _binding_matches_source(binding, normalized_source)
    ):
        return {}
    units = normalized_source.get("layout_units")
    output: dict[str, Mapping[str, Any]] = {}
    for unit in units:
        unit_id = unit.get("layout_unit_id")
        output[unit_id] = unit
    return output


def _validate_literal_and_source_bindings(
    package: Mapping[str, Any],
    indexes: Mapping[str, Mapping[str, Mapping[str, Any]]],
    source_units: Mapping[str, Mapping[str, Any]],
    failures: list[tuple[str, str, str, str]],
) -> None:
    for index, evidence in enumerate(indexes["evidence"].values()):
        span = evidence.get("literal_span")
        statement = evidence.get("statement")
        literal = evidence.get("literal_surface")
        if (
            _valid_literal_span(span)
            and isinstance(statement, str)
            and isinstance(literal, str)
            and statement[span["start"]:span["end"]] != literal
        ):
            _add_failure(
                failures,
                "evidence",
                _record_id(evidence, "evidence", index),
                "EVIDENCE_LITERAL_SPAN_INVALID",
                "literal_span",
            )

    for index, projection in enumerate(indexes["projection"].values()):
        span = projection.get("literal_span")
        surface = projection.get("projected_surface")
        evidence_refs = projection.get("source_evidence_refs")
        if (
            not _valid_literal_span(span)
            or not isinstance(surface, str)
            or not isinstance(evidence_refs, list)
        ):
            continue
        statements = [
            indexes["evidence"][evidence_id].get("statement")
            for evidence_id in evidence_refs
            if evidence_id in indexes["evidence"]
        ]
        if not any(
            isinstance(statement, str)
            and statement[span["start"]:span["end"]] == surface
            for statement in statements
        ):
            _add_failure(
                failures,
                "projection",
                _record_id(projection, "projection", index),
                "PROJECTION_LITERAL_SPAN_INVALID",
                "literal_span",
            )

    for index, origin in enumerate(indexes["origin"].values()):
        origin_id = _record_id(origin, "origin", index)
        unit_id = origin.get("layout_unit_id")
        if not _non_empty_string(unit_id):
            continue
        unit = source_units.get(unit_id)
        if unit is None:
            _add_failure(
                failures,
                "origin",
                origin_id,
                "ORIGIN_LAYOUT_UNIT_REF_INVALID",
                "layout_unit_id",
            )
            continue
        _compare_origin_to_source(origin, origin_id, unit, indexes, failures)


def _compare_origin_to_source(
    origin: Mapping[str, Any],
    origin_id: str,
    unit: Mapping[str, Any],
    indexes: Mapping[str, Mapping[str, Mapping[str, Any]]],
    failures: list[tuple[str, str, str, str]],
) -> None:
    comparisons = (
        ("material_id", "ORIGIN_MATERIAL_MISMATCH"),
        ("block_id", "ORIGIN_BLOCK_REF_INVALID"),
        ("source_ref", "ORIGIN_SOURCE_REF_INVALID"),
        ("pdf_page", "ORIGIN_PAGE_INVALID"),
        ("reading_order", "ORIGIN_READING_ORDER_INVALID"),
        ("bbox", "ORIGIN_BBOX_INVALID"),
    )
    for field, code in comparisons:
        if field in origin and field in unit and origin.get(field) != unit.get(field):
            _add_failure(failures, "origin", origin_id, code, field)
    text = unit.get("text")
    if isinstance(text, str):
        if origin.get("layout_unit_text_sha256") != _text_sha256(text):
            _add_failure(
                failures,
                "origin",
                origin_id,
                "ORIGIN_TEXT_HASH_MISMATCH",
                "layout_unit_text_sha256",
            )
        candidate = indexes["candidate"].get(origin.get("candidate_id"))
        span = origin.get("literal_span")
        if (
            candidate is not None
            and isinstance(candidate.get("surface"), str)
            and _valid_literal_span(span)
            and text[span["start"]:span["end"]] != candidate.get("surface")
        ):
            _add_failure(
                failures,
                "origin",
                origin_id,
                "ORIGIN_LITERAL_SPAN_INVALID",
                "literal_span",
            )


def _validate_context_boundaries(
    package: Mapping[str, Any],
    source_units: Mapping[str, Mapping[str, Any]],
    failures: list[tuple[str, str, str, str]],
) -> None:
    contexts = package.get("contexts")
    if not isinstance(contexts, list):
        return
    for index, context in enumerate(contexts):
        if not isinstance(context, Mapping):
            continue
        record_id = _record_id(context, "context", index)
        refs = context.get("layout_unit_refs")
        if not isinstance(refs, list):
            continue
        units = [
            source_units.get(unit_id)
            for unit_id in (_layout_unit_id(ref) for ref in refs)
            if unit_id is not None
        ]
        if len(units) != len(refs) or any(unit is None for unit in units):
            _add_failure(
                failures,
                "context",
                record_id,
                "CONTEXT_LAYOUT_REFS_INVALID",
                "layout_unit_refs",
            )
            continue
        resolved = [unit for unit in units if unit is not None]
        _validate_context_units(context, record_id, resolved, failures)
        _validate_anchor_overflow(
            package,
            context,
            record_id,
            source_units,
            failures,
        )


def _validate_anchor_overflow(
    package: Mapping[str, Any],
    context: Mapping[str, Any],
    record_id: str,
    source_units: Mapping[str, Mapping[str, Any]],
    failures: list[tuple[str, str, str, str]],
) -> None:
    primary_candidates = context.get("primary_candidate_ids")
    origins = package.get("origins")
    if not isinstance(primary_candidates, list) or not isinstance(origins, list):
        return
    anchor_unit_ids = {
        origin.get("layout_unit_id")
        for origin in origins
        if isinstance(origin, Mapping)
        and origin.get("candidate_id") in primary_candidates
        and origin.get("safe_context_id") == context.get("context_id")
        and _non_empty_string(origin.get("layout_unit_id"))
    }
    for unit_id in anchor_unit_ids:
        unit = source_units.get(unit_id)
        if unit is not None and isinstance(unit.get("text"), str):
            if len(unit["text"]) > 1200:
                _add_failure(
                    failures,
                    "context",
                    record_id,
                    "CONTEXT_ANCHOR_OVERFLOW",
                    "layout_unit_refs",
                )


def _validate_context_units(
    context: Mapping[str, Any],
    record_id: str,
    units: list[Mapping[str, Any]],
    failures: list[tuple[str, str, str, str]],
) -> None:
    material_ids = {unit.get("material_id") for unit in units}
    if len(material_ids) != 1 or context.get("material_id") not in material_ids:
        _add_failure(failures, "context", record_id, "CONTEXT_CROSS_MATERIAL", "layout_unit_refs")
    pages = {unit.get("pdf_page") for unit in units}
    if len(pages) != 1:
        _add_failure(failures, "context", record_id, "CONTEXT_CROSS_PAGE", "layout_unit_refs")
    columns = {unit.get("column_id") for unit in units if unit.get("column_id") is not None}
    if len(columns) > 1:
        _add_failure(failures, "context", record_id, "CONTEXT_CROSS_COLUMN", "layout_unit_refs")
    for unit in units:
        code = _CONTEXT_FORBIDDEN_KINDS.get(unit.get("unit_kind"))
        if code is not None:
            _add_failure(failures, "context", record_id, code, "layout_unit_refs")

    orders = [unit.get("reading_order") for unit in units]
    if (
        not all(_integer(order) for order in orders)
        or any(right != left + 1 for left, right in zip(orders, orders[1:]))
    ):
        _add_failure(
            failures,
            "context",
            record_id,
            "CONTEXT_NON_CONSECUTIVE_ORDER",
            "layout_unit_refs",
        )
    for left, right in zip(units, units[1:]):
        _validate_adjacent_units(record_id, left, right, failures)

    texts = [unit.get("text") for unit in units]
    if all(isinstance(text, str) for text in texts):
        joined = "\n".join(texts)
        if context.get("text") != joined:
            _add_failure(failures, "context", record_id, "CONTEXT_TEXT_INVALID", "text")
        normalized = [unit.get("normalized_text") for unit in units]
        if all(isinstance(text, str) for text in normalized):
            if context.get("normalized_text") != "\n".join(normalized):
                _add_failure(
                    failures,
                    "context",
                    record_id,
                    "CONTEXT_NORMALIZED_TEXT_INVALID",
                    "normalized_text",
                )
    first_locator = units[0].get("locator")
    last_locator = units[-1].get("locator")
    if first_locator is not None and context.get("start_locator") != first_locator:
        _add_failure(
            failures,
            "context",
            record_id,
            "CONTEXT_START_LOCATOR_INVALID",
            "start_locator",
        )
    if last_locator is not None and context.get("end_locator") != last_locator:
        _add_failure(
            failures,
            "context",
            record_id,
            "CONTEXT_END_LOCATOR_INVALID",
            "end_locator",
        )
    if len(units) > 3:
        _add_failure(
            failures,
            "context",
            record_id,
            "CONTEXT_UNIT_LIMIT_EXCEEDED",
            "layout_unit_refs",
        )
    text = context.get("text")
    if isinstance(text, str) and len(text) > 1200:
        _add_failure(
            failures,
            "context",
            record_id,
            "CONTEXT_LENGTH_LIMIT_EXCEEDED",
            "text",
        )


def _validate_adjacent_units(
    record_id: str,
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    failures: list[tuple[str, str, str, str]],
) -> None:
    left_bbox = left.get("bbox")
    right_bbox = right.get("bbox")
    if _valid_bbox(left_bbox) and _valid_bbox(right_bbox):
        overlap = max(
            0.0,
            min(left_bbox[2], right_bbox[2])
            - max(left_bbox[0], right_bbox[0]),
        )
        minimum_width = min(
            left_bbox[2] - left_bbox[0],
            right_bbox[2] - right_bbox[0],
        )
        if overlap / minimum_width < 0.60:
            _add_failure(
                failures,
                "context",
                record_id,
                "CONTEXT_HORIZONTAL_OVERLAP_LOW",
                "layout_unit_refs",
            )
        gap = max(0.0, right_bbox[1] - left_bbox[3])
        font_sizes = [
            value
            for value in (
                left.get("font_size_max"),
                right.get("font_size_max"),
            )
            if _finite_number(value)
        ]
        if not font_sizes:
            font_sizes = [0.0]
        if gap > max(2.5 * max(font_sizes), 24.0):
            _add_failure(
                failures,
                "context",
                record_id,
                "CONTEXT_VERTICAL_GAP_HIGH",
                "layout_unit_refs",
            )
    left_text = left.get("text")
    if (
        isinstance(left_text, str)
        and left_text.rstrip().endswith(_SENTENCE_TERMINALS)
    ):
        _add_failure(
            failures,
            "context",
            record_id,
            "CONTEXT_SENTENCE_TERMINAL_BOUNDARY",
            "layout_unit_refs",
        )
    if left.get("heading_transition_after") is True or right.get("heading_transition_before") is True:
        _add_failure(
            failures,
            "context",
            record_id,
            "CONTEXT_CROSSES_HEADING",
            "layout_unit_refs",
        )
    intervening_kind = left.get("intervening_kind_after")
    code = _CONTEXT_FORBIDDEN_KINDS.get(intervening_kind)
    if code is not None:
        _add_failure(failures, "context", record_id, code, "layout_unit_refs")


def _generated_invalid_records(
    failures: Sequence[tuple[str, str, str, str]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, set[str]]] = defaultdict(
        lambda: {"codes": set(), "details": set()}
    )
    for collection, record_id, code, detail in failures:
        if code in RESERVED_NON_EMITTED_CODES:
            continue
        target = (collection, record_id)
        grouped[target]["codes"].add(code)
        grouped[target]["details"].add(f"{code}:{detail}")

    records: list[dict[str, Any]] = []
    for (collection, record_id), values in sorted(grouped.items()):
        failure_codes = sorted(values["codes"])
        identity = {
            "collection": _INVALID_RECORD_COLLECTION[collection],
            "record_id": record_id,
            "failure_codes": failure_codes,
        }
        record = {
            "invalid_record_id": f"invalid-{canonical_sha256(identity)[:24]}",
            **identity,
            "reason": ";".join(sorted(values["details"]))[:512],
        }
        record["canonical_sha256"] = record_canonical_sha256(record)
        records.append(record)
    return records


def _validation_summary(
    sealed: Mapping[str, Any],
    *,
    input_summary: Any,
) -> dict[str, Any]:
    invalid_records = sealed["invalid_records"]
    counts = _failure_code_counts(invalid_records)
    status = "FAIL" if invalid_records else "PASS"
    unknown_fields = (
        {
            key: copy.deepcopy(value)
            for key, value in input_summary.items()
            if key not in FIELD_METADATA["validation_summary"]
        }
        if isinstance(input_summary, Mapping)
        else {}
    )
    identity = {
        "package_id": sealed.get("package_id"),
        "content_sha256": sealed["content_sha256"],
        "validator_version": VALIDATOR_VERSION,
    }
    return {
        **unknown_fields,
        "validation_run_id": f"validation-{canonical_sha256(identity)[:24]}",
        "validator_version": VALIDATOR_VERSION,
        "validated_content_sha256": sealed["content_sha256"],
        "status": status,
        "failure_count": len(invalid_records),
        "failure_code_counts": counts,
    }


def _failure_code_counts(invalid_records: Any) -> dict[str, int]:
    counts: Counter[str] = Counter()
    if isinstance(invalid_records, list):
        for record in invalid_records:
            if not isinstance(record, Mapping):
                continue
            codes = record.get("failure_codes")
            if isinstance(codes, list):
                counts.update(
                    code
                    for code in codes
                    if _non_empty_string(code)
                    and code not in RESERVED_NON_EMITTED_CODES
                )
    return dict(sorted(counts.items()))


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
