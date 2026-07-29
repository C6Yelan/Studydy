from __future__ import annotations

import copy
import hashlib

import pytest

from material_runtime_files import canonical_json_bytes
from handoff.contract import is_handoff_consumer_eligible_package
from handoff.contract_hashing import record_canonical_sha256
from handoff.package_builder import build_handoff_package
from presemantic_records_provider import (
    build_presemantic_records_package_input,
)


MATERIAL_ID = "material-package-synthetic"


def _unit(
    unit_id: str,
    reading_order: int,
    *,
    kind: str = "text",
    text: str | None = None,
    bbox: list[float] | None = None,
) -> dict:
    unit = {
        "layout_unit_id": unit_id,
        "parent_block_id": "block-001",
        "reading_order": reading_order,
        "bbox": bbox
        or [
            0.0,
            float(reading_order * 12),
            100.0,
            float(reading_order * 12 + 10),
        ],
        "kind": kind,
    }
    if kind == "text":
        value = text if text is not None else f"Literal {unit_id}"
        unit.update(
            {
                "text": value,
                "style_summary": {
                    "bold": False,
                    "font_names": ["Synthetic"],
                    "font_size_max": 10.0,
                    "font_size_min": 10.0,
                    "line_count": 1,
                    "monospace": False,
                },
            }
        )
    return unit


def _block(
    block_id: str,
    page: int,
    units: list[dict],
) -> dict:
    copied_units = copy.deepcopy(units)
    for unit in copied_units:
        unit["parent_block_id"] = block_id
    return {
        "material_id": MATERIAL_ID,
        "case_id": "case-package-synthetic",
        "artifact_ref": "synthetic:package-material",
        "block_id": block_id,
        "pdf_page": page,
        "source_ref": f"synthetic://source#page={page}&block={block_id}",
        "locator": {
            "pdf_page": page,
            "source_ref": f"synthetic://source#page={page}&block={block_id}",
        },
        "page_bbox": [0.0, 0.0, 200.0, 300.0],
        "provenance": {"native_analysis": {"policy": "synthetic-v2"}},
        "native_analysis_status": "success",
        "selection_status": "selected",
        "reasons": [],
        "warnings": [],
        "layout_units": copied_units,
        "layout_unit_omissions": [],
    }


def _normalized(*blocks: dict) -> dict:
    return {
        "schema_version": "normalized-material-blocks/v2",
        "status": "success",
        "source_provenance": {},
        "materials": [
            {
                "material_id": MATERIAL_ID,
                "case_id": "case-package-synthetic",
                "artifact_ref": "synthetic:package-material",
                "blocks": list(blocks),
            }
        ],
    }


def _envelope(
    units: list[dict] | None = None,
    *,
    blocks: list[dict] | None = None,
) -> dict:
    if blocks is None:
        blocks = [
            _block(
                "block-001",
                1,
                units or [_unit("unit-anchor", 0)],
            )
        ]
    return build_presemantic_records_package_input(
        _normalized(*blocks),
        MATERIAL_ID,
    )


def _context_id_for_unit(envelope: dict, unit_id: str) -> str:
    origin = next(
        origin
        for origin in envelope["records_artifact"]["origins"]
        if origin["layout_unit_id"] == unit_id
    )
    return origin["safe_context_id"]


def _output_context(
    package: dict,
    context_id: str,
) -> dict:
    return next(
        context
        for context in package["contexts"]
        if context["context_id"] == context_id
    )


def _add_known_column(envelope: dict, column: str = "column-a") -> None:
    for unit in envelope["normalized_source"]["layout_units"]:
        unit["column_id"] = column
        if unit.get("unit_kind") == "text":
            unit["normalized_text"] = unit["text"]


def _restamp_records_binding(envelope: dict) -> None:
    records = envelope["records_artifact"]
    envelope["deterministic_records_binding"]["raw_sha256"] = hashlib.sha256(
        canonical_json_bytes(records)
    ).hexdigest()


def _append_source_failure(
    envelope: dict,
    *,
    layout_unit_id: str | None,
    source_status: str,
    reason: str,
    block_id: str = "block-001",
) -> dict:
    source_unit = envelope["normalized_source"]["layout_units"][0]
    failure = {
        "source_failure_id": (
            f"source-failure:{source_status}:{layout_unit_id or block_id}"
        ),
        "material_id": MATERIAL_ID,
        "block_id": block_id,
        "layout_unit_id": layout_unit_id,
        "source_ref": source_unit["source_ref"],
        "pdf_page": source_unit["pdf_page"],
        "source_status": source_status,
        "source_failure_reasons": [reason],
        "locator": {
            "pdf_page": source_unit["pdf_page"],
            "source_ref": source_unit["source_ref"],
        },
        "provenance": {"policy": "synthetic-source-failure"},
    }
    failure["canonical_sha256"] = record_canonical_sha256(failure)
    envelope["records_artifact"]["source_failures"].append(failure)
    _restamp_records_binding(envelope)
    return failure


def test_public_builder_seals_pass_without_mutating_provider_input() -> None:
    envelope = _envelope()
    original = copy.deepcopy(envelope)

    package = build_handoff_package(envelope)

    assert envelope == original
    assert package["status"] == "PASS"
    assert is_handoff_consumer_eligible_package(
        package,
        normalized_source=envelope["normalized_source"],
    )
    assert package["candidate_source_binding"] == envelope[
        "deterministic_records_binding"
    ]
    assert package["projection_records"] == []


def test_build_attestation_keeps_production_replay_external_only() -> None:
    envelope = _envelope()

    package = build_handoff_package(envelope)

    assert len(package["build_attestations"]) == 1
    attestation = package["build_attestations"][0]
    assert attestation["replay_count"] == 0
    assert attestation["replay_content_sha256s"] == []
    assert attestation["deterministic_replay_pass"] is False
    assert attestation["input_bindings"] == sorted(
        [
            envelope["normalized_source_binding"],
            envelope["deterministic_records_binding"],
        ],
        key=lambda binding: (
            binding["artifact_id"],
            binding["schema_version"],
            binding["raw_sha256"],
        ),
    )


def test_three_independent_builds_have_identical_bytes_and_hashes() -> None:
    envelope = _envelope(
        [
            _unit("unit-001", 0),
            _unit("unit-anchor", 1),
            _unit("unit-003", 2),
        ]
    )
    _add_known_column(envelope)

    packages = [
        build_handoff_package(copy.deepcopy(envelope))
        for _ in range(3)
    ]

    encoded = [canonical_json_bytes(package) for package in packages]
    assert encoded[0] == encoded[1] == encoded[2]
    assert len({package["content_sha256"] for package in packages}) == 1
    assert len({package["canonical_sha256"] for package in packages}) == 1


def test_binding_hash_mismatch_fails_before_an_envelope_is_returned() -> None:
    envelope = _envelope()
    envelope["deterministic_records_binding"]["raw_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="^invalid package input$"):
        build_handoff_package(envelope)


def test_invalid_record_is_preserved_and_only_pr1_assigns_fail() -> None:
    envelope = _envelope()
    candidate = envelope["records_artifact"]["candidates"][0]
    candidate["surface"] = ""
    candidate["canonical_sha256"] = record_canonical_sha256(candidate)
    _restamp_records_binding(envelope)

    package = build_handoff_package(envelope)

    assert package["status"] == "FAIL"
    assert package["candidates"][0]["surface"] == ""
    assert any(
        "CANDIDATE_SURFACE_INVALID" in record["failure_codes"]
        for record in package["invalid_records"]
    )


def test_invalid_context_is_not_repaired_before_pr1_sealing() -> None:
    envelope = _envelope()
    context = envelope["records_artifact"]["contexts"][0]
    context["text"] = "tampered raw context"
    context["normalized_text"] = "tampered raw context"
    context["code_point_count"] = len(context["text"])
    context["canonical_sha256"] = record_canonical_sha256(context)
    _restamp_records_binding(envelope)

    package = build_handoff_package(envelope)

    assert package["status"] == "FAIL"
    assert package["contexts"][0]["text"] == "tampered raw context"
    assert any(
        "CONTEXT_TEXT_INVALID" in record["failure_codes"]
        for record in package["invalid_records"]
    )


def test_noncritical_source_omission_passes_without_top_level_pollution() -> None:
    envelope = _envelope()
    source_failure = _append_source_failure(
        envelope,
        layout_unit_id="omission-unit-001",
        source_status="omitted",
        reason="layout_unit_kind_unsupported",
    )

    package = build_handoff_package(envelope)

    assert package["status"] == "PASS"
    assert "source_failures" not in package
    assert envelope["records_artifact"]["source_failures"] == [
        source_failure
    ]
    assert package["candidate_source_binding"] == envelope[
        "deterministic_records_binding"
    ]
    assert is_handoff_consumer_eligible_package(
        package,
        normalized_source=envelope["normalized_source"],
    )


def test_candidate_lineage_source_failure_uses_existing_lifecycle_path() -> None:
    envelope = _envelope()
    _append_source_failure(
        envelope,
        layout_unit_id="unit-anchor",
        source_status="omitted",
        reason="layout_unit_invalid",
    )

    package = build_handoff_package(envelope)

    candidate = package["candidates"][0]
    assert package["status"] == "FAIL"
    assert candidate["construction_status"] == "invalid"
    assert candidate["failure_codes"] == ["layout_unit_invalid"]
    assert any(
        "layout_unit_invalid" in record["failure_codes"]
        for record in package["invalid_records"]
    )
    assert all(
        "PKG_FIELD_INVALID" not in record["failure_codes"]
        for record in package["invalid_records"]
    )


def test_package_scope_source_failure_uses_existing_package_path() -> None:
    envelope = _envelope()
    source_failure = _append_source_failure(
        envelope,
        layout_unit_id=None,
        source_status="failed",
        reason="source_mapping_missing",
    )

    package = build_handoff_package(envelope)

    assert package["status"] == "FAIL"
    assert "source_failures" not in package
    package_failure = next(
        record
        for record in package["invalid_records"]
        if record["collection"] == "package"
    )
    assert package_failure["failure_codes"] == [
        "PKG_CANDIDATES_INVALID"
    ]
    assert source_failure["source_failure_reasons"][0] in package_failure[
        "reason"
    ]
    assert "PKG_FIELD_INVALID" not in package_failure["failure_codes"]


def test_unrepresentable_source_failure_locator_keeps_no_package_boundary() -> None:
    envelope = _envelope()
    failure = _append_source_failure(
        envelope,
        layout_unit_id=None,
        source_status="failed",
        reason="page_unreadable",
    )
    failure["locator"] = {}
    failure["canonical_sha256"] = record_canonical_sha256(failure)
    _restamp_records_binding(envelope)

    with pytest.raises(ValueError, match="^invalid package input$"):
        build_handoff_package(envelope)


def test_zero_literal_candidates_use_package_level_fail_closed_path() -> None:
    envelope = _envelope(
        [_unit("unit-anchor", 0, text="Alpha")]
    )

    package = build_handoff_package(envelope)

    assert package["status"] == "FAIL"
    assert package["candidates"] == []
    package_failures = [
        record
        for record in package["invalid_records"]
        if record["collection"] == "package"
    ]
    assert all(
        record["failure_codes"] == ["PKG_CANDIDATES_INVALID"]
        for record in package_failures
    )
    assert any(
        "zero valid literal candidates" in record["reason"]
        for record in package_failures
    )


def test_semantic_authority_input_is_rejected_not_copied() -> None:
    envelope = _envelope()
    envelope["records_artifact"]["candidates"][0][
        "concept_status"
    ] = "retain"
    _restamp_records_binding(envelope)

    with pytest.raises(ValueError, match="^invalid package input$"):
        build_handoff_package(envelope)


def test_anchor_overflow_is_not_truncated_and_pr1_seals_fail() -> None:
    text = "x" * 1201
    envelope = _envelope([_unit("unit-anchor", 0, text=text)])
    _add_known_column(envelope)
    context_id = _context_id_for_unit(envelope, "unit-anchor")

    package = build_handoff_package(envelope)

    context = _output_context(package, context_id)
    assert package["status"] == "FAIL"
    assert context["text"] == text
    assert context["code_point_count"] == 1201
    failure_codes = {
        code
        for record in package["invalid_records"]
        for code in record["failure_codes"]
    }
    assert "CONTEXT_ANCHOR_OVERFLOW" in failure_codes
    assert "CONTEXT_LENGTH_LIMIT_EXCEEDED" in failure_codes


def _critical_scenario(
    scenario: str,
) -> tuple[dict, str, int, str, str, str]:
    if scenario == "page_boundary":
        envelope = _envelope(
            blocks=[
                _block("block-001", 1, [_unit("unit-anchor", 0)]),
                _block("block-002", 2, [_unit("unit-next", 0)]),
            ]
        )
        expected_next = "page_boundary"
    elif scenario == "block_boundary":
        envelope = _envelope(
            blocks=[
                _block("block-001", 1, [_unit("unit-anchor", 0)]),
                _block("block-002", 1, [_unit("unit-next", 0)]),
            ]
        )
        expected_next = "block_boundary"
    elif scenario == "limits":
        envelope = _envelope(
            [
                _unit("unit-001", 0),
                _unit("unit-anchor", 1),
                _unit("unit-003", 2),
                _unit("unit-004", 3),
            ]
        )
        expected_next = "unit_limit"
    elif scenario == "unknown":
        envelope = _envelope(
            [
                _unit("unit-001", 0),
                _unit("unit-anchor", 1),
                _unit("unit-003", 2),
            ]
        )
        expected_next = "unknown_boundary"
    elif scenario == "sentence_terminal":
        envelope = _envelope(
            [
                _unit("unit-001", 0, text="Completed."),
                _unit("unit-anchor", 1),
            ]
        )
        expected_next = "material_end"
    elif scenario == "horizontal_overlap":
        envelope = _envelope(
            [
                _unit(
                    "unit-anchor",
                    0,
                    bbox=[0.0, 0.0, 100.0, 10.0],
                ),
                _unit(
                    "unit-next",
                    1,
                    bbox=[50.0, 12.0, 150.0, 22.0],
                ),
            ]
        )
        expected_next = "horizontal_overlap"
    elif scenario == "vertical_gap":
        envelope = _envelope(
            [
                _unit(
                    "unit-anchor",
                    0,
                    bbox=[0.0, 0.0, 100.0, 10.0],
                ),
                _unit(
                    "unit-next",
                    1,
                    bbox=[0.0, 50.0, 100.0, 60.0],
                ),
            ]
        )
        expected_next = "vertical_gap"
    else:
        envelope = _envelope(
            [
                _unit("unit-anchor", 0),
                _unit("unit-next", 1),
            ]
        )
        expected_next = {
            "safe_three": "material_end",
            "material_boundary": "material_boundary",
            "column_boundary": "column_boundary",
            "nonconsecutive": "non_consecutive_order",
            "heading": "heading_boundary",
            "omission": "structural_boundary",
        }.get(scenario, "material_end")

    _add_known_column(envelope)
    source_units = envelope["normalized_source"]["layout_units"]
    source_by_id = {
        unit["layout_unit_id"]: unit
        for unit in source_units
    }
    anchor = source_by_id["unit-anchor"]
    next_unit = source_by_id.get("unit-next") or source_by_id.get(
        "unit-003"
    )
    expected_count = 1
    expected_status = "PASS"
    expected_previous = "material_start"

    if scenario == "safe_three":
        third = {
            **copy.deepcopy(next_unit),
            "layout_unit_id": "unit-third",
            "reading_order": 2,
            "bbox": [0.0, 24.0, 100.0, 34.0],
            "text": "Third literal",
            "normalized_text": "Third literal",
        }
        source_units.append(third)
        expected_count = 3
    elif scenario == "material_boundary":
        next_unit["material_id"] = "zz-material-other"
        expected_status = "FAIL"
    elif scenario == "column_boundary":
        next_unit["column_id"] = "column-b"
    elif scenario == "unknown":
        source_by_id["unit-001"].pop("column_id")
        anchor["boundary_after"] = "unknown"
        expected_previous = "unknown_column"
    elif scenario == "nonconsecutive":
        next_unit["reading_order"] = 2
        expected_status = "FAIL"
    elif scenario == "heading":
        anchor["heading_transition_after"] = True
    elif scenario == "omission":
        anchor["intervening_kind_after"] = "omission"
    elif scenario == "sentence_terminal":
        expected_previous = "sentence_terminal"
    elif scenario == "limits":
        expected_count = 3
        expected_previous = "material_start"
    elif scenario in {
        "page_boundary",
        "block_boundary",
        "horizontal_overlap",
        "vertical_gap",
    }:
        pass
    else:
        expected_count = 2 if scenario == "safe_three" else expected_count

    context_id = _context_id_for_unit(envelope, "unit-anchor")
    return (
        envelope,
        context_id,
        expected_count,
        expected_previous,
        expected_next,
        expected_status,
    )


@pytest.mark.parametrize(
    "scenario",
    [
        "safe_three",
        "material_boundary",
        "page_boundary",
        "block_boundary",
        "column_boundary",
        "unknown",
        "nonconsecutive",
        "heading",
        "omission",
        "sentence_terminal",
        "horizontal_overlap",
        "vertical_gap",
        "limits",
    ],
)
def test_critical_synthetic_context_groups_13_of_13(
    scenario: str,
) -> None:
    (
        envelope,
        context_id,
        expected_count,
        expected_previous,
        expected_next,
        expected_status,
    ) = _critical_scenario(scenario)

    package = build_handoff_package(envelope)

    context = _output_context(package, context_id)
    assert package["status"] == expected_status
    assert len(context["layout_unit_refs"]) == expected_count
    assert context["boundary_reason"]["previous"] == expected_previous
    assert context["boundary_reason"]["next"] == expected_next
    assert len(context["layout_unit_refs"]) <= 3
    assert context["code_point_count"] <= 1200


def test_negative_zero_context_geometry_keeps_builder_policy() -> None:
    envelope = _envelope(
        [
            _unit("unit-anchor", 0),
            _unit("unit-next", 1),
        ]
    )
    _add_known_column(envelope)
    envelope["normalized_source"]["layout_units"][1]["bbox"][0] = -0.0
    context_id = _context_id_for_unit(envelope, "unit-anchor")

    package = build_handoff_package(envelope)

    context = _output_context(package, context_id)
    assert package["status"] == "PASS"
    assert context["layout_unit_refs"] == [
        {"layout_unit_id": "unit-anchor"},
        {"layout_unit_id": "unit-next"},
    ]
    assert context["boundary_reason"]["next"] == "material_end"


def test_image_boundary_and_code_point_limit_stop_without_dropping_anchor() -> None:
    image_envelope = _envelope(
        [
            _unit("unit-anchor", 0),
            _unit("unit-image", 1, kind="image"),
        ]
    )
    _add_known_column(image_envelope)
    image_context_id = _context_id_for_unit(
        image_envelope,
        "unit-anchor",
    )

    image_package = build_handoff_package(image_envelope)

    image_context = _output_context(image_package, image_context_id)
    assert image_package["status"] == "PASS"
    assert len(image_context["layout_unit_refs"]) == 1
    assert image_context["boundary_reason"]["next"] == "structural_boundary"

    length_envelope = _envelope(
        [
            _unit("unit-anchor", 0, text="a" * 700),
            _unit("unit-next", 1, text="b" * 600),
        ]
    )
    _add_known_column(length_envelope)
    length_context_id = _context_id_for_unit(
        length_envelope,
        "unit-anchor",
    )

    length_package = build_handoff_package(length_envelope)

    length_context = _output_context(length_package, length_context_id)
    assert length_package["status"] == "PASS"
    assert len(length_context["layout_unit_refs"]) == 1
    assert length_context["boundary_reason"]["next"] == "code_point_limit"
