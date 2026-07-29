from __future__ import annotations

import hashlib
import math
from pathlib import Path

import pymupdf
import pytest

from material_blocks import ActiveMaterial, build_material_blocks
from material_native_analysis import (
    BBOX_MATCH_TOLERANCE_PT,
    NATIVE_ANALYSIS_STABLE_PATH,
    analyze_material_native,
    persist_material_native_analysis,
)
from material_runtime_files import canonical_json_bytes


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


def _descriptor(material: dict, pdf_path: Path) -> dict:
    return {
        "material_id": material["material_id"],
        "case_id": material["case_id"],
        "artifact_ref": material["artifact_ref"],
        "pdf_path": pdf_path,
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
    descriptors = [
        _descriptor(later, later_pdf),
        _descriptor(earlier, earlier_pdf),
    ]

    first = analyze_material_native(source, descriptors)
    second = analyze_material_native(source, descriptors)

    assert first == second
    assert first["schema_version"] == "material-native-analysis/v2"
    assert first["page_count"] == 3
    assert [
        (row["case_id"], row["pdf_page"]) for row in first["pages"]
    ] == [("earlier", 1), ("later", 1), ("later", 2)]

    # v2 保留文字 layout unit，但不得保留 image payload 或語意輸出。
    forbidden = {
        "image_data",
        "stream",
        "xref",
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
        assert set(row) == {
            "material_id",
            "case_id",
            "artifact_ref",
            "block_id",
            "pdf_page",
            "source_ref",
            "layout_unit_omissions",
            "layout_units",
            "page_bbox",
            "provenance",
            "native_summary",
            "status",
            "reasons",
        }
        x0, y0, x1, y1 = row["page_bbox"]
        assert all(math.isfinite(value) for value in row["page_bbox"])
        assert x1 > x0 and y1 > y0
        assert row["provenance"]["bbox_tolerance_points"] == BBOX_MATCH_TOLERANCE_PT
        assert (
            row["provenance"]["native_policy"]
            == "dict-layout-units:sort-true-v2"
        )
        assert (
            row["provenance"]["coordinate_space"]
            == "pymupdf-unrotated-page-v1"
        )
        assert row["block_id"]
        assert row["source_ref"] == f"slide:{row['pdf_page']}"
        assert set(row["native_summary"]) == {"blocks"}
        assert row["native_summary"]["blocks"]["available"] is True
        assert row["native_summary"]["blocks"]["text_blocks"] == 1
        assert row["layout_unit_omissions"] == []
        assert len(row["layout_units"]) == 1
        unit = row["layout_units"][0]
        assert unit["parent_block_id"] == row["block_id"]
        assert unit["reading_order"] == 0
        assert unit["kind"] == "text"
        assert unit["text"]
        assert unit["style_summary"]["font_size_max"] == 11.0


def test_missing_document_and_blocks_failure_keep_rows_and_reason_codes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf_path = tmp_path / "case.pdf"
    _text_pdf(pdf_path, ["alpha", "beta"])
    material = _material(pdf_path, "case", 2)

    missing = analyze_material_native(_artifact(material), [])
    assert len(missing["pages"]) == 2
    assert all(row["status"] == "failed" for row in missing["pages"])
    assert all(row["reasons"] == ["source_mapping_missing"] for row in missing["pages"])
    assert all(
        set(row)
        == {
            "material_id",
            "case_id",
            "artifact_ref",
            "block_id",
            "pdf_page",
            "source_ref",
            "page_bbox",
            "provenance",
            "native_summary",
            "status",
            "reasons",
        }
        for row in missing["pages"]
    )

    original_get_text = pymupdf.Page.get_text

    def fail_blocks(page: pymupdf.Page, option: str = "text", *args, **kwargs):
        if option == "blocks":
            raise RuntimeError("synthetic blocks failure")
        return original_get_text(page, option, *args, **kwargs)

    monkeypatch.setattr(pymupdf.Page, "get_text", fail_blocks)
    partial = analyze_material_native(
        _artifact(material),
        [_descriptor(material, pdf_path)],
    )

    assert len(partial["pages"]) == 2
    assert all(row["status"] == "partial" for row in partial["pages"])
    assert all("blocks_analysis_failed" in row["reasons"] for row in partial["pages"])
    assert all(
        row["native_summary"]["blocks"]["available"] is False
        for row in partial["pages"]
    )


def test_invalid_native_bbox_is_counted_and_marks_partial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf_path = tmp_path / "case.pdf"
    _text_pdf(pdf_path, ["alpha"])
    material = _material(pdf_path, "case", 1)
    original_get_text = pymupdf.Page.get_text

    def invalid_blocks_bbox(page: pymupdf.Page, option: str = "text", *args, **kwargs):
        result = original_get_text(page, option, *args, **kwargs)
        if option == "blocks" and result:
            block = list(result[0])
            block[0] = math.nan
            result[0] = tuple(block)
        return result

    monkeypatch.setattr(pymupdf.Page, "get_text", invalid_blocks_bbox)
    row = analyze_material_native(
        _artifact(material),
        [_descriptor(material, pdf_path)],
    )["pages"][0]

    assert row["status"] == "partial"
    assert "native_bbox_invalid" in row["reasons"]
    assert row["native_summary"]["blocks"]["bboxes"]["invalid"] == 1
    assert row["page_bbox"] == [0.0, 0.0, 300.0, 200.0]


def test_document_page_count_mismatch_keeps_analyzable_page_partial(
    tmp_path: Path,
) -> None:
    baseline_pdf = tmp_path / "baseline.pdf"
    changed_pdf = tmp_path / "changed.pdf"
    _text_pdf(baseline_pdf, ["alpha"])
    _text_pdf(changed_pdf, ["alpha", "beta"])
    material = _material(baseline_pdf, "case", 1)

    result = analyze_material_native(
        _artifact(material),
        [_descriptor(material, changed_pdf)],
    )

    assert result["page_count"] == 1
    row = result["pages"][0]
    assert row["status"] == "partial"
    assert "document_page_count_mismatch" in row["reasons"]
    assert row["native_summary"]["blocks"]["available"] is True


def test_bbox_outside_tolerance_is_non_blocking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf_path = tmp_path / "case.pdf"
    _text_pdf(pdf_path, ["alpha"])
    material = _material(pdf_path, "case", 1)
    original_get_text = pymupdf.Page.get_text

    def outside_blocks_bbox(page: pymupdf.Page, option: str = "text", *args, **kwargs):
        result = original_get_text(page, option, *args, **kwargs)
        if option == "blocks" and result:
            block = list(result[0])
            block[0] = -BBOX_MATCH_TOLERANCE_PT - 1
            result[0] = tuple(block)
        return result

    monkeypatch.setattr(pymupdf.Page, "get_text", outside_blocks_bbox)
    row = analyze_material_native(
        _artifact(material),
        [_descriptor(material, pdf_path)],
    )["pages"][0]

    assert row["status"] == "success"
    assert row["reasons"] == ["native_bbox_outside_page_tolerance"]
    assert (
        row["native_summary"]["blocks"]["bboxes"]["outside_page_tolerance"]
        == 1
    )


def test_blocks_summary_reports_type_counts_and_bboxes(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "structured.pdf"
    _image_pdf(pdf_path, with_text=True)
    material = _material(pdf_path, "structured", 1)

    row = analyze_material_native(
        _artifact(material),
        [_descriptor(material, pdf_path)],
    )["pages"][0]

    blocks = row["native_summary"]["blocks"]
    assert blocks["available"] is True
    assert blocks["count"] == 1
    assert blocks["text_blocks"] == 1
    assert blocks["image_blocks"] == 0
    assert blocks["bboxes"]["total"] == 1
    assert blocks["bboxes"]["valid"] == 1


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
        analyze_material_native(source, [])


def test_persist_native_analysis_uses_stable_runtime_path(tmp_path: Path) -> None:
    pdf_path = tmp_path / "case.pdf"
    _text_pdf(pdf_path, ["alpha"])
    material = _material(pdf_path, "case", 1)
    artifact = analyze_material_native(
        _artifact(material),
        [_descriptor(material, pdf_path)],
    )

    persist_material_native_analysis(artifact, repo_root=tmp_path)

    stable = tmp_path / NATIVE_ANALYSIS_STABLE_PATH
    assert stable.read_bytes() == canonical_json_bytes(artifact)
    assert not list((tmp_path / ".studydy-runtime").rglob("*.tmp"))


def test_layout_units_use_stable_local_geometry_order(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "layout.pdf"
    document = pymupdf.open()
    page = document.new_page(width=300, height=200)
    page.insert_text((160, 120), "later")
    page.insert_text((36, 60), "earlier")
    document.save(pdf_path)
    document.close()
    material = _material(pdf_path, "layout", 1)

    first = analyze_material_native(
        _artifact(material),
        [_descriptor(material, pdf_path)],
    )
    second = analyze_material_native(
        _artifact(material),
        [_descriptor(material, pdf_path)],
    )

    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    units = first["pages"][0]["layout_units"]
    assert [unit["reading_order"] for unit in units] == [0, 1]
    assert [unit["text"] for unit in units] == ["earlier", "later"]
    assert len({unit["layout_unit_id"] for unit in units}) == 2


def test_invalid_structured_unit_is_preserved_as_omission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf_path = tmp_path / "case.pdf"
    _text_pdf(pdf_path, ["alpha"])
    material = _material(pdf_path, "case", 1)
    original_get_text = pymupdf.Page.get_text

    def invalid_unit(page: pymupdf.Page, option: str = "text", *args, **kwargs):
        result = original_get_text(page, option, *args, **kwargs)
        if option == "dict":
            result["blocks"].append(
                {"type": 99, "bbox": [20.0, 20.0, 30.0, 30.0]}
            )
        return result

    monkeypatch.setattr(pymupdf.Page, "get_text", invalid_unit)
    row = analyze_material_native(
        _artifact(material),
        [_descriptor(material, pdf_path)],
    )["pages"][0]

    assert row["status"] == "partial"
    assert "layout_unit_kind_unsupported" in row["reasons"]
    assert len(row["layout_units"]) == 1
    omission = row["layout_unit_omissions"][0]
    assert omission["status"] == "omitted"
    assert omission["reason"] == "layout_unit_kind_unsupported"
    assert omission["identity"]["material_id"] == material["material_id"]


@pytest.mark.parametrize(
    "descriptors, reason",
    [
        (
            [
                {
                    "material_id": "wrong",
                    "case_id": "case",
                    "artifact_ref": "private:case",
                    "pdf_path": "case.pdf",
                }
            ],
            "source_mapping_identity_mismatch",
        ),
        (
            [
                {
                    "material_id": "material:case",
                    "case_id": "case",
                    "artifact_ref": "private:case",
                    "pdf_path": "one.pdf",
                },
                {
                    "material_id": "material:case",
                    "case_id": "case",
                    "artifact_ref": "private:case",
                    "pdf_path": "two.pdf",
                },
            ],
            "source_mapping_ambiguous",
        ),
    ],
)
def test_source_mapping_does_not_fallback_or_guess(
    tmp_path: Path,
    descriptors: list[dict],
    reason: str,
) -> None:
    pdf_path = tmp_path / "case.pdf"
    _text_pdf(pdf_path, ["alpha"])
    material = _material(pdf_path, "case", 1)
    for descriptor in descriptors:
        if descriptor["material_id"] == "material:case":
            descriptor["material_id"] = material["material_id"]

    result = analyze_material_native(_artifact(material), descriptors)

    assert result["pages"][0]["status"] == "failed"
    assert result["pages"][0]["reasons"] == [reason]
