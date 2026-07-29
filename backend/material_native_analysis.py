from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import pymupdf

from material_runtime_files import publish_runtime_json


SCHEMA_VERSION = "material-native-analysis/v2"
NATIVE_ANALYSIS_V1_STABLE_PATH = (
    ".studydy-runtime/materials/native-analysis/stable/"
    "material-native-analysis.v1.json"
)
NATIVE_ANALYSIS_STABLE_PATH = (
    ".studydy-runtime/materials/native-analysis/stable/"
    "material-native-analysis.v2.json"
)
# PDF 座標會有浮點誤差；允許 0.5 pt 可避免邊界元素被誤判為越界。
BBOX_MATCH_TOLERANCE_PT = 0.5


def analyze_material_native(
    material_blocks: Mapping[str, Any],
    source_descriptors: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """依 exact identity descriptor 分析 PDF。"""
    if material_blocks.get("schema_version") != "material-blocks/v1":
        raise ValueError("material_blocks_schema_mismatch")
    materials = material_blocks.get("materials")
    if not isinstance(materials, list):
        raise ValueError("materials_invalid")
    if not isinstance(source_descriptors, Sequence) or isinstance(
        source_descriptors,
        (str, bytes),
    ):
        raise ValueError("source_descriptors_invalid")

    source_paths, source_failures = _source_paths(
        materials,
        source_descriptors,
    )

    rows: list[dict[str, Any]] = []
    for material in materials:
        if not isinstance(material, Mapping):
            raise ValueError("material_invalid")
        identity = _material_identity(material)
        rows.extend(
            _analyze_material(
                material,
                source_paths.get(identity),
                source_failures.get(identity),
            )
        )

    rows.sort(key=lambda row: (row["material_id"], row["pdf_page"]))
    return {
        "schema_version": SCHEMA_VERSION,
        "page_count": len(rows),
        "pages": rows,
    }


def persist_material_native_analysis(
    artifact: Mapping[str, Any],
    *,
    repo_root: str | Path,
) -> None:
    publish_runtime_json(
        artifact,
        repo_root=repo_root,
        stable_path=NATIVE_ANALYSIS_STABLE_PATH,
    )


def _analyze_material(
    material: Mapping[str, Any],
    pdf_path: str | Path | None,
    source_failure_reason: str | None,
) -> list[dict[str, Any]]:
    """依序分析教材 block 對應的 PDF 原生頁面。"""
    # 讀取並驗證教材識別資訊與既有 blocks。
    material_id = material.get("material_id")
    case_id = material.get("case_id")
    artifact_ref = material.get("artifact_ref")
    blocks = material.get("blocks")
    if not isinstance(material_id, str) or not material_id:
        raise ValueError("material_id_missing")
    if not isinstance(case_id, str) or not case_id:
        raise ValueError("case_id_missing")
    if not isinstance(blocks, list):
        raise ValueError("blocks_invalid")

    # 只有 caller descriptor 的 exact identity mapping 可提供 PDF。
    if source_failure_reason is not None or pdf_path is None:
        return [
            _failed_row(
                material_id,
                case_id,
                artifact_ref,
                block,
                source_failure_reason or "source_mapping_missing",
            )
            for block in blocks
        ]

    try:
        document = pymupdf.open(Path(pdf_path))
    except Exception:
        return [
            _failed_row(
                material_id,
                case_id,
                artifact_ref,
                block,
                "document_unreadable",
            )
            for block in blocks
        ]

    try:
        # 依 block locator 載入對應頁面並執行原生結構分析。
        page_count_mismatch = document.page_count != len(blocks)
        rows = []
        for block in blocks:
            if not isinstance(block, Mapping):
                raise ValueError("block_invalid")
            locator = block.get("locator")
            if not isinstance(locator, Mapping):
                raise ValueError("locator_missing")
            pdf_page = locator.get("pdf_page")
            if not isinstance(pdf_page, int) or pdf_page < 1:
                raise ValueError("page_locator_missing")
            try:
                page = document.load_page(pdf_page - 1)
            except Exception:
                rows.append(
                    _failed_row(
                        material_id,
                        case_id,
                        artifact_ref,
                        block,
                        "page_unreadable",
                    )
                )
                continue
            row = _analyze_page(material_id, case_id, artifact_ref, block, page)
            # 頁數不一致時保留分析結果，但將狀態標記為 partial。
            if page_count_mismatch:
                row["status"] = "partial"
                row["reasons"] = sorted(
                    {*row["reasons"], "document_page_count_mismatch"}
                )
            rows.append(row)
        return rows
    finally:
        # 完成或中斷分析時都釋放 PDF 文件資源。
        document.close()


def _material_identity(
    material: Mapping[str, Any],
) -> tuple[str, str, str]:
    material_id = material.get("material_id")
    case_id = material.get("case_id")
    artifact_ref = material.get("artifact_ref")
    if not isinstance(material_id, str) or not material_id:
        raise ValueError("material_id_missing")
    if not isinstance(case_id, str) or not case_id:
        raise ValueError("case_id_missing")
    if not isinstance(artifact_ref, str) or not artifact_ref:
        raise ValueError("artifact_ref_missing")
    return material_id, case_id, artifact_ref


def _source_paths(
    materials: list[Any],
    descriptors: Sequence[Mapping[str, Any]],
) -> tuple[
    dict[tuple[str, str, str], str | Path],
    dict[tuple[str, str, str], str],
]:
    material_counts: dict[tuple[str, str, str], int] = {}
    for material in materials:
        if not isinstance(material, Mapping):
            raise ValueError("material_invalid")
        identity = _material_identity(material)
        material_counts[identity] = material_counts.get(identity, 0) + 1

    descriptor_rows: dict[
        tuple[str, str, str],
        list[str | Path],
    ] = {}
    invalid_descriptor = False
    for descriptor in descriptors:
        if not isinstance(descriptor, Mapping) or set(descriptor) != {
            "artifact_ref",
            "case_id",
            "material_id",
            "pdf_path",
        }:
            invalid_descriptor = True
            continue
        identity_values = (
            descriptor.get("material_id"),
            descriptor.get("case_id"),
            descriptor.get("artifact_ref"),
        )
        pdf_path = descriptor.get("pdf_path")
        if (
            not all(
                isinstance(value, str) and value
                for value in identity_values
            )
            or not isinstance(pdf_path, (str, Path))
            or not str(pdf_path)
        ):
            invalid_descriptor = True
            continue
        identity = (
            identity_values[0],
            identity_values[1],
            identity_values[2],
        )
        descriptor_rows.setdefault(identity, []).append(pdf_path)

    material_identities = set(material_counts)
    descriptor_identities = set(descriptor_rows)
    paths: dict[tuple[str, str, str], str | Path] = {}
    if invalid_descriptor:
        return paths, {
            identity: "source_mapping_invalid"
            for identity in material_identities
        }
    if descriptor_identities - material_identities:
        return paths, {
            identity: "source_mapping_identity_mismatch"
            for identity in material_identities
        }

    identities_by_path: dict[str, set[tuple[str, str, str]]] = {}
    for identity, descriptor_paths in descriptor_rows.items():
        for descriptor_path in descriptor_paths:
            identities_by_path.setdefault(
                str(Path(descriptor_path)),
                set(),
            ).add(identity)
    shared_paths = {
        identity
        for identities in identities_by_path.values()
        if len(identities) > 1
        for identity in identities
    }

    failures: dict[tuple[str, str, str], str] = {}
    for identity in material_identities:
        descriptor_paths = descriptor_rows.get(identity, [])
        if (
            material_counts[identity] != 1
            or len(descriptor_paths) > 1
            or identity in shared_paths
        ):
            failures[identity] = "source_mapping_ambiguous"
        elif not descriptor_paths:
            failures[identity] = "source_mapping_missing"
        else:
            paths[identity] = descriptor_paths[0]
    return paths, failures


def _provenance() -> dict[str, Any]:
    """記錄產生分析結果時使用的套件版本與 blocks 擷取策略。"""
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
    """整理頁面結果共用的教材、block、頁碼與來源識別資訊。"""
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
    """建立尚未累計資料的 bbox 統計結構。

    bbox（bounding box）是標示頁面物件位置與大小的矩形邊界框。
    """
    return {
        "total": 0,
        "valid": 0,
        "inside_page_tolerance": 0,
        "outside_page_tolerance": 0,
        "invalid": 0,
    }


def _initial_native_summary() -> dict[str, Any]:
    """建立 blocks 尚未分析時的預設摘要。"""
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
    """建立保留來源定位資訊的單頁失敗結果。"""
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
    """收集單頁原生結構證據與分析狀態。"""
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

    # 分析 PyMuPDF 提供的 blocks 結構。
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
    """將 PyMuPDF blocks 轉成不含 image payload 的最小 ordered units。"""
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
    if value == 0:
        return "text"
    if value == 1:
        return "image"
    return "unknown"


def _text_unit_content(
    block: Mapping[str, Any],
) -> tuple[str, dict[str, Any]]:
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
    """彙整文字與圖片 block 的數量及 bbox。"""
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
    """將有效且具面積的矩形轉成四個有限浮點座標。"""
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
    """統計 bbox 是否有效，以及是否落在允許的頁面誤差範圍內。"""
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
    """依原因是否影響分析完整性決定 success 或 partial。"""
    non_blocking = {"native_bbox_outside_page_tolerance"}
    return "partial" if any(reason not in non_blocking for reason in reasons) else "success"


def _bbox_reason_codes(summary: Mapping[str, Any]) -> list[str]:
    """將 blocks bbox 異常整理成頁面原因代碼。"""
    blocks_bboxes = summary["blocks"]["bboxes"]
    reasons = []
    if blocks_bboxes["invalid"]:
        reasons.append("native_bbox_invalid")
    if blocks_bboxes["outside_page_tolerance"]:
        reasons.append("native_bbox_outside_page_tolerance")
    return reasons
