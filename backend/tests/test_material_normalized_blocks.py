from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

import material_lexical_index
from material_normalized_blocks import (
    MATERIAL_BLOCKS_STABLE_PATH,
    NATIVE_ANALYSIS_STABLE_PATH,
    NORMALIZED_BLOCKS_STABLE_PATH,
    NORMALIZED_BLOCKS_V2_STABLE_PATH,
    SCHEMA_VERSION,
    load_and_normalize_material_blocks,
    normalize_material_blocks,
    persist_normalized_material_blocks,
)
from material_runtime_files import canonical_json_bytes


def _blocks(*rows: tuple[str, int, str]) -> dict:
    materials: dict[str, dict] = {}
    for case_id, page, text in rows:
        material = materials.setdefault(
            case_id,
            {
                "material_id": f"material:{case_id}",
                "case_id": case_id,
                "artifact_ref": f"artifact:{case_id}",
                "blocks": [],
            },
        )
        material["blocks"].append(
            {
                "block_id": f"block:{case_id}:{page}",
                "text": text,
                "locator": {"pdf_page": page, "source_ref": f"source:{page}"},
                "parser_status": "success",
                "failure_reason": None,
            }
        )
    return {
        "schema_version": "material-blocks/v1",
        "parser_provenance": {"parser": "synthetic"},
        "materials": list(materials.values()),
    }


def _native(
    *rows: tuple[str, int, str, list[str]],
) -> dict:
    pages = []
    for case_id, page, status, reasons in rows:
        block_id = f"block:{case_id}:{page}"
        pages.append(
            {
                "material_id": f"material:{case_id}",
                "case_id": case_id,
                "artifact_ref": f"artifact:{case_id}",
                "block_id": block_id,
                "pdf_page": page,
                "source_ref": f"source:{page}",
                "layout_unit_omissions": [],
                "layout_units": [
                    {
                        "layout_unit_id": f"unit:{case_id}:{page}",
                        "parent_block_id": block_id,
                        "reading_order": 0,
                        "bbox": [0.0, 0.0, 80.0, 20.0],
                        "kind": "text",
                        "text": f"{case_id}-{page}",
                        "style_summary": {
                            "bold": False,
                            "font_names": ["Synthetic"],
                            "font_size_max": 10.0,
                            "font_size_min": 10.0,
                            "line_count": 1,
                            "monospace": False,
                        },
                    }
                ],
                "page_bbox": [0.0, 0.0, 100.0, 100.0],
                "provenance": {"library": "synthetic"},
                "status": status,
                "reasons": reasons,
            }
        )
    return {
        "schema_version": "material-native-analysis/v2",
        "page_count": len(pages),
        "pages": pages,
    }


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


def test_success_and_warning_partial_are_selected_deterministically() -> None:
    blocks = _blocks(("later", 2, "beta"), ("earlier", 1, "alpha"))
    native = _native(
        ("later", 2, "partial", ["native_bbox_invalid"]),
        ("earlier", 1, "success", []),
    )

    first = normalize_material_blocks(blocks, native)
    second = normalize_material_blocks(blocks, native)

    assert first == second
    assert first["status"] == "success"
    normalized_blocks = [
        block for material in first["materials"] for block in material["blocks"]
    ]
    assert len(normalized_blocks) == 2
    assert all(block["selection_status"] == "selected" for block in normalized_blocks)
    assert (
        sum("native_bbox_invalid" in block["warnings"] for block in normalized_blocks)
        == 1
    )
    assert [
        (material["case_id"], block["locator"]["pdf_page"])
        for material in first["materials"]
        for block in material["blocks"]
    ] == [("earlier", 1), ("later", 2)]
    partial = first["materials"][1]["blocks"][0]
    assert partial["selection_status"] == "selected"
    assert partial["native_analysis_status"] == "partial"
    assert partial["provenance"] == {
        "native_analysis": {"library": "synthetic"},
    }
    assert partial["selection_reason"] == "native_bbox_invalid"
    assert partial["warnings"] == ["native_bbox_invalid"]
    assert partial["layout_units"][0]["text"] == "later-2"
    assert partial["layout_unit_omissions"] == []
    assert first["source_provenance"]["material_blocks"]["parser_provenance"] == {
        "parser": "synthetic"
    }
    # 正規化結果保留最小 layout units，但不帶入其他底層結構或語意資料。
    forbidden = {
        "native_summary",
        "rawdict",
        "words",
        "images",
        "image_data",
        "stream",
        "chars",
        "cells",
        "candidates",
        "concepts",
        "semantic",
    }
    assert not (_all_keys(first) & forbidden)


def test_other_partial_is_visible_failed_without_lexical_text() -> None:
    result = normalize_material_blocks(
        _blocks(("case", 1, "private text")),
        _native(("case", 1, "partial", ["table_analysis_failed"])),
    )

    block = result["materials"][0]["blocks"][0]
    assert result["status"] == "failed"
    assert set(block) == {
        "material_id",
        "case_id",
        "artifact_ref",
        "block_id",
        "pdf_page",
        "source_ref",
        "locator",
        "page_bbox",
        "provenance",
        "native_analysis_status",
        "selection_status",
        "reasons",
        "warnings",
    }
    assert block["selection_status"] == "failed"
    assert block["native_analysis_status"] == "partial"
    assert block["reasons"] == ["selection_failed", "table_analysis_failed"]
    assert "text" not in block


@pytest.mark.parametrize(
    ("mutation", "expected_reason"),
    [
        ("missing_match", "identity_join_invalid"),
        ("duplicate_match", "identity_join_invalid"),
        ("invalid_bbox", "native_analysis_row_invalid"),
        ("missing_provenance", "native_analysis_row_invalid"),
        ("failed", "selection_failed"),
    ],
)
def test_join_and_validation_failed_blocks_are_visible(
    mutation: str,
    expected_reason: str,
) -> None:
    blocks = _blocks(("case", 1, "alpha"))
    native = _native(("case", 1, "success", []))
    if mutation == "missing_match":
        native["pages"][0]["block_id"] = "block:other:1"
    elif mutation == "duplicate_match":
        blocks["materials"][0]["blocks"].append(
            deepcopy(blocks["materials"][0]["blocks"][0])
        )
    elif mutation == "invalid_bbox":
        native["pages"][0]["page_bbox"] = [0.0, 0.0, float("nan"), 100.0]
    elif mutation == "missing_provenance":
        native["pages"][0]["provenance"] = {}
    else:
        native["pages"][0]["status"] = "failed"
        native["pages"][0]["reasons"] = ["native_analysis_failed"]

    result = normalize_material_blocks(blocks, native)

    normalized_blocks = [
        block for material in result["materials"] for block in material["blocks"]
    ]
    assert len(normalized_blocks) == 1
    failed_block = normalized_blocks[0]
    assert failed_block["selection_status"] == "failed"
    assert expected_reason in failed_block["reasons"]
    assert "text" not in failed_block
    if mutation == "invalid_bbox":
        assert failed_block["page_bbox"] is None
        assert b"NaN" not in canonical_json_bytes(result)


@pytest.mark.parametrize(
    "rows",
    [
        [("single", 1, "alpha")],
        [
            ("later", 2, "beta"),
            ("earlier", 1, "alpha"),
            ("later", 1, "gamma"),
        ],
    ],
)
def test_multi_cardinality_has_no_fixed_page_or_material_count(
    rows: list[tuple[str, int, str]],
) -> None:
    native_rows = [(case, page, "success", []) for case, page, _ in rows]

    result = normalize_material_blocks(_blocks(*rows), _native(*native_rows))

    normalized_blocks = [
        block for material in result["materials"] for block in material["blocks"]
    ]
    assert len(normalized_blocks) == len(rows)
    assert all(block["selection_status"] == "selected" for block in normalized_blocks)


def test_loader_reads_stable_runtime_inputs_and_persistence_is_atomic(
    tmp_path: Path,
) -> None:
    blocks = _blocks(("case", 1, "alpha"))
    native = _native(("case", 1, "success", []))
    for relative, value in [
        (MATERIAL_BLOCKS_STABLE_PATH, blocks),
        (NATIVE_ANALYSIS_STABLE_PATH, native),
    ]:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(canonical_json_bytes(value))
    artifact = load_and_normalize_material_blocks(repo_root=tmp_path)
    persist_normalized_material_blocks(artifact, repo_root=tmp_path)

    stable = tmp_path / NORMALIZED_BLOCKS_V2_STABLE_PATH
    assert json.loads(stable.read_bytes()) == artifact
    assert set(artifact) == {
        "schema_version",
        "status",
        "source_provenance",
        "materials",
    }
    assert artifact["source_provenance"] == {
        "material_blocks": {
            "schema_version": "material-blocks/v1",
            "parser_provenance": {"parser": "synthetic"},
        },
        "native_analysis": {
            "schema_version": "material-native-analysis/v2",
        },
    }
    assert not list((tmp_path / ".studydy-runtime").rglob("*.tmp"))


def test_legacy_lexical_schema_and_locator_remain_v1_literals() -> None:
    expected_schema = "normalized-material-blocks/v1"
    expected_locator = (
        ".studydy-runtime/materials/normalized-blocks/stable/"
        "normalized-material-blocks.v1.json"
    )

    assert SCHEMA_VERSION == expected_schema
    assert NORMALIZED_BLOCKS_STABLE_PATH == expected_locator
    assert (
        material_lexical_index.NORMALIZED_BLOCKS_SCHEMA_VERSION
        == expected_schema
    )
    assert (
        material_lexical_index.NORMALIZED_BLOCKS_STABLE_PATH
        == expected_locator
    )


def test_layout_unit_omission_is_preserved_and_marks_partial() -> None:
    blocks = _blocks(("case", 1, "alpha"))
    native = _native(
        ("case", 1, "partial", ["layout_unit_kind_unsupported"])
    )
    row = native["pages"][0]
    row["layout_unit_omissions"] = [
        {
            "identity": {
                field: row[field]
                for field in (
                    "material_id",
                    "case_id",
                    "artifact_ref",
                    "block_id",
                    "pdf_page",
                    "source_ref",
                )
            },
            "kind": "unknown",
            "layout_unit_id": "omission:case:1",
            "locator": {
                "bbox": [82.0, 0.0, 90.0, 20.0],
                "omission_order": 0,
            },
            "provenance": {"native_policy": "synthetic-v2"},
            "reason": "layout_unit_kind_unsupported",
            "status": "omitted",
        }
    ]

    result = normalize_material_blocks(blocks, native)

    assert result["status"] == "partial"
    block = result["materials"][0]["blocks"][0]
    assert block["selection_status"] == "selected"
    assert block["selection_reason"] == "layout_unit_omissions_present"
    assert block["warnings"] == ["layout_unit_kind_unsupported"]
    assert block["layout_unit_omissions"] == row["layout_unit_omissions"]


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate_unit_id",
        "reading_order_gap",
        "bbox_outside_page",
        "unknown_kind",
        "missing_font_maximum",
    ],
)
def test_invalid_layout_units_fail_closed(mutation: str) -> None:
    blocks = _blocks(("case", 1, "alpha"))
    native = _native(("case", 1, "success", []))
    row = native["pages"][0]
    unit = row["layout_units"][0]
    if mutation == "duplicate_unit_id":
        duplicate = deepcopy(unit)
        duplicate["reading_order"] = 1
        row["layout_units"].append(duplicate)
    elif mutation == "reading_order_gap":
        unit["reading_order"] = 1
    elif mutation == "bbox_outside_page":
        unit["bbox"] = [0.0, 0.0, 120.0, 20.0]
    elif mutation == "unknown_kind":
        unit["kind"] = "caption"
    else:
        del unit["style_summary"]["font_size_max"]

    result = normalize_material_blocks(blocks, native)

    block = result["materials"][0]["blocks"][0]
    assert result["status"] == "failed"
    assert block["selection_status"] == "failed"
    assert "native_layout_units_invalid" in block["reasons"]
    assert "layout_units" not in block


def test_image_unit_is_preserved_without_payload() -> None:
    blocks = _blocks(("case", 1, "alpha"))
    native = _native(("case", 1, "success", []))
    native["pages"][0]["layout_units"] = [
        {
            "layout_unit_id": "image:case:1",
            "parent_block_id": "block:case:1",
            "reading_order": 0,
            "bbox": [0.0, 0.0, 80.0, 20.0],
            "kind": "image",
        }
    ]

    result = normalize_material_blocks(blocks, native)

    unit = result["materials"][0]["blocks"][0]["layout_units"][0]
    assert unit["kind"] == "image"
    assert set(unit) == {
        "layout_unit_id",
        "parent_block_id",
        "reading_order",
        "bbox",
        "kind",
    }
