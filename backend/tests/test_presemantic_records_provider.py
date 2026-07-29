from __future__ import annotations

import copy
import hashlib

import pytest

from material_runtime_files import canonical_json_bytes
from handoff.contract import (
    is_handoff_consumer_eligible_package,
    seal_handoff_draft,
)
from handoff.contract_hashing import (
    package_content_sha256,
    package_envelope_sha256,
    record_canonical_sha256,
)
from handoff.contract_schema_metadata import PACKAGE_SCHEMA_VERSION
from presemantic_records_provider import (
    PACKAGE_INPUT_SCHEMA_VERSION,
    RECORDS_SCHEMA_VERSION,
    build_presemantic_records_package_input,
)


MATERIAL_ID = "material-synthetic"


def _text_unit(
    unit_id: str = "unit-001",
    *,
    reading_order: int = 0,
    text: str = "Alpha concept definition",
) -> dict:
    return {
        "layout_unit_id": unit_id,
        "parent_block_id": "block-001",
        "reading_order": reading_order,
        "bbox": [0.0, float(reading_order * 12), 100.0, float(reading_order * 12 + 10)],
        "kind": "text",
        "text": text,
        "style_summary": {
            "bold": False,
            "font_names": ["Synthetic"],
            "font_size_max": 10.0,
            "font_size_min": 10.0,
            "line_count": 1,
            "monospace": False,
        },
    }


def _image_unit() -> dict:
    return {
        "layout_unit_id": "unit-image",
        "parent_block_id": "block-001",
        "reading_order": 1,
        "bbox": [0.0, 12.0, 100.0, 22.0],
        "kind": "image",
    }


def _normalized(*units: dict) -> dict:
    return {
        "schema_version": "normalized-material-blocks/v2",
        "status": "success",
        "source_provenance": {},
        "materials": [
            {
                "material_id": MATERIAL_ID,
                "case_id": "case-synthetic",
                "artifact_ref": "synthetic:material",
                "blocks": [
                    {
                        "material_id": MATERIAL_ID,
                        "case_id": "case-synthetic",
                        "artifact_ref": "synthetic:material",
                        "block_id": "block-001",
                        "pdf_page": 1,
                        "source_ref": "synthetic://source#page=1",
                        "locator": {
                            "pdf_page": 1,
                            "source_ref": "synthetic://source#page=1",
                        },
                        "page_bbox": [0.0, 0.0, 200.0, 200.0],
                        "provenance": {"native_analysis": {"policy": "synthetic-v2"}},
                        "native_analysis_status": "success",
                        "selection_status": "selected",
                        "reasons": [],
                        "warnings": [],
                        "layout_units": list(units) or [_text_unit()],
                        "layout_unit_omissions": [],
                    }
                ],
            }
        ],
    }


def _stamp_draft(draft: dict) -> dict:
    """替 provider compatibility draft 補齊各層雜湊與 PASS validation summary。"""
    for key in (
        "candidates",
        "origins",
        "contexts",
        "evidence_records",
        "projection_records",
        "build_attestations",
        "invalid_records",
    ):
        for record in draft[key]:
            record["canonical_sha256"] = record_canonical_sha256(record)
    draft["content_sha256"] = package_content_sha256(draft)
    draft["validation_summary"] = {
        "validation_run_id": "provider-compatibility",
        "validator_version": "provider-compatibility/v1",
        "validated_content_sha256": draft["content_sha256"],
        "status": "PASS",
        "failure_count": 0,
        "failure_code_counts": {},
    }
    draft["canonical_sha256"] = package_envelope_sha256(draft)
    return draft


def _handoff_draft(output: dict) -> dict:
    """把 provider output 組成可 sealing 的 handoff draft，保留 bindings 與 records。"""
    records = copy.deepcopy(output["records_artifact"])
    attestation = {
        "attestation_id": "attestation-provider",
        "package_id": "package-provider",
        "builder_component": "presemantic-records-provider-compatibility",
        "builder_version": "v1",
        "input_bindings": sorted(
            [
                copy.deepcopy(output["normalized_source_binding"]),
                copy.deepcopy(output["deterministic_records_binding"]),
            ],
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
            "candidates": len(records["candidates"]),
            "origins": len(records["origins"]),
            "contexts": len(records["contexts"]),
            "evidence_records": len(records["evidence_records"]),
            "projection_records": 0,
        },
    }
    return _stamp_draft(
        {
            "schema_version": PACKAGE_SCHEMA_VERSION,
            "package_id": "package-provider",
            "material_id": MATERIAL_ID,
            "status": "built",
            "normalized_source_binding": copy.deepcopy(
                output["normalized_source_binding"]
            ),
            "candidate_source_binding": copy.deepcopy(
                output["deterministic_records_binding"]
            ),
            "context_policy_binding": copy.deepcopy(
                output["context_policy_binding"]
            ),
            "projection_policy_binding": copy.deepcopy(
                output["projection_policy_binding"]
            ),
            "candidates": records["candidates"],
            "origins": records["origins"],
            "contexts": records["contexts"],
            "evidence_records": records["evidence_records"],
            "projection_records": [],
            "build_attestations": [attestation],
            "invalid_records": [],
            "content_sha256": "",
            "validation_summary": {},
            "canonical_sha256": "",
        }
    )


def _all_keys(value: object) -> set[str]:
    result: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            result.add(key)
            result.update(_all_keys(child))
    elif isinstance(value, list):
        for child in value:
            result.update(_all_keys(child))
    return result


def test_public_interface_emits_raw_literal_records_and_empty_projection() -> None:
    output = build_presemantic_records_package_input(
        _normalized(_text_unit(), _image_unit()),
        MATERIAL_ID,
    )

    assert output["schema_version"] == PACKAGE_INPUT_SCHEMA_VERSION
    assert output["material_id"] == MATERIAL_ID
    records = output["records_artifact"]
    assert records["schema_version"] == RECORDS_SCHEMA_VERSION
    assert len(records["candidates"]) == 1
    assert len(records["origins"]) == 1
    assert len(records["contexts"]) == 1
    assert len(records["evidence_records"]) == 1
    assert records["projection_records"] == []
    candidate = records["candidates"][0]
    origin = records["origins"][0]
    evidence = records["evidence_records"][0]
    assert candidate["surface"] == "Alpha"
    assert candidate["extraction_methods"] == ["literal"]
    assert origin["literal_span"] == {"start": 0, "end": 5}
    assert evidence["statement"][0:5] == evidence["literal_surface"]
    source_units = output["normalized_source"]["layout_units"]
    assert [unit["unit_kind"] for unit in source_units] == ["text", "image"]
    assert "text" not in source_units[1]


def test_literal_fallback_is_bounded_exact_and_never_whole_unit_text() -> None:
    output = build_presemantic_records_package_input(
        _normalized(
            _text_unit(
                text="  Alpha concept definition",
            )
        ),
        MATERIAL_ID,
    )

    candidate = output["records_artifact"]["candidates"][0]
    origin = output["records_artifact"]["origins"][0]
    evidence = output["records_artifact"]["evidence_records"][0]
    unit_text = output["normalized_source"]["layout_units"][0]["text"]
    span = origin["literal_span"]
    assert candidate["surface"] == "Alpha"
    assert candidate["surface"] != unit_text
    assert unit_text[span["start"]:span["end"]] == candidate["surface"]
    assert evidence["statement"][span["start"]:span["end"]] == candidate[
        "surface"
    ]
    assert evidence["literal_surface"] == candidate["surface"]

    no_fallback = build_presemantic_records_package_input(
        _normalized(_text_unit(text="Alpha")),
        MATERIAL_ID,
    )
    assert no_fallback["records_artifact"]["candidates"] == []

    long_token = "x" * 80
    bounded = build_presemantic_records_package_input(
        _normalized(_text_unit(text=long_token)),
        MATERIAL_ID,
    )
    bounded_candidate = bounded["records_artifact"]["candidates"][0]
    assert bounded_candidate["surface"] == "x" * 64
    assert bounded_candidate["surface"] != long_token


def test_records_binding_hashes_only_canonical_records_artifact_and_replays_3_of_3() -> None:
    outputs = [
        build_presemantic_records_package_input(_normalized(), MATERIAL_ID)
        for _ in range(3)
    ]
    encoded = [canonical_json_bytes(output) for output in outputs]
    assert encoded[0] == encoded[1] == encoded[2]
    hashes = [
        output["deterministic_records_binding"]["raw_sha256"]
        for output in outputs
    ]
    assert hashes[0] == hashes[1] == hashes[2]
    records = outputs[0]["records_artifact"]
    assert "deterministic_records_binding" not in records
    assert hashes[0] == hashlib.sha256(canonical_json_bytes(records)).hexdigest()


def test_handoff_contract_compatibility_seals_pass() -> None:
    output = build_presemantic_records_package_input(_normalized(), MATERIAL_ID)

    sealed = seal_handoff_draft(
        _handoff_draft(output),
        normalized_source=output["normalized_source"],
    )

    assert sealed["status"] == "PASS"
    assert is_handoff_consumer_eligible_package(
        sealed,
        normalized_source=output["normalized_source"],
    )


def test_provider_output_has_no_semantic_authority_fields() -> None:
    output = build_presemantic_records_package_input(_normalized(), MATERIAL_ID)

    forbidden = {
        "concept_id",
        "concept_status",
        "route",
        "teaching_scope",
        "aliases",
        "alias_relation",
        "canonical_group",
        "merge",
        "split",
        "retain",
        "reject",
        "recovery",
        "conflict",
        "gold_slot_id",
        "ranking",
        "provider",
        "llm",
    }
    assert not (_all_keys(output) & forbidden)


def test_source_omission_and_failed_block_reasons_are_preserved_literally() -> None:
    normalized = _normalized()
    block = normalized["materials"][0]["blocks"][0]
    identity = {
        field: block[field]
        for field in (
            "material_id",
            "case_id",
            "artifact_ref",
            "block_id",
            "pdf_page",
            "source_ref",
        )
    }
    block["native_analysis_status"] = "partial"
    block["reasons"] = ["layout_unit_kind_unsupported"]
    block["warnings"] = ["layout_unit_kind_unsupported"]
    block["layout_unit_omissions"] = [
        {
            "identity": identity,
            "kind": "unknown",
            "layout_unit_id": "omission-001",
            "locator": {"bbox": [120.0, 0.0, 130.0, 10.0], "omission_order": 0},
            "provenance": {"policy": "synthetic-v2"},
            "reason": "layout_unit_kind_unsupported",
            "status": "omitted",
        }
    ]

    omission_output = build_presemantic_records_package_input(
        normalized,
        MATERIAL_ID,
    )

    failures = omission_output["records_artifact"]["source_failures"]
    assert len(failures) == 1
    assert failures[0]["source_failure_reasons"] == [
        "layout_unit_kind_unsupported"
    ]
    assert "failure_codes" not in failures[0]

    failed = _normalized()
    failed_block = failed["materials"][0]["blocks"][0]
    failed_block["selection_status"] = "failed"
    failed_block["native_analysis_status"] = "failed"
    failed_block["reasons"] = ["source_mapping_missing"]
    failed_block.pop("layout_units")
    output = build_presemantic_records_package_input(failed, MATERIAL_ID)
    assert output["records_artifact"]["candidates"] == []
    assert output["records_artifact"]["source_failures"][0][
        "source_failure_reasons"
    ] == ["source_mapping_missing"]


@pytest.mark.parametrize(
    ("group", "mutation", "message"),
    [
        (
            "schema",
            lambda artifact: artifact.update({"schema_version": "wrong"}),
            "normalized_blocks_schema_mismatch",
        ),
        (
            "material-missing",
            lambda artifact: artifact["materials"].clear(),
            "material_identity_unresolved",
        ),
        (
            "material-duplicate",
            lambda artifact: artifact["materials"].append(
                copy.deepcopy(artifact["materials"][0])
            ),
            "material_identity_unresolved",
        ),
        (
            "block-identity",
            lambda artifact: artifact["materials"][0]["blocks"][0].update(
                {"artifact_ref": "mismatched"}
            ),
            "normalized_block_identity_mismatch",
        ),
        (
            "locator",
            lambda artifact: artifact["materials"][0]["blocks"][0][
                "locator"
            ].update({"pdf_page": None}),
            "normalized_block_locator_invalid",
        ),
        (
            "unit-block",
            lambda artifact: artifact["materials"][0]["blocks"][0][
                "layout_units"
            ][0].update({"parent_block_id": "wrong"}),
            "layout_unit_block_mismatch",
        ),
        (
            "unit-duplicate",
            lambda artifact: artifact["materials"][0]["blocks"][0][
                "layout_units"
            ].append(copy.deepcopy(_text_unit(reading_order=1))),
            "layout_unit_identity_ambiguous",
        ),
        (
            "unit-bbox",
            lambda artifact: artifact["materials"][0]["blocks"][0][
                "layout_units"
            ][0].update({"bbox": [0.0, 0.0, 0.0, 10.0]}),
            "layout_unit_structure_invalid",
        ),
        (
            "failed-without-reason",
            lambda artifact: artifact["materials"][0]["blocks"][0].update(
                {
                    "selection_status": "failed",
                    "native_analysis_status": "failed",
                }
            ),
            "failed_block_reasons_missing",
        ),
        (
            "omission-identity",
            lambda artifact: artifact["materials"][0]["blocks"][0].update(
                {
                    "layout_unit_omissions": [
                        {
                            "identity": {},
                            "kind": "unknown",
                            "layout_unit_id": "omission-bad",
                            "locator": {"bbox": None, "omission_order": 0},
                            "provenance": {"policy": "synthetic"},
                            "reason": "layout_unit_invalid",
                            "status": "omitted",
                        }
                    ]
                }
            ),
            "layout_unit_omission_invalid",
        ),
    ],
)
def test_public_synthetic_critical_groups_fail_closed_10_of_10(
    group: str,
    mutation,
    message: str,
) -> None:
    artifact = _normalized()
    mutation(artifact)

    with pytest.raises(ValueError, match=message):
        build_presemantic_records_package_input(artifact, MATERIAL_ID)
