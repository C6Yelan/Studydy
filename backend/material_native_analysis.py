from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import pymupdf

from material_runtime_files import publish_runtime_json


SCHEMA_VERSION = "material-native-analysis/v1"
NATIVE_ANALYSIS_STABLE_PATH = (
    ".studydy-runtime/materials/native-analysis/stable/"
    "material-native-analysis.v1.json"
)
# PDF 座標會有浮點誤差；允許 0.5 pt 可避免邊界元素被誤判為越界。
BBOX_MATCH_TOLERANCE_PT = 0.5


def analyze_material_native(
    material_blocks: Mapping[str, Any],
    pdf_paths: Mapping[str, str | Path],
) -> dict[str, Any]:
    """分析 PDF 原生結構，但不在結果中保留頁面內容。"""
    if material_blocks.get("schema_version") != "material-blocks/v1":
        raise ValueError("material_blocks_schema_mismatch")
    materials = material_blocks.get("materials")
    if not isinstance(materials, list):
        raise ValueError("materials_invalid")

    rows: list[dict[str, Any]] = []
    for material in materials:
        if not isinstance(material, Mapping):
            raise ValueError("material_invalid")
        rows.extend(_analyze_material(material, pdf_paths))

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
    pdf_paths: Mapping[str, str | Path],
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

    # 取得並開啟對應 PDF；無法使用時回傳各 block 的失敗結果。
    pdf_path = pdf_paths.get(case_id)
    if pdf_path is None:
        return [
            _failed_row(
                material_id,
                case_id,
                artifact_ref,
                block,
                "document_path_missing",
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


def _provenance() -> dict[str, Any]:
    """記錄產生分析結果時使用的套件版本與 blocks 擷取策略。"""
    return {
        "library": "PyMuPDF",
        "library_version": pymupdf.VersionBind,
        "native_policy": "blocks:sort-true-v1",
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
    page_bbox = _rect_values(page.rect)
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

    reasons.extend(_bbox_reason_codes(summary))

    identity = _identity(material_id, case_id, artifact_ref, block)
    if identity["source_ref"] is None:
        reasons.append("source_ref_missing")

    return {
        **identity,
        "page_bbox": page_bbox,
        "provenance": _provenance(),
        "native_summary": summary,
        "status": _status_for_reasons(reasons),
        "reasons": sorted(set(reasons)),
    }


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
