from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from material_runtime_files import canonical_json_bytes
from task11_handoff_contract import (
    CONTEXT_POLICY_VERSION,
    PACKAGE_SCHEMA_VERSION,
    VALIDATOR_VERSION,
    package_content_sha256,
    package_envelope_sha256,
    record_canonical_sha256,
    seal_handoff_draft,
)
from task11_presemantic_provider import (
    NORMALIZED_SOURCE_SCHEMA_VERSION,
    PACKAGE_INPUT_SCHEMA_VERSION,
    RECORDS_SCHEMA_VERSION,
)


BUILDER_VERSION = "task11-handoff-package/v1"
MAX_CONTEXT_UNITS = 3
MAX_CONTEXT_CODE_POINTS = 1200
MINIMUM_HORIZONTAL_OVERLAP = 0.60
MINIMUM_VERTICAL_GAP_LIMIT = 24.0
FONT_GAP_MULTIPLIER = 2.5

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
_FORBIDDEN_CONTEXT_KINDS = {
    "caption",
    "figure",
    "heading",
    "image",
    "omission",
    "table",
    "unknown",
}
_SENTENCE_TERMINALS = ("。", ".", "！", "!", "？", "?")


def build_task11_handoff_package(
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


def _classify_source_failures(
    source_failures: list[Any],
    candidates: list[Any],
    origins: list[Any],
    material_id: str,
) -> tuple[dict[str, set[str]], list[tuple[str, str]]]:
    candidate_ids = {
        candidate.get("candidate_id")
        for candidate in candidates
        if isinstance(candidate, Mapping)
        and _non_empty_string(candidate.get("candidate_id"))
    }
    candidates_by_unit: dict[str, set[str]] = {}
    candidates_by_block: dict[str, set[str]] = {}
    for origin in origins:
        if not isinstance(origin, Mapping):
            continue
        candidate_id = origin.get("candidate_id")
        unit_id = origin.get("layout_unit_id")
        block_id = origin.get("block_id")
        if candidate_id not in candidate_ids:
            continue
        if _non_empty_string(unit_id):
            candidates_by_unit.setdefault(unit_id, set()).add(
                candidate_id
            )
        if _non_empty_string(block_id):
            candidates_by_block.setdefault(block_id, set()).add(
                candidate_id
            )

    candidate_failures: dict[str, set[str]] = {}
    package_failures: list[tuple[str, str]] = []
    if not candidate_ids:
        package_failures.append(
            ("PKG_CANDIDATES_INVALID", "zero valid literal candidates")
        )
    for failure in source_failures:
        if not _valid_source_failure(failure):
            raise ValueError("invalid package input")
        reasons = failure["source_failure_reasons"]
        reason_text = ";".join(reasons)
        if failure["material_id"] != material_id:
            package_failures.append(
                ("PKG_MATERIAL_INVALID", reason_text)
            )
            continue
        package_scope_code = _package_scope_failure_code(reasons)
        if package_scope_code is not None:
            package_failures.append(
                (package_scope_code, reason_text)
            )
            continue

        unit_candidates = candidates_by_unit.get(
            failure.get("layout_unit_id"),
            set(),
        )
        if unit_candidates:
            for candidate_id in unit_candidates:
                candidate_failures.setdefault(
                    candidate_id,
                    set(),
                ).update(reasons)
            continue

        block_candidates = candidates_by_block.get(
            failure["block_id"],
            set(),
        )
        if (
            failure["source_status"] != "omitted"
            and failure["source_status"] != "selected"
            and block_candidates
        ):
            for candidate_id in block_candidates:
                candidate_failures.setdefault(
                    candidate_id,
                    set(),
                ).update(reasons)
            continue

        if failure["source_status"] in {"omitted", "selected"}:
            continue
        package_failures.append(
            (
                _package_scope_failure_code(reasons)
                or "PKG_CANDIDATES_INVALID",
                reason_text,
            )
        )
    return candidate_failures, sorted(set(package_failures))


def _valid_source_failure(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    reasons = value.get("source_failure_reasons")
    layout_unit_id = value.get("layout_unit_id")
    return (
        _non_empty_string(value.get("source_failure_id"))
        and _non_empty_string(value.get("material_id"))
        and _non_empty_string(value.get("block_id"))
        and (
            layout_unit_id is None
            or _non_empty_string(layout_unit_id)
        )
        and _non_empty_string(value.get("source_ref"))
        and _integer(value.get("pdf_page"))
        and value["pdf_page"] >= 1
        and value.get("source_status")
        in {"failed", "omitted", "selected"}
        and isinstance(reasons, list)
        and bool(reasons)
        and all(_non_empty_string(reason) for reason in reasons)
        and reasons == sorted(set(reasons))
        and isinstance(value.get("locator"), Mapping)
        and bool(value["locator"])
        and isinstance(value.get("provenance"), Mapping)
        and bool(value["provenance"])
        and _valid_input_record_hash(value)
    )


def _package_scope_failure_code(
    reasons: list[str],
) -> str | None:
    if any(
        "artifact" in reason or "binding" in reason
        for reason in reasons
    ):
        return "PKG_NORMALIZED_SOURCE_BINDING_INVALID"
    if any("material" in reason for reason in reasons):
        return "PKG_MATERIAL_INVALID"
    if any(
        reason.startswith("source_mapping_")
        or reason
        in {
            "document_page_count_mismatch",
            "document_unreadable",
        }
        for reason in reasons
    ):
        return "PKG_CANDIDATES_INVALID"
    return None


def _apply_candidate_failures(
    candidates: list[Any],
    failures: Mapping[str, set[str]],
) -> list[Any]:
    output = deepcopy(candidates)
    for candidate in output:
        if not isinstance(candidate, dict):
            continue
        reasons = failures.get(candidate.get("candidate_id"))
        if not reasons:
            continue
        existing = candidate.get("failure_codes")
        existing_codes = (
            existing
            if isinstance(existing, list)
            and all(_non_empty_string(code) for code in existing)
            else []
        )
        candidate["construction_status"] = "invalid"
        candidate["failure_codes"] = sorted(
            {*existing_codes, *reasons}
        )
        candidate["canonical_sha256"] = record_canonical_sha256(
            candidate
        )
    return output


def _package_invalid_records(
    package_id: str,
    failures: list[tuple[str, str]],
) -> list[dict[str, Any]]:
    records = []
    for code, reason in failures:
        identity = {
            "collection": "package",
            "record_id": package_id,
            "failure_codes": [code],
        }
        record = {
            "invalid_record_id": _stable_id(
                "invalid-package-source",
                {**identity, "reason": reason},
            ),
            **identity,
            "reason": reason[:512],
        }
        record["canonical_sha256"] = record_canonical_sha256(record)
        records.append(record)
    records.sort(key=lambda record: record["invalid_record_id"])
    return records


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


def _rebuild_contexts(
    contexts: list[Any],
    origins: list[Any],
    source_units: list[Any],
) -> list[Any]:
    if not all(isinstance(unit, Mapping) for unit in source_units):
        return deepcopy(contexts)
    ordered_units = sorted(
        source_units,
        key=lambda unit: (
            _sort_value(unit.get("material_id")),
            _sort_value(unit.get("pdf_page")),
            _sort_value(unit.get("block_id")),
            _sort_value(unit.get("reading_order")),
            _sort_value(unit.get("layout_unit_id")),
        ),
    )
    unit_indexes = {
        unit["layout_unit_id"]: index
        for index, unit in enumerate(ordered_units)
    }
    output = []
    for raw_context in contexts:
        context = deepcopy(raw_context)
        if not isinstance(context, dict):
            output.append(context)
            continue
        context_id = context.get("context_id")
        primary_ids = context.get("primary_candidate_ids")
        if (
            not _valid_input_record_hash(context)
            or not _non_empty_string(context_id)
            or not isinstance(primary_ids, list)
            or not all(_non_empty_string(value) for value in primary_ids)
        ):
            output.append(context)
            continue
        anchor_ids = {
            origin.get("layout_unit_id")
            for origin in origins
            if isinstance(origin, Mapping)
            and _valid_input_record_hash(origin)
            and origin.get("safe_context_id") == context_id
            and origin.get("candidate_id") in primary_ids
            and _non_empty_string(origin.get("layout_unit_id"))
        }
        if len(anchor_ids) != 1:
            output.append(context)
            continue
        anchor_id = next(iter(anchor_ids))
        anchor_index = unit_indexes.get(anchor_id)
        if anchor_index is None:
            output.append(context)
            continue
        anchor = ordered_units[anchor_index]
        if not _matches_provider_anchor_context(context, anchor):
            output.append(context)
            continue
        selected, previous_reason, next_reason = _bounded_context_units(
            ordered_units,
            anchor_index,
        )
        if not selected:
            output.append(context)
            continue
        texts = [unit.get("text") for unit in selected]
        if not all(isinstance(text, str) and text for text in texts):
            output.append(context)
            continue
        normalized_texts = [
            unit.get("normalized_text", unit["text"])
            for unit in selected
        ]
        if not all(
            isinstance(text, str) and text
            for text in normalized_texts
        ):
            output.append(context)
            continue
        limits = sorted(
            {
                reason
                for reason in (previous_reason, next_reason)
                if reason
                in {
                    "anchor_overflow",
                    "code_point_limit",
                    "unit_limit",
                }
            }
        )
        context.update(
            {
                "text": "\n".join(texts),
                "normalized_text": "\n".join(normalized_texts),
                "layout_unit_refs": [
                    {"layout_unit_id": unit["layout_unit_id"]}
                    for unit in selected
                ],
                "context_scope": CONTEXT_POLICY_VERSION,
                "start_locator": selected[0]["locator"],
                "end_locator": selected[-1]["locator"],
                "boundary_reason": {
                    "previous": previous_reason,
                    "next": next_reason,
                    "limits": limits,
                },
                "code_point_count": len("\n".join(texts)),
            }
        )
        context["canonical_sha256"] = record_canonical_sha256(context)
        output.append(context)
    return output


def _matches_provider_anchor_context(
    context: Mapping[str, Any],
    anchor: Mapping[str, Any],
) -> bool:
    anchor_id = anchor.get("layout_unit_id")
    text = anchor.get("text")
    locator = anchor.get("locator")
    return (
        _non_empty_string(anchor_id)
        and _non_empty_string(text)
        and locator is not None
        and context.get("text") == text
        and context.get("normalized_text") == text
        and context.get("layout_unit_refs")
        == [{"layout_unit_id": anchor_id}]
        and context.get("context_scope") == CONTEXT_POLICY_VERSION
        and context.get("start_locator") == locator
        and context.get("end_locator") == locator
        and context.get("code_point_count") == len(text)
    )


def _bounded_context_units(
    units: list[Mapping[str, Any]],
    anchor_index: int,
) -> tuple[list[Mapping[str, Any]], str, str]:
    anchor = units[anchor_index]
    if (
        anchor.get("unit_kind") != "text"
        or not isinstance(anchor.get("text"), str)
        or not anchor["text"]
    ):
        return [], "unknown_boundary", "unknown_boundary"
    selected = [anchor]
    first_index = anchor_index
    last_index = anchor_index

    if len(anchor["text"]) <= MAX_CONTEXT_CODE_POINTS:
        previous_index = anchor_index - 1
        if previous_index >= 0:
            previous = units[previous_index]
            if (
                _adjacent_boundary(previous, selected[0]) is None
                and _fits_context([previous, *selected])
            ):
                selected.insert(0, previous)
                first_index = previous_index

        next_index = anchor_index + 1
        while next_index < len(units) and len(selected) < MAX_CONTEXT_UNITS:
            candidate = units[next_index]
            if _adjacent_boundary(selected[-1], candidate) is not None:
                break
            if not _fits_context([*selected, candidate]):
                break
            selected.append(candidate)
            last_index = next_index
            next_index += 1

        previous_index = first_index - 1
        while previous_index >= 0 and len(selected) < MAX_CONTEXT_UNITS:
            candidate = units[previous_index]
            if _adjacent_boundary(candidate, selected[0]) is not None:
                break
            if not _fits_context([candidate, *selected]):
                break
            selected.insert(0, candidate)
            first_index = previous_index
            previous_index -= 1

    previous_reason = _edge_reason(
        units,
        first_index,
        first_index - 1,
        before=True,
        selected=selected,
    )
    next_reason = _edge_reason(
        units,
        last_index,
        last_index + 1,
        before=False,
        selected=selected,
    )
    if len(anchor["text"]) > MAX_CONTEXT_CODE_POINTS:
        previous_reason = "anchor_overflow"
        next_reason = "anchor_overflow"
    return selected, previous_reason, next_reason


def _edge_reason(
    units: list[Mapping[str, Any]],
    edge_index: int,
    neighbor_index: int,
    *,
    before: bool,
    selected: list[Mapping[str, Any]],
) -> str:
    if neighbor_index < 0:
        return "material_start"
    if neighbor_index >= len(units):
        return "material_end"
    neighbor = units[neighbor_index]
    edge = units[edge_index]
    reason = (
        _adjacent_boundary(neighbor, edge)
        if before
        else _adjacent_boundary(edge, neighbor)
    )
    if reason is not None:
        return reason
    if len(selected) >= MAX_CONTEXT_UNITS:
        return "unit_limit"
    proposed = (
        [neighbor, *selected]
        if before
        else [*selected, neighbor]
    )
    if not _fits_context(proposed):
        return "code_point_limit"
    return "bounded"


def _adjacent_boundary(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> str | None:
    for field, reason in (
        ("material_id", "material_boundary"),
        ("pdf_page", "page_boundary"),
        ("block_id", "block_boundary"),
    ):
        left_value = left.get(field)
        right_value = right.get(field)
        if left_value is None or right_value is None:
            return "unknown_boundary"
        if left_value != right_value:
            return reason

    left_column = left.get("column_id")
    right_column = right.get("column_id")
    if (
        not _non_empty_string(left_column)
        or not _non_empty_string(right_column)
    ):
        return "unknown_column"
    if left_column != right_column:
        return "column_boundary"
    left_order = left.get("reading_order")
    right_order = right.get("reading_order")
    if (
        not _integer(left_order)
        or not _integer(right_order)
        or right_order != left_order + 1
    ):
        return "non_consecutive_order"
    if (
        left.get("unit_kind") in _FORBIDDEN_CONTEXT_KINDS
        or right.get("unit_kind") in _FORBIDDEN_CONTEXT_KINDS
        or left.get("unit_kind") != "text"
        or right.get("unit_kind") != "text"
    ):
        return "structural_boundary"
    if (
        left.get("heading_transition_after") is True
        or right.get("heading_transition_before") is True
    ):
        return "heading_boundary"
    intervening_kind = left.get("intervening_kind_after")
    if intervening_kind in _FORBIDDEN_CONTEXT_KINDS:
        return "structural_boundary"
    for value in (
        left.get("boundary_after"),
        right.get("boundary_before"),
    ):
        if value not in {None, "none", "safe"}:
            return "unknown_boundary"
    left_text = left.get("text")
    if (
        isinstance(left_text, str)
        and left_text.rstrip().endswith(_SENTENCE_TERMINALS)
    ):
        return "sentence_terminal"

    left_bbox = left.get("bbox")
    right_bbox = right.get("bbox")
    if not _valid_bbox(left_bbox) or not _valid_bbox(right_bbox):
        return "unknown_geometry"
    overlap = max(
        0.0,
        min(left_bbox[2], right_bbox[2])
        - max(left_bbox[0], right_bbox[0]),
    )
    minimum_width = min(
        left_bbox[2] - left_bbox[0],
        right_bbox[2] - right_bbox[0],
    )
    if overlap / minimum_width < MINIMUM_HORIZONTAL_OVERLAP:
        return "horizontal_overlap"
    gap = max(0.0, right_bbox[1] - left_bbox[3])
    font_sizes = [
        value
        for value in (
            left.get("font_size_max"),
            right.get("font_size_max"),
        )
        if _finite_number(value) and value > 0
    ]
    if any(
        value is not None
        and (not _finite_number(value) or value <= 0)
        for value in (
            left.get("font_size_max"),
            right.get("font_size_max"),
        )
    ):
        return "unknown_geometry"
    gap_limit = max(
        FONT_GAP_MULTIPLIER * max(font_sizes, default=0.0),
        MINIMUM_VERTICAL_GAP_LIMIT,
    )
    if gap > gap_limit:
        return "vertical_gap"
    return None


def _fits_context(units: list[Mapping[str, Any]]) -> bool:
    texts = [unit.get("text") for unit in units]
    return (
        len(units) <= MAX_CONTEXT_UNITS
        and all(isinstance(text, str) and text for text in texts)
        and len("\n".join(texts)) <= MAX_CONTEXT_CODE_POINTS
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


def _valid_input_record_hash(record: Mapping[str, Any]) -> bool:
    try:
        return (
            record.get("canonical_sha256")
            == record_canonical_sha256(record)
        )
    except (TypeError, ValueError, RecursionError):
        return False


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


def _stable_id(prefix: str, value: Any) -> str:
    return f"{prefix}:{_canonical_sha256(value)[:24]}"


def _canonical_sha256(value: Any) -> str:
    try:
        return hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    except (TypeError, ValueError, RecursionError):
        raise ValueError("invalid package input") from None


def _sha256_hex(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _valid_bbox(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 4
        and all(_finite_number(coordinate) for coordinate in value)
        and value[2] > value[0]
        and value[3] > value[1]
    )


def _sort_value(value: Any) -> tuple[int, int | str]:
    if isinstance(value, int) and not isinstance(value, bool):
        return (0, value)
    if isinstance(value, str):
        return (1, value)
    return (2, "")
