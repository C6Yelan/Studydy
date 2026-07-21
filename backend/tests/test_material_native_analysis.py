from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pymupdf
import pytest

from material_blocks import ActiveMaterial, build_material_blocks
from material_native_analysis import (
    BBOX_TOLERANCE_POINTS,
    analyze_material_native,
)


def _text_pdf(path: Path, page_texts: list[str]) -> None:
    document = pymupdf.open()
    for text in page_texts:
        page = document.new_page(width=300, height=200)
        page.insert_text((36, 72), text)
    document.save(path)
    document.close()


def _image_pdf(path: Path, with_text: bool = False) -> None:
    document = pymupdf.open()
    page = document.new_page(width=300, height=200)
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 8), False)
    pixmap.clear_with(240)
    image = pixmap.tobytes("png")
    page.insert_image(page.rect, stream=image)
    page.insert_image(pymupdf.Rect(20, 20, 140, 100), stream=image)
    if with_text:
        page.insert_text((36, 72), "alpha")
    document.save(path)
    document.close()


def _material(pdf_path: Path, case_id: str, pages: int) -> dict:
    digest = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    artifact = build_material_blocks(
        ActiveMaterial(
            case_id=case_id,
            artifact_ref=f"private:{case_id}",
            pdf_path=pdf_path,
            declared_pages=pages,
            expected_sha256=digest,
            source_refs={page: f"slide:{page}" for page in range(1, pages + 1)},
        )
    )
    return artifact["materials"][0]


def _artifact(*materials: dict) -> dict:
    return {
        "schema_version": "material-blocks/v1",
        "materials": list(materials),
    }


def _all_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            keys.add(key)
            keys.update(_all_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(_all_keys(child))
    return keys


def test_output_is_aggregate_only_bbox_valid_canonical_and_deterministic(
    tmp_path: Path,
) -> None:
    later_pdf = tmp_path / "later.pdf"
    earlier_pdf = tmp_path / "earlier.pdf"
    _text_pdf(later_pdf, ["alpha", "beta"])
    _text_pdf(earlier_pdf, ["gamma"])
    later = _material(later_pdf, "later", 2)
    earlier = _material(earlier_pdf, "earlier", 1)
    source = _artifact(later, earlier)
    paths = {"later": later_pdf, "earlier": earlier_pdf}

    first = analyze_material_native(source, paths)
    second = analyze_material_native(source, paths)

    assert first == second
    assert json.dumps(first, sort_keys=True, separators=(",", ":")) == json.dumps(
        second,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert first["schema_version"] == "material-native-analysis/v1"
    assert first["page_count"] == 3
    assert [
        (row["case_id"], row["pdf_page"]) for row in first["pages"]
    ] == [("earlier", 1), ("later", 1), ("later", 2)]

    forbidden = {
        "text",
        "image",
        "chars",
        "items",
        "cells",
        "tokens",
        "candidates",
        "concepts",
        "semantic",
    }
    assert not (_all_keys(first) & forbidden)
    for row in first["pages"]:
        x0, y0, x1, y1 = row["page_bbox"]
        assert all(math.isfinite(value) for value in row["page_bbox"])
        assert x1 > x0 and y1 > y0
        assert row["provenance"]["bbox_tolerance_points"] == BBOX_TOLERANCE_POINTS
        assert row["block_id"]
        assert row["source_ref"] == f"slide:{row['pdf_page']}"
        assert row["comparability"]["baseline_nonempty"] is True
        assert isinstance(row["comparability"]["rawdict_exact_match"], bool)
        assert isinstance(row["comparability"]["blocks_exact_match"], bool)
        assert row["native_summary"]["rawdict"]["character_count"] > 0


def test_gap_precedence_and_next_task_eligibility_use_only_native_signals(
    tmp_path: Path,
) -> None:
    ordinary_pdf = tmp_path / "ordinary.pdf"
    scan_pdf = tmp_path / "scan.pdf"
    complex_pdf = tmp_path / "complex.pdf"
    _text_pdf(ordinary_pdf, ["alpha"])
    _image_pdf(scan_pdf)
    _image_pdf(complex_pdf, with_text=True)

    ordinary = _material(ordinary_pdf, "ordinary", 1)
    ordinary["blocks"][0]["text"] = "different baseline"
    scan = _material(scan_pdf, "scan", 1)
    complex_material = _material(complex_pdf, "complex", 1)
    complex_material["blocks"][0]["text"] = "different baseline"

    result = analyze_material_native(
        _artifact(ordinary, scan, complex_material),
        {
            "ordinary": ordinary_pdf,
            "scan": scan_pdf,
            "complex": complex_pdf,
        },
    )
    rows = {row["case_id"]: row for row in result["pages"]}

    ordinary_row = rows["ordinary"]
    assert ordinary_row["gap_class"] == "ordinary_layout_gap"
    assert ordinary_row["next_task_eligibility"] == {
        "task5_ordinary_layout": True,
        "task6_scan_image_text": False,
        "task7_complex_structure": False,
    }

    scan_row = rows["scan"]
    assert scan_row["gap_signals"]["scan_image_text"]["detected"] is True
    assert scan_row["gap_signals"]["complex_structure"]["detected"] is True
    assert scan_row["gap_class"] == "scan_image_text_gap"
    assert scan_row["next_task_eligibility"]["task6_scan_image_text"] is True
    assert scan_row["next_task_eligibility"]["task7_complex_structure"] is True

    complex_row = rows["complex"]
    assert complex_row["gap_signals"]["ordinary_layout"]["detected"] is True
    assert complex_row["gap_signals"]["complex_structure"]["detected"] is True
    assert complex_row["gap_class"] == "complex_structure_gap"
    assert complex_row["next_task_eligibility"]["task5_ordinary_layout"] is True
    assert complex_row["next_task_eligibility"]["task7_complex_structure"] is True


def test_missing_document_and_native_api_failures_keep_rows_and_reason_codes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf_path = tmp_path / "case.pdf"
    _text_pdf(pdf_path, ["alpha", "beta"])
    material = _material(pdf_path, "case", 2)

    missing = analyze_material_native(_artifact(material), {})
    assert len(missing["pages"]) == 2
    assert all(row["status"] == "failed" for row in missing["pages"])
    assert all(row["reasons"] == ["document_path_missing"] for row in missing["pages"])
    assert all(row["gap_class"] == "analysis_unavailable" for row in missing["pages"])

    def fail_drawings(page: pymupdf.Page):
        raise RuntimeError("synthetic drawing failure")

    def unsupported_tables(page: pymupdf.Page, *args, **kwargs):
        raise AttributeError("synthetic unsupported table API")

    monkeypatch.setattr(pymupdf.Page, "get_drawings", fail_drawings)
    monkeypatch.setattr(pymupdf.Page, "find_tables", unsupported_tables)
    partial = analyze_material_native(_artifact(material), {"case": pdf_path})

    assert len(partial["pages"]) == 2
    assert all(row["status"] == "partial" for row in partial["pages"])
    assert all("drawing_analysis_failed" in row["reasons"] for row in partial["pages"])
    assert all("table_api_unsupported" in row["reasons"] for row in partial["pages"])
    assert all(row["native_summary"]["vectors"]["available"] is False for row in partial["pages"])
    assert all(row["native_summary"]["tables"]["available"] is False for row in partial["pages"])


def test_invalid_native_bbox_is_counted_and_marks_partial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf_path = tmp_path / "case.pdf"
    _text_pdf(pdf_path, ["alpha"])
    material = _material(pdf_path, "case", 1)
    original_get_text = pymupdf.Page.get_text

    def invalid_rawdict_bbox(page: pymupdf.Page, option: str = "text", *args, **kwargs):
        result = original_get_text(page, option, *args, **kwargs)
        if option == "rawdict" and result["blocks"]:
            result["blocks"][0]["bbox"] = (math.nan, 0, 10, 10)
        return result

    monkeypatch.setattr(pymupdf.Page, "get_text", invalid_rawdict_bbox)
    row = analyze_material_native(_artifact(material), {"case": pdf_path})["pages"][0]

    assert row["status"] == "partial"
    assert "native_bbox_invalid" in row["reasons"]
    assert row["native_summary"]["rawdict"]["block_bboxes"]["invalid"] == 1
    assert row["page_bbox"] == [0.0, 0.0, 300.0, 200.0]


@pytest.mark.parametrize(
    "source",
    [
        {},
        {"schema_version": "wrong", "materials": []},
        {"schema_version": "material-blocks/v1", "materials": None},
    ],
)
def test_invalid_input_contract_fails_with_stable_reason(source: dict) -> None:
    message = (
        "material_blocks_schema_mismatch"
        if source.get("schema_version") != "material-blocks/v1"
        else "materials_invalid"
    )
    with pytest.raises(ValueError, match=message):
        analyze_material_native(source, {})
