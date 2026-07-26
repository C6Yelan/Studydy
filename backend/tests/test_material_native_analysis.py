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
    assert first["schema_version"] == "material-native-analysis/v1"
    assert first["page_count"] == 3
    assert [
        (row["case_id"], row["pdf_page"]) for row in first["pages"]
    ] == [("earlier", 1), ("later", 1), ("later", 2)]

    # 原生分析只輸出結構摘要，不應夾帶教材文字、影像或語意內容。
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
        assert set(row) == {
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
        x0, y0, x1, y1 = row["page_bbox"]
        assert all(math.isfinite(value) for value in row["page_bbox"])
        assert x1 > x0 and y1 > y0
        assert row["provenance"]["bbox_tolerance_points"] == BBOX_MATCH_TOLERANCE_PT
        assert row["provenance"]["native_policy"] == "blocks:sort-true-v1"
        assert row["block_id"]
        assert row["source_ref"] == f"slide:{row['pdf_page']}"
        assert set(row["native_summary"]) == {"blocks"}
        assert row["native_summary"]["blocks"]["available"] is True
        assert row["native_summary"]["blocks"]["text_blocks"] == 1


def test_missing_document_and_blocks_failure_keep_rows_and_reason_codes(
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
    partial = analyze_material_native(_artifact(material), {"case": pdf_path})

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
    row = analyze_material_native(_artifact(material), {"case": pdf_path})["pages"][0]

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

    result = analyze_material_native(_artifact(material), {"case": changed_pdf})

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
    row = analyze_material_native(_artifact(material), {"case": pdf_path})["pages"][0]

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
        {"structured": pdf_path},
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
        analyze_material_native(source, {})


def test_persist_native_analysis_uses_stable_runtime_path(tmp_path: Path) -> None:
    pdf_path = tmp_path / "case.pdf"
    _text_pdf(pdf_path, ["alpha"])
    material = _material(pdf_path, "case", 1)
    artifact = analyze_material_native(_artifact(material), {"case": pdf_path})

    persist_material_native_analysis(artifact, repo_root=tmp_path)

    stable = tmp_path / NATIVE_ANALYSIS_STABLE_PATH
    assert stable.read_bytes() == canonical_json_bytes(artifact)
    assert not list((tmp_path / ".studydy-runtime").rglob("*.tmp"))
