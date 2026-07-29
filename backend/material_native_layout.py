from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping, Sequence

import pymupdf


BBOX_MATCH_TOLERANCE_PT = 0.5


def _provenance() -> dict[str, Any]:
    """回傳這次分析使用的 PyMuPDF 版本、座標系統與版面處理規則。"""
    return {
        "library": "PyMuPDF",
        "library_version": pymupdf.VersionBind,
        "native_policy": "dict-layout-units:sort-true-v2",
        "source_mapping_policy": "caller-exact-identity-descriptor-v1",
        "coordinate_space": "pymupdf-unrotated-page-v1",
        "bbox_tolerance_points": BBOX_MATCH_TOLERANCE_PT,
    }


def _identity(
    material_id: str,
    case_id: str,
    artifact_ref: Any,
    block: Mapping[str, Any],
) -> dict[str, Any]:
    """整理每頁結果都需要的教材 ID、block ID、頁碼與來源位置。"""
    locator = block.get("locator")
    if not isinstance(locator, Mapping):
        locator = {}

    artifact_ref_value = artifact_ref if isinstance(artifact_ref, str) else None

    block_id = block.get("block_id")
    if not isinstance(block_id, str):
        block_id = None

    pdf_page = locator.get("pdf_page")
    if not isinstance(pdf_page, int):
        pdf_page = None

    source_ref = locator.get("source_ref")
    if not isinstance(source_ref, str):
        source_ref = None

    return {
        "material_id": material_id,
        "case_id": case_id,
        "artifact_ref": artifact_ref_value,
        "block_id": block_id,
        "pdf_page": pdf_page,
        "source_ref": source_ref,
    }


def _initial_bbox_summary() -> dict[str, int]:
    """建立尚未加入任何頁面物件時使用的位置框統計。

    bbox 是用四個座標標示文字或圖片位置與大小的矩形框。
    """
    return {
        "total": 0,
        "valid": 0,
        "inside_page_tolerance": 0,
        "outside_page_tolerance": 0,
        "invalid": 0,
    }


def _initial_native_summary() -> dict[str, Any]:
    """建立某一頁還沒讀取任何文字或圖片區塊時使用的空白摘要。"""
    return {
        "blocks": {
            "available": False,
            "count": 0,
            "text_blocks": 0,
            "image_blocks": 0,
            "bboxes": _initial_bbox_summary(),
        },
    }


def _failed_row(
    material_id: str,
    case_id: str,
    artifact_ref: Any,
    block: Mapping[str, Any],
    reason: str,
) -> dict[str, Any]:
    """建立單頁失敗結果，保留教材、頁碼與來源位置，方便之後回查。"""
    return {
        **_identity(material_id, case_id, artifact_ref, block),
        "page_bbox": None,
        "provenance": _provenance(),
        "native_summary": _initial_native_summary(),
        "status": "failed",
        "reasons": [reason],
    }


def _analyze_page(
    material_id: str,
    case_id: str,
    artifact_ref: Any,
    block: Mapping[str, Any],
    page: pymupdf.Page,
) -> dict[str, Any]:
    """讀取一頁 PDF 的文字、圖片與位置資料，整理分析結果、狀態及原因。"""
    page_bbox = _rect_values(page.rect * page.derotation_matrix)
    if page_bbox is None:
        return _failed_row(
            material_id,
            case_id,
            artifact_ref,
            block,
            "page_bbox_invalid",
        )

    reasons: list[str] = []
    summary = _initial_native_summary()

    try:
        blocks = page.get_text("blocks", sort=True)
    except Exception:
        reasons.append("blocks_analysis_failed")
    else:
        _summarize_blocks(blocks, summary["blocks"], page_bbox)

    layout_units: list[dict[str, Any]] = []
    layout_unit_omissions: list[dict[str, Any]] = []
    try:
        structured = page.get_text("dict", sort=True)
        (
            layout_units,
            layout_unit_omissions,
            layout_reasons,
        ) = _layout_units(
            structured,
            _identity(material_id, case_id, artifact_ref, block),
            page_bbox,
        )
        reasons.extend(layout_reasons)
    except Exception:
        reasons.append("layout_units_analysis_failed")

    reasons.extend(_bbox_reason_codes(summary))

    identity = _identity(material_id, case_id, artifact_ref, block)
    if identity["source_ref"] is None:
        reasons.append("source_ref_missing")

    return {
        **identity,
        "layout_unit_omissions": layout_unit_omissions,
        "layout_units": layout_units,
        "page_bbox": page_bbox,
        "provenance": _provenance(),
        "native_summary": summary,
        "status": _status_for_reasons(reasons),
        "reasons": sorted(set(reasons)),
    }


def _layout_units(
    structured: Any,
    identity: Mapping[str, Any],
    page_bbox: list[float],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[str],
]:
    """把頁面區塊整理成有閱讀順序的文字或圖片單元，並記錄被省略的無效單元。"""
    if not isinstance(structured, Mapping):
        raise ValueError("layout_units_invalid")
    blocks = structured.get("blocks")
    if not isinstance(blocks, list):
        raise ValueError("layout_units_invalid")

    raw_units: list[dict[str, Any]] = []
    raw_omissions: list[dict[str, Any]] = []
    reasons: list[str] = []
    page = pymupdf.Rect(page_bbox)
    tolerance = BBOX_MATCH_TOLERANCE_PT
    for source_order, block in enumerate(blocks):
        if not isinstance(block, Mapping):
            reasons.append("layout_unit_invalid")
            raw_omissions.append(
                {
                    "bbox": None,
                    "kind": "unknown",
                    "reason": "layout_unit_invalid",
                    "source_order": source_order,
                }
            )
            continue
        bbox = _rect_values(block.get("bbox"))
        if bbox is None:
            reasons.append("layout_unit_invalid")
            raw_omissions.append(
                {
                    "bbox": None,
                    "kind": _layout_unit_kind(block.get("type")),
                    "reason": "layout_unit_invalid",
                    "source_order": source_order,
                }
            )
            continue
        block_type = block.get("type")
        kind = _layout_unit_kind(block_type)
        if kind == "unknown":
            reasons.append("layout_unit_kind_unsupported")
            raw_omissions.append(
                {
                    "bbox": bbox,
                    "kind": kind,
                    "reason": "layout_unit_kind_unsupported",
                    "source_order": source_order,
                }
            )
            continue
        rect = pymupdf.Rect(bbox)
        if (
            rect.x0 < page.x0 - tolerance
            or rect.y0 < page.y0 - tolerance
            or rect.x1 > page.x1 + tolerance
            or rect.y1 > page.y1 + tolerance
        ):
            reasons.append("layout_unit_bbox_outside_page")
            raw_omissions.append(
                {
                    "bbox": bbox,
                    "kind": kind,
                    "reason": "layout_unit_bbox_outside_page",
                    "source_order": source_order,
                }
            )
            continue
        if block_type == 0:
            try:
                text, style_summary = _text_unit_content(block)
            except ValueError:
                reasons.append("layout_unit_invalid")
                raw_omissions.append(
                    {
                        "bbox": bbox,
                        "kind": kind,
                        "reason": "layout_unit_invalid",
                        "source_order": source_order,
                    }
                )
                continue
            if not text:
                reasons.append("layout_unit_text_empty")
                raw_omissions.append(
                    {
                        "bbox": bbox,
                        "kind": kind,
                        "reason": "layout_unit_text_empty",
                        "source_order": source_order,
                    }
                )
                continue
            raw_units.append(
                {
                    "bbox": bbox,
                    "kind": "text",
                    "parent_block_id": identity.get("block_id"),
                    "source_order": source_order,
                    "style_summary": style_summary,
                    "text": text,
                }
            )
        else:
            raw_units.append(
                {
                    "bbox": bbox,
                    "kind": "image",
                    "parent_block_id": identity.get("block_id"),
                    "source_order": source_order,
                }
            )

    raw_units.sort(
        key=lambda unit: (
            unit["bbox"][1],
            unit["bbox"][0],
            unit["bbox"][3],
            unit["bbox"][2],
            unit["kind"],
            unit.get("text", ""),
            json.dumps(
                unit.get("style_summary"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            unit["source_order"],
        )
    )
    units = []
    for reading_order, raw_unit in enumerate(raw_units):
        unit = {
            key: value
            for key, value in raw_unit.items()
            if key != "source_order"
        }
        unit["reading_order"] = reading_order
        unit["layout_unit_id"] = _layout_unit_id(
            identity,
            reading_order,
            unit,
        )
        units.append(unit)

    raw_omissions.sort(
        key=lambda omission: (
            omission["bbox"] is None,
            omission["bbox"] or [],
            omission["kind"],
            omission["reason"],
            omission["source_order"],
        )
    )
    omissions = []
    for omission_order, raw_omission in enumerate(raw_omissions):
        omission = {
            "identity": dict(identity),
            "kind": raw_omission["kind"],
            "locator": {
                "bbox": raw_omission["bbox"],
                "omission_order": omission_order,
            },
            "provenance": {
                "coordinate_space": "pymupdf-unrotated-page-v1",
                "native_policy": "dict-layout-units:sort-true-v2",
            },
            "reason": raw_omission["reason"],
            "status": "omitted",
        }
        omission["layout_unit_id"] = _layout_unit_omission_id(omission)
        omissions.append(omission)
    return units, omissions, sorted(set(reasons))


def _layout_unit_kind(value: Any) -> str:
    """把 PyMuPDF 的區塊編號轉成 text、image 或 unknown。"""
    if value == 0:
        return "text"
    if value == 1:
        return "image"
    return "unknown"


def _text_unit_content(
    block: Mapping[str, Any],
) -> tuple[str, dict[str, Any]]:
    """合併區塊內每行文字，並整理字型、字級、粗體與等寬字等樣式。"""
    lines = block.get("lines")
    if not isinstance(lines, list):
        raise ValueError("layout_unit_lines_invalid")
    output_lines: list[str] = []
    font_names: set[str] = set()
    font_sizes: list[float] = []
    bold = False
    monospace = False
    for line in lines:
        if not isinstance(line, Mapping):
            raise ValueError("layout_unit_line_invalid")
        spans = line.get("spans")
        if not isinstance(spans, list):
            raise ValueError("layout_unit_spans_invalid")
        line_parts = []
        for span in spans:
            if not isinstance(span, Mapping):
                raise ValueError("layout_unit_span_invalid")
            span_text = span.get("text")
            if isinstance(span_text, str):
                line_parts.append(span_text)
            font = span.get("font")
            if isinstance(font, str) and font:
                font_names.add(font)
            size = span.get("size")
            if (
                isinstance(size, (int, float))
                and not isinstance(size, bool)
                and math.isfinite(size)
                and size > 0
            ):
                font_sizes.append(float(size))
            flags = span.get("flags")
            if isinstance(flags, int) and not isinstance(flags, bool):
                bold = bold or bool(flags & 16)
                monospace = monospace or bool(flags & 8)
        output_lines.append("".join(line_parts).strip())
    text = "\n".join(line for line in output_lines if line)
    return text, {
        "bold": bold,
        "font_names": sorted(font_names),
        "font_size_max": round(max(font_sizes), 3) if font_sizes else None,
        "font_size_min": round(min(font_sizes), 3) if font_sizes else None,
        "line_count": len([line for line in output_lines if line]),
        "monospace": monospace,
    }


def _layout_unit_id(
    identity: Mapping[str, Any],
    reading_order: int,
    unit: Mapping[str, Any],
) -> str:
    """根據教材、來源位置與閱讀順序，為相同的版面單元產生固定 ID。"""
    payload = {
        "artifact_ref": identity.get("artifact_ref"),
        "bbox": unit["bbox"],
        "block_id": identity.get("block_id"),
        "case_id": identity.get("case_id"),
        "kind": unit["kind"],
        "material_id": identity.get("material_id"),
        "pdf_page": identity.get("pdf_page"),
        "reading_order": reading_order,
        "source_ref": identity.get("source_ref"),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"layout-unit:{hashlib.sha256(encoded).hexdigest()[:24]}"


def _layout_unit_omission_id(omission: Mapping[str, Any]) -> str:
    """根據來源位置與省略原因，為相同的省略記錄產生固定 ID。"""
    payload = {
        "identity": omission["identity"],
        "kind": omission["kind"],
        "locator": omission["locator"],
        "reason": omission["reason"],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return (
        "layout-unit-omission:"
        f"{hashlib.sha256(encoded).hexdigest()[:24]}"
    )


def _summarize_blocks(
    blocks: Any,
    output: dict[str, Any],
    page_bbox: list[float],
) -> None:
    """計算一頁有多少文字與圖片區塊，並統計它們的位置框是否有效。"""
    if not isinstance(blocks, list):
        raise ValueError("blocks_invalid")
    bboxes = []
    text_blocks = 0
    image_blocks = 0
    for block in blocks:
        if not isinstance(block, Sequence) or len(block) < 7:
            continue
        bboxes.append(block[:4])
        if block[6] == 0:
            text_blocks += 1
        elif block[6] == 1:
            image_blocks += 1
    output.update(
        {
            "available": True,
            "count": len(blocks),
            "text_blocks": text_blocks,
            "image_blocks": image_blocks,
            "bboxes": _bbox_summary(bboxes, page_bbox),
        }
    )


def _rect_values(value: Any) -> list[float] | None:
    """把有效矩形轉成四個座標；格式錯誤、無限值或沒有面積時回傳 None。"""
    try:
        rect = pymupdf.Rect(value)
    except Exception:
        return None
    coordinates = [float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)]
    if not all(math.isfinite(coordinate) for coordinate in coordinates):
        return None
    if rect.width <= 0 or rect.height <= 0:
        return None
    return coordinates


def _bbox_summary(
    values: Sequence[Any],
    page_bbox: list[float],
) -> dict[str, int]:
    """逐一檢查位置框，統計有效、無效、頁面內與超出頁面的數量。"""
    result = _initial_bbox_summary()
    result["total"] = len(values)
    page = pymupdf.Rect(page_bbox)
    tolerance = BBOX_MATCH_TOLERANCE_PT
    for value in values:
        bbox = _rect_values(value)
        if bbox is None:
            result["invalid"] += 1
            continue
        result["valid"] += 1
        rect = pymupdf.Rect(bbox)
        if (
            rect.x0 >= page.x0 - tolerance
            and rect.y0 >= page.y0 - tolerance
            and rect.x1 <= page.x1 + tolerance
            and rect.y1 <= page.y1 + tolerance
        ):
            result["inside_page_tolerance"] += 1
        else:
            result["outside_page_tolerance"] += 1
    return result


def _status_for_reasons(reasons: Sequence[str]) -> str:
    """有影響完整性的問題時回傳 partial，否則回傳 success。"""
    non_blocking = {"native_bbox_outside_page_tolerance"}
    return "partial" if any(reason not in non_blocking for reason in reasons) else "success"


def _bbox_reason_codes(summary: Mapping[str, Any]) -> list[str]:
    """根據位置框統計結果，列出格式無效或超出頁面的原因代碼。"""
    blocks_bboxes = summary["blocks"]["bboxes"]
    reasons = []
    if blocks_bboxes["invalid"]:
        reasons.append("native_bbox_invalid")
    if blocks_bboxes["outside_page_tolerance"]:
        reasons.append("native_bbox_outside_page_tolerance")
    return reasons
