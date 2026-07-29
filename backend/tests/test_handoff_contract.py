from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from material_runtime_files import canonical_json_bytes
from handoff.contract import (
    FIELD_METADATA,
    FIELD_METADATA_ROWS,
    HandoffDraftUnserializable,
    PACKAGE_SCHEMA_VERSION,
    RECORD_HASH_MISMATCH,
    UNKNOWN_FIELD_CODES,
    is_handoff_consumer_eligible_package,
    package_content_sha256,
    package_envelope_sha256,
    record_canonical_sha256,
    seal_handoff_draft,
)


COLLECTION_PATHS = {
    "candidate": "candidates",
    "origin": "origins",
    "context": "contexts",
    "evidence": "evidence_records",
    "projection": "projection_records",
    "build_attestation": "build_attestations",
    "invalid_record": "invalid_records",
}


def _text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _artifact_binding(
    artifact_id: str,
    raw_sha256: str,
) -> dict:
    return {
        "artifact_id": artifact_id,
        "schema_version": f"{artifact_id}/v1",
        "material_id": "material-synthetic",
        "raw_sha256": raw_sha256,
        "locator": f"synthetic://{artifact_id}",
    }


def synthetic_normalized_source() -> dict:
    units = [
        {
            "layout_unit_id": "unit-001",
            "material_id": "material-synthetic",
            "block_id": "block-001",
            "source_ref": "synthetic://source#block-001",
            "pdf_page": 1,
            "column_id": "column-a",
            "reading_order": 0,
            "bbox": [0.0, 0.0, 100.0, 10.0],
            "font_size_max": 10.0,
            "unit_kind": "text",
            "text": "Intro phrase",
            "normalized_text": "Intro phrase",
            "locator": "synthetic://source#unit-001",
        },
        {
            "layout_unit_id": "unit-002",
            "material_id": "material-synthetic",
            "block_id": "block-002",
            "source_ref": "synthetic://source#block-002",
            "pdf_page": 1,
            "column_id": "column-a",
            "reading_order": 1,
            "bbox": [0.0, 12.0, 100.0, 22.0],
            "font_size_max": 10.0,
            "unit_kind": "text",
            "text": "Alpha concept definition",
            "normalized_text": "Alpha concept definition",
            "locator": "synthetic://source#unit-002",
        },
        {
            "layout_unit_id": "unit-003",
            "material_id": "material-synthetic",
            "block_id": "block-003",
            "source_ref": "synthetic://source#block-003",
            "pdf_page": 1,
            "column_id": "column-a",
            "reading_order": 2,
            "bbox": [0.0, 24.0, 100.0, 34.0],
            "font_size_max": 10.0,
            "unit_kind": "text",
            "text": "Additional detail",
            "normalized_text": "Additional detail",
            "locator": "synthetic://source#unit-003",
        },
    ]
    return {
        "artifact_id": "normalized-source",
        "schema_version": "normalized-source/v1",
        "material_id": "material-synthetic",
        "raw_sha256": "a" * 64,
        "locator": "synthetic://normalized-source",
        "layout_units": units,
    }


def synthetic_draft() -> dict:
    source = synthetic_normalized_source()
    normalized_binding = {
        key: source[key]
        for key in (
            "artifact_id",
            "schema_version",
            "material_id",
            "raw_sha256",
            "locator",
        )
    }
    candidate_binding = _artifact_binding("candidate-source", "b" * 64)
    context_text = "\n".join(
        unit["text"]
        for unit in source["layout_units"]
    )
    candidate = {
        "candidate_id": "candidate-001",
        "material_id": "material-synthetic",
        "surface": "Alpha concept",
        "normalized_surface": "Alpha concept",
        "generator_kinds": ["lexical"],
        "origin_ids": ["origin-001"],
        "context_ids": ["context-001"],
        "evidence_refs": ["evidence-001"],
        "projection_ids": [],
        "support": {
            "flags": {"literal": True},
            "origin_count": 1,
            "context_count": 1,
            "hard_negative_gate": False,
        },
        "construction_status": "valid",
        "failure_codes": [],
    }
    origin = {
        "origin_id": "origin-001",
        "candidate_id": "candidate-001",
        "material_id": "material-synthetic",
        "block_id": "block-002",
        "layout_unit_id": "unit-002",
        "source_ref": "synthetic://source#block-002",
        "pdf_page": 1,
        "reading_order": 1,
        "bbox": [0.0, 12.0, 100.0, 22.0],
        "literal_span": {"start": 0, "end": 13},
        "safe_context_id": "context-001",
        "layout_unit_text_sha256": _text_sha256("Alpha concept definition"),
    }
    context = {
        "context_id": "context-001",
        "material_id": "material-synthetic",
        "text": context_text,
        "normalized_text": context_text,
        "layout_unit_refs": [
            {"layout_unit_id": "unit-001"},
            {"layout_unit_id": "unit-002"},
            {"layout_unit_id": "unit-003"},
        ],
        "primary_candidate_ids": ["candidate-001"],
        "context_scope": "same-material-local-layout-v1",
        "start_locator": "synthetic://source#unit-001",
        "end_locator": "synthetic://source#unit-003",
        "boundary_reason": {
            "previous": "material_start",
            "next": "material_end",
            "limits": [],
        },
        "evidence_refs": ["evidence-001"],
        "code_point_count": len(context_text),
    }
    evidence = {
        "evidence_id": "evidence-001",
        "material_id": "material-synthetic",
        "evidence_kind": "candidate_literal",
        "statement": "Alpha concept definition",
        "normalized_statement": "Alpha concept definition",
        "literal_surface": "Alpha concept",
        "literal_span": {"start": 0, "end": 13},
        "candidate_ids": ["candidate-001"],
        "context_ids": ["context-001"],
        "origin_ids": ["origin-001"],
    }
    attestation = {
        "attestation_id": "attestation-001",
        "package_id": "package-synthetic",
        "builder_component": "synthetic-fixture",
        "builder_version": "v1",
        "input_bindings": sorted(
            [normalized_binding, candidate_binding],
            key=lambda binding: (
                binding["artifact_id"],
                binding["schema_version"],
                binding["raw_sha256"],
            ),
        ),
        "replay_count": 0,
        "replay_content_sha256s": [],
        "deterministic_replay_pass": False,
        "record_counts": {
            "candidates": 1,
            "origins": 1,
            "contexts": 1,
            "evidence_records": 1,
            "projection_records": 0,
        },
    }
    draft = {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "package_id": "package-synthetic",
        "material_id": "material-synthetic",
        "status": "built",
        "normalized_source_binding": normalized_binding,
        "candidate_source_binding": candidate_binding,
        "context_policy_binding": {
            "policy_version": "same-material-local-layout-v1",
            "canonical_sha256": "c" * 64,
        },
        "projection_policy_binding": {
            "policy_version": "literal-projection/v1",
            "canonical_sha256": "d" * 64,
        },
        "candidates": [candidate],
        "origins": [origin],
        "contexts": [context],
        "evidence_records": [evidence],
        "projection_records": [],
        "build_attestations": [attestation],
        "invalid_records": [],
        "content_sha256": "",
        "validation_summary": {},
        "canonical_sha256": "",
    }
    return _stamp_draft(draft)


def _stamp_draft(
    draft: dict,
    *,
    skip_record_hash: tuple[str, int] | None = None,
    skip_content_hash: bool = False,
    skip_envelope_hash: bool = False,
    preserve_summary_fields: set[str] | None = None,
) -> dict:
    preserve_summary_fields = preserve_summary_fields or set()
    for collection, package_key in COLLECTION_PATHS.items():
        records = draft.get(package_key)
        if not isinstance(records, list):
            continue
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                continue
            if skip_record_hash == (collection, index):
                continue
            record["canonical_sha256"] = record_canonical_sha256(record)

    if not skip_content_hash:
        draft["content_sha256"] = package_content_sha256(draft)
    summary = draft.get("validation_summary")
    if isinstance(summary, dict):
        invalid_records = draft.get("invalid_records", [])
        counts: Counter[str] = Counter()
        if isinstance(invalid_records, list):
            for record in invalid_records:
                if isinstance(record, dict) and isinstance(record.get("failure_codes"), list):
                    counts.update(record["failure_codes"])
        values = {
            "validation_run_id": "synthetic-draft-validation",
            "validator_version": "synthetic-draft/v1",
            "validated_content_sha256": draft.get("content_sha256"),
            "status": "FAIL" if invalid_records else "PASS",
            "failure_count": len(invalid_records),
            "failure_code_counts": dict(sorted(counts.items())),
        }
        for field, value in values.items():
            if field not in preserve_summary_fields:
                summary[field] = value
    if not skip_envelope_hash:
        draft["canonical_sha256"] = package_envelope_sha256(draft)
    return draft


def _draft_with_existing_invalid_record() -> dict:
    draft = synthetic_draft()
    record = {
        "invalid_record_id": "invalid-existing",
        "collection": "candidates",
        "record_id": "candidate-001",
        "failure_codes": ["SYNTHETIC_EXISTING_FAILURE"],
        "reason": "synthetic existing failure",
    }
    draft["invalid_records"] = [record]
    return _stamp_draft(draft)


def _add_projection(draft: dict) -> None:
    draft["projection_records"] = [
        {
            "projection_id": "projection-001",
            "material_id": "material-synthetic",
            "projection_kind": "longer_literal_substring",
            "source_candidate_ids": ["candidate-001"],
            "source_context_ids": ["context-001"],
            "source_evidence_refs": ["evidence-001"],
            "projected_surface": "Alpha concept",
            "normalized_projected_surface": "Alpha concept",
            "literal_span": {"start": 0, "end": 13},
            "algorithm_version": "synthetic/v1",
        }
    ]
    draft["candidates"][0]["projection_ids"] = ["projection-001"]
    draft["build_attestations"][0]["record_counts"]["projection_records"] = 1
    _stamp_draft(draft)


def _record_for_collection(draft: dict, collection: str) -> dict:
    if collection == "package":
        return draft
    if collection == "validation_summary":
        return draft["validation_summary"]
    if collection == "invalid_record" and not draft["invalid_records"]:
        draft.update(_draft_with_existing_invalid_record())
    if collection == "invalid_record":
        return next(
            record
            for record in draft["invalid_records"]
            if record.get("invalid_record_id") == "invalid-existing"
        )
    if collection == "projection" and not draft["projection_records"]:
        _add_projection(draft)
    return draft[COLLECTION_PATHS[collection]][0]


def _all_failure_codes(package: dict) -> list[str]:
    return [
        code
        for record in package["invalid_records"]
        if isinstance(record.get("failure_codes"), list)
        for code in record["failure_codes"]
    ]


def _restamp_field_mutation(
    draft: dict,
    collection: str,
    field: str,
) -> None:
    skip_record_hash = None
    if field == "canonical_sha256" and collection in COLLECTION_PATHS:
        skip_record_hash = (collection, 0)
    skip_content_hash = collection == "package" and field == "content_sha256"
    skip_envelope_hash = collection == "package" and field == "canonical_sha256"
    preserve_summary_fields = (
        {field}
        if collection == "validation_summary"
        else set()
    )
    _stamp_draft(
        draft,
        skip_record_hash=skip_record_hash,
        skip_content_hash=skip_content_hash,
        skip_envelope_hash=skip_envelope_hash,
        preserve_summary_fields=preserve_summary_fields,
    )


def test_valid_synthetic_draft_seals_pass_without_mutation() -> None:
    draft = synthetic_draft()
    original = copy.deepcopy(draft)

    sealed = seal_handoff_draft(
        draft,
        normalized_source=synthetic_normalized_source(),
    )

    assert draft == original
    assert sealed["status"] == "PASS"
    assert sealed["invalid_records"] == []
    assert sealed["content_sha256"] == package_content_sha256(sealed)
    assert sealed["canonical_sha256"] == package_envelope_sha256(sealed)
    assert sealed["validation_summary"] == {
        "validation_run_id": sealed["validation_summary"]["validation_run_id"],
        "validator_version": "task11-handoff-contract/v1",
        "validated_content_sha256": sealed["content_sha256"],
        "status": "PASS",
        "failure_count": 0,
        "failure_code_counts": {},
    }
    assert is_handoff_consumer_eligible_package(
        sealed,
        normalized_source=synthetic_normalized_source(),
    )
    assert sealed["build_attestations"][0]["replay_count"] == 0
    assert sealed["build_attestations"][0]["replay_content_sha256s"] == []
    assert sealed["build_attestations"][0]["deterministic_replay_pass"] is False


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_draft_is_preseal_unserializable(value: float) -> None:
    draft = synthetic_draft()
    draft["unexpected_number"] = value

    with pytest.raises(HandoffDraftUnserializable, match="PKG_DRAFT_UNSERIALIZABLE"):
        seal_handoff_draft(
            draft,
            normalized_source=synthetic_normalized_source(),
        )


def test_cyclic_draft_is_preseal_unserializable() -> None:
    draft = synthetic_draft()
    draft["cycle"] = draft

    with pytest.raises(HandoffDraftUnserializable, match="PKG_DRAFT_UNSERIALIZABLE"):
        seal_handoff_draft(
            draft,
            normalized_source=synthetic_normalized_source(),
        )


def test_representable_invalid_draft_is_sha_bound_fail_and_preserves_data() -> None:
    draft = synthetic_draft()
    draft["candidates"][0]["surface"] = None
    _stamp_draft(draft)
    original_candidate = copy.deepcopy(draft["candidates"][0])

    sealed = seal_handoff_draft(
        draft,
        normalized_source=synthetic_normalized_source(),
    )

    assert sealed["status"] == "FAIL"
    assert len(sealed["candidates"]) == 1
    assert sealed["candidates"][0]["candidate_id"] == "candidate-001"
    assert {
        key: value
        for key, value in sealed["candidates"][0].items()
        if key != "canonical_sha256"
    } == {
        key: value
        for key, value in original_candidate.items()
        if key != "canonical_sha256"
    }
    assert "CANDIDATE_SURFACE_INVALID" in _all_failure_codes(sealed)
    assert sealed["candidates"][0]["canonical_sha256"] == record_canonical_sha256(
        sealed["candidates"][0]
    )
    expected_counts = Counter(_all_failure_codes(sealed))
    assert sealed["validation_summary"]["failure_count"] == len(
        sealed["invalid_records"]
    )
    assert sealed["validation_summary"]["failure_code_counts"] == dict(
        sorted(expected_counts.items())
    )
    assert sealed["canonical_sha256"] == package_envelope_sha256(sealed)
    assert not is_handoff_consumer_eligible_package(
        sealed,
        normalized_source=synthetic_normalized_source(),
    )


def test_negative_zero_is_representable_and_seals_exact_field_failure() -> None:
    draft = synthetic_draft()
    draft["origins"][0]["bbox"][0] = -0.0
    _stamp_draft(draft)

    sealed = seal_handoff_draft(
        draft,
        normalized_source=synthetic_normalized_source(),
    )

    assert sealed["status"] == "FAIL"
    assert "ORIGIN_BBOX_INVALID" in _all_failure_codes(sealed)
    assert sealed["canonical_sha256"] == package_envelope_sha256(sealed)


def test_empty_candidate_collection_cannot_seal_pass() -> None:
    draft = synthetic_draft()
    draft["candidates"] = []
    draft["build_attestations"][0]["record_counts"]["candidates"] = 0
    _stamp_draft(draft)

    sealed = seal_handoff_draft(
        draft,
        normalized_source=synthetic_normalized_source(),
    )

    assert sealed["status"] == "FAIL"
    assert "PKG_CANDIDATES_INVALID" in _all_failure_codes(sealed)


def test_invalid_candidate_promotes_existing_failure_codes_exactly() -> None:
    draft = synthetic_draft()
    candidate = draft["candidates"][0]
    candidate["construction_status"] = "invalid"
    candidate["failure_codes"] = [
        "LITERAL_SURFACE_NOT_FOUND",
        "XREF_DANGLING",
    ]
    _stamp_draft(draft)

    sealed = seal_handoff_draft(
        draft,
        normalized_source=synthetic_normalized_source(),
    )

    assert sealed["status"] == "FAIL"
    assert set(_all_failure_codes(sealed)) == {
        "LITERAL_SURFACE_NOT_FOUND",
        "XREF_DANGLING",
    }
    assert "CANDIDATE_CONSTRUCTION_STATUS_INVALID" not in _all_failure_codes(sealed)
    assert "CANDIDATE_FAILURE_CODES_INVALID" not in _all_failure_codes(sealed)
    assert sealed["candidates"][0]["construction_status"] == "invalid"
    assert sealed["candidates"][0]["failure_codes"] == [
        "LITERAL_SURFACE_NOT_FOUND",
        "XREF_DANGLING",
    ]


def test_valid_candidate_with_failure_codes_is_field_invalid() -> None:
    draft = synthetic_draft()
    candidate = draft["candidates"][0]
    candidate["failure_codes"] = ["SYNTHETIC_CONSTRUCTION_FAILURE"]
    _stamp_draft(draft)

    sealed = seal_handoff_draft(
        draft,
        normalized_source=synthetic_normalized_source(),
    )

    assert sealed["status"] == "FAIL"
    assert set(_all_failure_codes(sealed)) == {
        "CANDIDATE_FAILURE_CODES_INVALID",
    }


def test_unreferenced_origin_cannot_exist_in_pass_package() -> None:
    draft = synthetic_draft()
    extra_origin = copy.deepcopy(draft["origins"][0])
    extra_origin["origin_id"] = "origin-002"
    draft["origins"].append(extra_origin)
    draft["build_attestations"][0]["record_counts"]["origins"] = 2
    _stamp_draft(draft)

    sealed = seal_handoff_draft(
        draft,
        normalized_source=synthetic_normalized_source(),
    )

    assert sealed["status"] == "FAIL"
    assert "PKG_ORIGINS_INVALID" in _all_failure_codes(sealed)
    assert [origin["origin_id"] for origin in sealed["origins"]] == [
        "origin-001",
        "origin-002",
    ]


def test_field_metadata_is_immutable_and_has_exact_101_rows() -> None:
    assert len(FIELD_METADATA_ROWS) == 101
    assert {
        collection: len(fields)
        for collection, fields in FIELD_METADATA.items()
    } == {
        "package": 18,
        "candidate": 13,
        "origin": 13,
        "context": 13,
        "evidence": 11,
        "projection": 11,
        "build_attestation": 10,
        "invalid_record": 6,
        "validation_summary": 6,
    }
    assert len(
        {
            (row["collection"], row["path"])
            for row in FIELD_METADATA_ROWS
        }
    ) == 101
    with pytest.raises(TypeError):
        FIELD_METADATA["package"]["package_id"] = {}


def test_replay_metadata_requires_production_single_build_shape() -> None:
    replay_count = FIELD_METADATA["build_attestation"]["replay_count"]
    replay_hashes = FIELD_METADATA["build_attestation"][
        "replay_content_sha256s"
    ]
    replay_pass = FIELD_METADATA["build_attestation"][
        "deterministic_replay_pass"
    ]

    assert replay_count["type"] == "const 0"
    assert replay_hashes["type"] == "const []"
    assert replay_pass["type"] == "const false"
    assert "package-external" in replay_count["invariant"]
    assert "package-external" in replay_hashes["invariant"]
    assert "package-external" in replay_pass["invariant"]


@pytest.mark.parametrize(
    ("collection", "field", "required", "expected_code"),
    [
        (
            row["collection"],
            row["path"],
            row["required"],
            row["validation_failure_code"],
        )
        for row in FIELD_METADATA_ROWS
    ],
)
def test_each_known_field_emits_its_exact_metadata_code(
    collection: str,
    field: str,
    required: bool,
    expected_code: str,
) -> None:
    draft = (
        _draft_with_existing_invalid_record()
        if collection == "invalid_record"
        else synthetic_draft()
    )
    record = _record_for_collection(draft, collection)
    if required:
        record.pop(field)
    else:
        record[field] = 0
    _restamp_field_mutation(draft, collection, field)

    sealed = seal_handoff_draft(
        draft,
        normalized_source=synthetic_normalized_source(),
    )
    codes = _all_failure_codes(sealed)

    assert expected_code in codes
    assert RECORD_HASH_MISMATCH not in codes


@pytest.mark.parametrize(
    ("collection", "expected_code"),
    list(UNKNOWN_FIELD_CODES.items()),
)
def test_closed_key_unknown_field_is_preserved_and_exact(
    collection: str,
    expected_code: str,
) -> None:
    draft = (
        _draft_with_existing_invalid_record()
        if collection == "invalid_record"
        else synthetic_draft()
    )
    record = _record_for_collection(draft, collection)
    record["unexpected_synthetic_field"] = {"kept": True}
    _restamp_field_mutation(draft, collection, "unexpected_synthetic_field")

    sealed = seal_handoff_draft(
        draft,
        normalized_source=synthetic_normalized_source(),
    )
    sealed_record = _record_for_collection(sealed, collection)

    assert sealed["status"] == "FAIL"
    assert sealed_record["unexpected_synthetic_field"] == {"kept": True}
    assert expected_code in _all_failure_codes(sealed)


@pytest.mark.parametrize(
    ("collection", "expected_code"),
    [
        ("candidate", "CANDIDATE_HASH_MISMATCH"),
        ("origin", "ORIGIN_HASH_MISMATCH"),
        ("context", "CONTEXT_HASH_MISMATCH"),
        ("evidence", "EVIDENCE_HASH_MISMATCH"),
        ("projection", "PROJECTION_HASH_MISMATCH"),
        ("build_attestation", "BUILD_ATTESTATION_HASH_MISMATCH"),
        ("invalid_record", "INVALID_RECORD_HASH_MISMATCH"),
    ],
)
def test_record_hash_drift_has_only_collection_specific_hash_code(
    collection: str,
    expected_code: str,
) -> None:
    draft = (
        _draft_with_existing_invalid_record()
        if collection == "invalid_record"
        else synthetic_draft()
    )
    if collection == "projection":
        _add_projection(draft)
        _stamp_draft(draft)
    record = draft[COLLECTION_PATHS[collection]][0]
    record["canonical_sha256"] = "0" * 64
    _stamp_draft(draft, skip_record_hash=(collection, 0))

    sealed = seal_handoff_draft(
        draft,
        normalized_source=synthetic_normalized_source(),
    )
    codes = _all_failure_codes(sealed)

    assert expected_code in codes
    assert RECORD_HASH_MISMATCH not in codes
    hash_codes = {
        "CANDIDATE_HASH_MISMATCH",
        "ORIGIN_HASH_MISMATCH",
        "CONTEXT_HASH_MISMATCH",
        "EVIDENCE_HASH_MISMATCH",
        "PROJECTION_HASH_MISMATCH",
        "BUILD_ATTESTATION_HASH_MISMATCH",
        "INVALID_RECORD_HASH_MISMATCH",
    }
    assert set(codes) & hash_codes == {expected_code}


def test_package_content_and_envelope_hash_codes_are_not_aliased() -> None:
    content_draft = synthetic_draft()
    content_draft["content_sha256"] = "0" * 64
    content_draft["validation_summary"]["validated_content_sha256"] = "0" * 64
    content_draft["canonical_sha256"] = package_envelope_sha256(content_draft)
    content = seal_handoff_draft(
        content_draft,
        normalized_source=synthetic_normalized_source(),
    )

    envelope_draft = synthetic_draft()
    envelope_draft["canonical_sha256"] = "0" * 64
    envelope = seal_handoff_draft(
        envelope_draft,
        normalized_source=synthetic_normalized_source(),
    )

    assert "PKG_CONTENT_HASH_MISMATCH" in _all_failure_codes(content)
    assert "PKG_ENVELOPE_HASH_MISMATCH" not in _all_failure_codes(content)
    assert "PKG_ENVELOPE_HASH_MISMATCH" in _all_failure_codes(envelope)
    assert "PKG_CONTENT_HASH_MISMATCH" not in _all_failure_codes(envelope)


def test_cross_reference_and_material_failures_are_retained() -> None:
    draft = synthetic_draft()
    draft["candidates"][0]["origin_ids"] = ["origin-missing"]
    draft["origins"][0]["material_id"] = "material-other"
    _stamp_draft(draft)

    sealed = seal_handoff_draft(
        draft,
        normalized_source=synthetic_normalized_source(),
    )
    codes = set(_all_failure_codes(sealed))

    assert "XREF_CANDIDATE_ORIGIN_DANGLING" in codes
    assert "ORIGIN_MATERIAL_MISMATCH" in codes
    assert "XREF_ORIGIN_CANDIDATE_CROSS_MATERIAL" in codes
    assert sealed["candidates"][0]["origin_ids"] == ["origin-missing"]
    assert sealed["origins"][0]["material_id"] == "material-other"


def test_literal_locator_and_context_boundaries_use_only_bound_source() -> None:
    draft = synthetic_draft()
    source = synthetic_normalized_source()
    source["layout_units"][2]["reading_order"] = 4
    source["layout_units"][2]["pdf_page"] = 2
    source["layout_units"][2]["bbox"] = [200.0, 80.0, 300.0, 90.0]
    draft["origins"][0]["literal_span"] = {"start": 0, "end": 5}
    draft["origins"][0]["source_ref"] = "synthetic://source#wrong-block"
    draft["evidence_records"][0]["literal_span"] = {"start": 1, "end": 5}
    _stamp_draft(draft)

    sealed = seal_handoff_draft(draft, normalized_source=source)
    codes = set(_all_failure_codes(sealed))

    assert "ORIGIN_LITERAL_SPAN_INVALID" in codes
    assert "ORIGIN_SOURCE_REF_INVALID" in codes
    assert "EVIDENCE_LITERAL_SPAN_INVALID" in codes
    assert "CONTEXT_NON_CONSECUTIVE_ORDER" in codes
    assert "CONTEXT_CROSS_PAGE" in codes
    assert "CONTEXT_HORIZONTAL_OVERLAP_LOW" in codes
    assert "CONTEXT_VERTICAL_GAP_HIGH" in codes


def test_anchor_overflow_fails_closed_without_truncating_literal_or_text() -> None:
    draft = synthetic_draft()
    source = synthetic_normalized_source()
    anchor_text = "Alpha concept" + ("x" * 1200)
    source["layout_units"][1]["text"] = anchor_text
    source["layout_units"][1]["normalized_text"] = anchor_text
    draft["origins"][0]["layout_unit_text_sha256"] = _text_sha256(anchor_text)
    context = draft["contexts"][0]
    context["text"] = anchor_text
    context["normalized_text"] = anchor_text
    context["layout_unit_refs"] = [{"layout_unit_id": "unit-002"}]
    context["start_locator"] = "synthetic://source#unit-002"
    context["end_locator"] = "synthetic://source#unit-002"
    context["code_point_count"] = len(anchor_text)
    _stamp_draft(draft)

    sealed = seal_handoff_draft(draft, normalized_source=source)

    assert "CONTEXT_ANCHOR_OVERFLOW" in _all_failure_codes(sealed)
    assert sealed["contexts"][0]["text"] == anchor_text
    assert sealed["candidates"][0]["surface"] == "Alpha concept"


def test_omitting_normalized_source_is_rejected_by_signature() -> None:
    with pytest.raises(TypeError, match="normalized_source"):
        seal_handoff_draft(synthetic_draft())


@pytest.mark.parametrize("source", [None, []])
def test_none_or_nonmapping_normalized_source_cannot_pass(
    source: object,
) -> None:
    sealed = seal_handoff_draft(
        synthetic_draft(),
        normalized_source=source,
    )

    assert sealed["status"] == "FAIL"
    assert "PKG_NORMALIZED_SOURCE_BINDING_INVALID" in _all_failure_codes(sealed)
    assert not is_handoff_consumer_eligible_package(
        sealed,
        normalized_source=source,
    )


def test_unresolved_required_source_unit_cannot_pass() -> None:
    source = synthetic_normalized_source()
    source["layout_units"] = [
        unit
        for unit in source["layout_units"]
        if unit["layout_unit_id"] != "unit-002"
    ]

    sealed = seal_handoff_draft(
        synthetic_draft(),
        normalized_source=source,
    )
    codes = set(_all_failure_codes(sealed))

    assert sealed["status"] == "FAIL"
    assert "ORIGIN_LAYOUT_UNIT_REF_INVALID" in codes
    assert "CONTEXT_LAYOUT_REFS_INVALID" in codes


@pytest.mark.parametrize(
    ("binding_field", "invalid_value"),
    [
        ("artifact_id", "other-normalized-source"),
        ("raw_sha256", "f" * 64),
        ("material_id", "material-other"),
    ],
)
def test_unbound_normalized_source_is_not_used_for_locator_validation(
    binding_field: str,
    invalid_value: str,
) -> None:
    draft = synthetic_draft()
    source = synthetic_normalized_source()
    source[binding_field] = invalid_value
    source["layout_units"][1]["text"] = "unbound different text"

    sealed = seal_handoff_draft(draft, normalized_source=source)
    codes = set(_all_failure_codes(sealed))

    assert "PKG_NORMALIZED_SOURCE_BINDING_INVALID" in codes
    assert "ORIGIN_LITERAL_SPAN_INVALID" not in codes
    assert "ORIGIN_TEXT_HASH_MISMATCH" not in codes


def test_runtime_evaluation_field_is_fail_closed_even_when_nested() -> None:
    draft = synthetic_draft()
    draft["candidates"][0]["support"]["flags"]["gold_name"] = "forbidden"
    _stamp_draft(draft)

    sealed = seal_handoff_draft(
        draft,
        normalized_source=synthetic_normalized_source(),
    )
    codes = set(_all_failure_codes(sealed))

    assert sealed["status"] == "FAIL"
    assert "CANDIDATE_SUPPORT_INVALID" in codes
    assert "PKG_FIELD_INVALID" in codes


@pytest.mark.parametrize(
    ("field", "value", "expected_code"),
    [
        ("replay_count", 3, "BUILD_REPLAY_COUNT_INVALID"),
        ("replay_content_sha256s", ["a" * 64] * 3, "BUILD_REPLAY_HASH_DRIFT"),
        ("deterministic_replay_pass", True, "BUILD_REPLAY_FAILED"),
    ],
)
def test_runtime_attestation_rejects_embedded_replay_results(
    field: str,
    value: object,
    expected_code: str,
) -> None:
    draft = synthetic_draft()
    draft["build_attestations"][0][field] = value
    _stamp_draft(draft)

    sealed = seal_handoff_draft(
        draft,
        normalized_source=synthetic_normalized_source(),
    )

    assert expected_code in _all_failure_codes(sealed)


def _seal_in_isolated_process(payload: dict) -> bytes:
    command = [
        sys.executable,
        "-c",
        (
            "import json,sys;"
            "from material_runtime_files import canonical_json_bytes;"
            "from handoff.contract import seal_handoff_draft;"
            "p=json.load(sys.stdin);"
            "sys.stdout.buffer.write(canonical_json_bytes("
            "seal_handoff_draft(p['draft'],normalized_source=p['source'])))"
        ),
    ]
    result = subprocess.run(
        command,
        cwd=Path(__file__).parents[1],
        input=canonical_json_bytes(payload),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return result.stdout


def test_three_isolated_processes_produce_identical_external_replay_evidence() -> None:
    payload = {
        "draft": synthetic_draft(),
        "source": synthetic_normalized_source(),
    }

    outputs = [_seal_in_isolated_process(payload) for _ in range(3)]
    hashes = [hashlib.sha256(output).hexdigest() for output in outputs]
    packages = [json.loads(output) for output in outputs]

    assert len(set(outputs)) == 1
    assert len(set(hashes)) == 1
    assert len({package["content_sha256"] for package in packages}) == 1
    assert len({package["canonical_sha256"] for package in packages}) == 1
    assert all(
        package["build_attestations"][0]["replay_count"] == 0
        for package in packages
    )

    changed = copy.deepcopy(payload)
    changed["draft"]["candidates"][0]["surface"] = "Different literal"
    _stamp_draft(changed["draft"])
    assert _seal_in_isolated_process(changed) != outputs[0]
