from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from material_runtime_files import canonical_json_bytes
from .context_builder import (
    FONT_GAP_MULTIPLIER,
    MAX_CONTEXT_CODE_POINTS,
    MAX_CONTEXT_UNITS,
    MINIMUM_HORIZONTAL_OVERLAP,
    MINIMUM_VERTICAL_GAP_LIMIT,
    _rebuild_contexts,
)
from .contract import seal_handoff_draft
from .contract_hashing import (
    _canonical_sha256,
    _stable_id,
    package_content_sha256,
    package_envelope_sha256,
    record_canonical_sha256,
)
from .contract_schema import (
    CONTEXT_POLICY_VERSION,
    PACKAGE_SCHEMA_VERSION,
    VALIDATOR_VERSION,
    _non_empty_string,
)
from .source_failure_policy import (
    _apply_candidate_failures,
    _classify_source_failures,
    _package_invalid_records,
)
from presemantic_records_provider import (
    NORMALIZED_SOURCE_SCHEMA_VERSION,
    PACKAGE_INPUT_SCHEMA_VERSION,
    RECORDS_SCHEMA_VERSION,
)


BUILDER_VERSION = "task11-handoff-package/v1"

_PACKAGE_INPUT_FIELDS = {
    "schema_version",
    "package_id",
    "material_id",
    "normalized_source",
    "normalized_source_binding",
    "deterministic_records_binding",
    "context_policy_binding",
    "projection_policy_binding",
    "records_artifact",
}

_NORMALIZED_SOURCE_FIELDS = {
    "artifact_id",
    "schema_version",
    "material_id",
    "raw_sha256",
    "locator",
    "layout_units",
}

_RECORDS_ARTIFACT_FIELDS = {
    "artifact_id",
    "schema_version",
    "material_id",
    "locator",
    "candidates",
    "origins",
    "contexts",
    "evidence_records",
    "projection_records",
    "source_failures",
}

_BINDING_FIELDS = {
    "artifact_id",
    "schema_version",
    "material_id",
    "raw_sha256",
    "locator",
}

_SEMANTIC_AUTHORITY_FIELDS = {
    "alias_relation",
    "aliases",
    "canonical_group",
    "canonicalize",
    "concept_id",
    "concept_name",
    "concept_status",
    "conflict",
    "gold_name",
    "gold_slot_id",
    "merge",
    "ranking",
    "recover",
    "recovery",
    "reject",
    "retain",
    "route",
    "split",
    "teaching_scope",
    "unresolved",
}

_RECORD_COLLECTIONS = (
    ("candidates", "candidate_id"),
    ("origins", "origin_id"),
    ("contexts", "context_id"),
    ("evidence_records", "evidence_id"),
    ("projection_records", "projection_id"),
)

def build_handoff_package(
    package_input: Mapping[str, Any],
) -> dict[str, Any]:
    """Build and seal one deterministic PR1 handoff package."""
    _validate_package_input(package_input)
    copied_input = deepcopy(package_input)
    normalized_source = copied_input["normalized_source"]
    records_artifact = copied_input["records_artifact"]

    collections = {
        key: deepcopy(records_artifact[key])
        for key, _ in _RECORD_COLLECTIONS
    }
    (
        candidate_failures,
        package_failures,
    ) = _classify_source_failures(
        records_artifact["source_failures"],
        collections["candidates"],
        collections["origins"],
        copied_input["material_id"],
    )
    collections["candidates"] = _apply_candidate_failures(
        collections["candidates"],
        candidate_failures,
    )
    collections["contexts"] = _rebuild_contexts(
        collections["contexts"],
        collections["origins"],
        normalized_source["layout_units"],
    )
    for key, id_field in _RECORD_COLLECTIONS:
        collections[key] = _sorted_records(
            collections[key],
            id_field,
        )

    context_policy_binding = {
        "policy_version": CONTEXT_POLICY_VERSION,
        "canonical_sha256": _canonical_sha256(
            {
                "font_gap_multiplier": FONT_GAP_MULTIPLIER,
                "max_code_points": MAX_CONTEXT_CODE_POINTS,
                "max_units": MAX_CONTEXT_UNITS,
                "minimum_horizontal_overlap": MINIMUM_HORIZONTAL_OVERLAP,
                "minimum_vertical_gap_limit": MINIMUM_VERTICAL_GAP_LIMIT,
                "policy_version": CONTEXT_POLICY_VERSION,
                "unknown_boundary": "stop",
            }
        ),
    }
    input_bindings = sorted(
        [
            deepcopy(copied_input["normalized_source_binding"]),
            deepcopy(copied_input["deterministic_records_binding"]),
        ],
        key=lambda binding: (
            binding["artifact_id"],
            binding["schema_version"],
            binding["raw_sha256"],
        ),
    )
    package_id = _stable_id(
        "task11-handoff-package",
        {
            "context_policy_sha256": context_policy_binding[
                "canonical_sha256"
            ],
            "input_package_id": copied_input["package_id"],
            "input_bindings": input_bindings,
            "material_id": copied_input["material_id"],
        },
    )
    attestation = {
        "attestation_id": _stable_id(
            "build-attestation",
            {
                "input_bindings": input_bindings,
                "package_id": package_id,
            },
        ),
        "package_id": package_id,
        "builder_component": "task11_handoff_package",
        "builder_version": BUILDER_VERSION,
        "input_bindings": input_bindings,
        "replay_count": 0,
        "replay_content_sha256s": [],
        "deterministic_replay_pass": False,
        "record_counts": {
            key: len(collections[key])
            for key, _ in _RECORD_COLLECTIONS
        },
    }
    attestation["canonical_sha256"] = record_canonical_sha256(
        attestation
    )
    package_invalid_records = _package_invalid_records(
        package_id,
        package_failures,
    )

    draft = {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "package_id": package_id,
        "material_id": copied_input["material_id"],
        "status": "built",
        "normalized_source_binding": deepcopy(
            copied_input["normalized_source_binding"]
        ),
        "candidate_source_binding": deepcopy(
            copied_input["deterministic_records_binding"]
        ),
        "context_policy_binding": context_policy_binding,
        "projection_policy_binding": deepcopy(
            copied_input["projection_policy_binding"]
        ),
        **collections,
        "build_attestations": [attestation],
        "invalid_records": package_invalid_records,
        "content_sha256": "",
        "validation_summary": {},
        "canonical_sha256": "",
    }
    draft["content_sha256"] = package_content_sha256(draft)
    failure_code_counts: dict[str, int] = {}
    for invalid_record in package_invalid_records:
        for code in invalid_record["failure_codes"]:
            failure_code_counts[code] = (
                failure_code_counts.get(code, 0) + 1
            )
    draft["validation_summary"] = {
        "validation_run_id": _stable_id(
            "draft-validation",
            {
                "content_sha256": draft["content_sha256"],
                "package_id": package_id,
            },
        ),
        "validator_version": VALIDATOR_VERSION,
        "validated_content_sha256": draft["content_sha256"],
        "status": "FAIL" if package_invalid_records else "PASS",
        "failure_count": len(package_invalid_records),
        "failure_code_counts": dict(sorted(failure_code_counts.items())),
    }
    draft["canonical_sha256"] = package_envelope_sha256(draft)
    return seal_handoff_draft(
        draft,
        normalized_source=normalized_source,
    )

def _validate_package_input(package_input: Any) -> None:
    try:
        canonical_json_bytes(package_input)
    except (TypeError, ValueError, RecursionError):
        raise ValueError("invalid package input") from None
    if (
        not isinstance(package_input, Mapping)
        or set(package_input) != _PACKAGE_INPUT_FIELDS
        or package_input.get("schema_version")
        != PACKAGE_INPUT_SCHEMA_VERSION
        or not _non_empty_string(package_input.get("package_id"))
        or not _non_empty_string(package_input.get("material_id"))
        or _contains_semantic_authority(package_input)
    ):
        raise ValueError("invalid package input")

    material_id = package_input["material_id"]
    normalized_source = package_input.get("normalized_source")
    normalized_binding = package_input.get("normalized_source_binding")
    records_artifact = package_input.get("records_artifact")
    records_binding = package_input.get("deterministic_records_binding")
    if (
        not isinstance(normalized_source, Mapping)
        or set(normalized_source) != _NORMALIZED_SOURCE_FIELDS
        or normalized_source.get("schema_version")
        != NORMALIZED_SOURCE_SCHEMA_VERSION
        or normalized_source.get("material_id") != material_id
        or not _binding_matches(normalized_binding, normalized_source)
        or not isinstance(records_artifact, Mapping)
        or set(records_artifact) != _RECORDS_ARTIFACT_FIELDS
        or records_artifact.get("schema_version") != RECORDS_SCHEMA_VERSION
        or records_artifact.get("material_id") != material_id
        or not _binding_matches(records_binding, records_artifact)
        or records_binding["raw_sha256"]
        != _canonical_sha256(records_artifact)
    ):
        raise ValueError("invalid package input")

    units = normalized_source.get("layout_units")
    if not isinstance(units, list):
        raise ValueError("invalid package input")
    unit_ids = [
        unit.get("layout_unit_id")
        for unit in units
        if isinstance(unit, Mapping)
    ]
    if (
        len(unit_ids) != len(units)
        or not all(_non_empty_string(unit_id) for unit_id in unit_ids)
        or len(unit_ids) != len(set(unit_ids))
    ):
        raise ValueError("invalid package input")

    for key, _ in _RECORD_COLLECTIONS:
        if not isinstance(records_artifact.get(key), list):
            raise ValueError("invalid package input")
    if not isinstance(records_artifact.get("source_failures"), list):
        raise ValueError("invalid package input")
    if not _valid_policy_binding(package_input.get("context_policy_binding")):
        raise ValueError("invalid package input")
    if (
        package_input["context_policy_binding"].get("policy_version")
        != CONTEXT_POLICY_VERSION
        or not _valid_policy_binding(
            package_input.get("projection_policy_binding")
        )
    ):
        raise ValueError("invalid package input")

def _binding_matches(binding: Any, artifact: Mapping[str, Any]) -> bool:
    return (
        isinstance(binding, Mapping)
        and set(binding) == _BINDING_FIELDS
        and all(
            binding.get(field) == artifact.get(field)
            for field in (
                "artifact_id",
                "schema_version",
                "material_id",
                "locator",
            )
        )
        and _sha256_hex(binding.get("raw_sha256"))
    )

def _valid_policy_binding(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and set(value) == {"policy_version", "canonical_sha256"}
        and _non_empty_string(value.get("policy_version"))
        and _sha256_hex(value.get("canonical_sha256"))
    )

def _sorted_records(value: list[Any], id_field: str) -> list[Any]:
    copied = deepcopy(value)
    if all(
        isinstance(record, Mapping)
        and _non_empty_string(record.get(id_field))
        for record in copied
    ):
        copied.sort(key=lambda record: record[id_field])
    return copied

def _contains_semantic_authority(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            key in _SEMANTIC_AUTHORITY_FIELDS
            or _contains_semantic_authority(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_semantic_authority(item) for item in value)
    return False

def _sha256_hex(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )
