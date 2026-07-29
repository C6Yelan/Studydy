from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from material_runtime_files import publish_runtime_json, resolve_runtime_path


# material_lexical_index still imports this legacy contract constant.
SCHEMA_VERSION = "normalized-material-blocks/v1"
STRUCTURED_SCHEMA_VERSION = "normalized-material-blocks/v2"
MATERIAL_BLOCKS_STABLE_PATH = (
    ".studydy-runtime/materials/blocks/stable/material-blocks.v1.json"
)
NATIVE_ANALYSIS_STABLE_PATH = (
    ".studydy-runtime/materials/native-analysis/stable/"
    "material-native-analysis.v2.json"
)
NORMALIZED_BLOCKS_STABLE_PATH = (
    ".studydy-runtime/materials/normalized-blocks/stable/"
    "normalized-material-blocks.v1.json"
)
NORMALIZED_BLOCKS_V2_STABLE_PATH = (
    ".studydy-runtime/materials/normalized-blocks/stable/"
    "normalized-material-blocks.v2.json"
)
IDENTITY_FIELDS = (
    "material_id",
    "case_id",
    "artifact_ref",
    "block_id",
    "pdf_page",
    "source_ref",
)
UNIT_OMISSION_PARTIAL_REASONS = {
    "layout_unit_bbox_outside_page",
    "layout_unit_invalid",
    "layout_unit_kind_unsupported",
    "layout_unit_text_empty",
    "native_bbox_invalid",
    "native_bbox_outside_page_tolerance",
}


def load_and_normalize_material_blocks(
    *,
    repo_root: str | Path,
) -> dict[str, Any]:
    """從 stable inputs 載入資料並執行正規化。"""
    blocks = _read_stable_json(repo_root, MATERIAL_BLOCKS_STABLE_PATH)
    native = _read_stable_json(repo_root, NATIVE_ANALYSIS_STABLE_PATH)
    return _normalize_material_blocks(blocks, native)


def normalize_material_blocks(
    material_blocks: Mapping[str, Any],
    native_analysis: Mapping[str, Any],
) -> dict[str, Any]:
    """正規化已載入的教材 blocks 與原生分析結果。"""
    return _normalize_material_blocks(material_blocks, native_analysis)


def _normalize_material_blocks(
    material_blocks: Mapping[str, Any],
    native_analysis: Mapping[str, Any],
) -> dict[str, Any]:
    """執行共用的粗粒度輸入檢查與正規化。"""
    if material_blocks.get("schema_version") != "material-blocks/v1":
        raise ValueError("material_blocks_schema_mismatch")
    if native_analysis.get("schema_version") != "material-native-analysis/v2":
        raise ValueError("native_analysis_schema_mismatch")
    materials = material_blocks.get("materials")
    pages = native_analysis.get("pages")
    if not isinstance(materials, list):
        raise ValueError("materials_invalid")
    if not isinstance(pages, list):
        raise ValueError("native_analysis_pages_invalid")
    if native_analysis.get("page_count") != len(pages):
        raise ValueError("native_analysis_page_count_invalid")

    baseline_rows = _baseline_index(materials)
    output_materials: dict[
        tuple[str | None, str | None, str | None], list[dict[str, Any]]
    ] = defaultdict(list)
    has_selected = False
    has_failed = False
    has_omissions = False

    for raw_row in pages:
        row = raw_row if isinstance(raw_row, Mapping) else {}
        identity = _available_identity(row)
        identity_tuple = _identity_tuple(row)
        validation_reasons = _row_validation_reasons(row)
        matches = baseline_rows.get(identity_tuple, [])
        if len(matches) != 1:
            validation_reasons.append("identity_join_invalid")

        baseline = matches[0] if len(matches) == 1 else None
        if baseline is not None and not _baseline_usable(baseline):
            validation_reasons.append("source_block_invalid")

        native_analysis_status = (
            row.get("status") if isinstance(row.get("status"), str) else None
        )
        native_analysis_reasons = _string_list(row.get("reasons"))
        omissions = row.get("layout_unit_omissions")
        valid_omissions = _valid_layout_unit_omissions(omissions, row)
        omission_rows = omissions if valid_omissions else []
        ordered_omission_rows = sorted(
            deepcopy(omission_rows),
            key=lambda omission: omission["locator"]["omission_order"],
        )
        omission_partial = (
            native_analysis_status == "partial"
            and bool(omission_rows)
            and set(native_analysis_reasons).issubset(
                UNIT_OMISSION_PARTIAL_REASONS
            )
        )
        warnings = (
            sorted(set(native_analysis_reasons))
            if native_analysis_status == "partial"
            and (
                omission_partial
                or set(native_analysis_reasons)
                == {"native_bbox_invalid"}
            )
            else []
        )
        # 只要仍有 validated units，unit omission 不阻斷同頁其餘內容。
        selected = not validation_reasons and (
            native_analysis_status == "success"
            or (
                native_analysis_status == "partial"
                and (
                    omission_partial
                    or set(native_analysis_reasons)
                    == {"native_bbox_invalid"}
                )
            )
        )
        if not selected and not validation_reasons:
            validation_reasons.append("selection_failed")
        reasons = sorted(set([*native_analysis_reasons, *validation_reasons]))

        page_bbox = row.get("page_bbox")
        block = {
            **identity,
            "locator": {
                "pdf_page": identity["pdf_page"],
                "source_ref": identity["source_ref"],
            },
            "page_bbox": page_bbox if _valid_page_bbox(page_bbox) else None,
            "provenance": {
                "native_analysis": row.get("provenance"),
            },
            "native_analysis_status": native_analysis_status,
            "selection_status": "selected" if selected else "failed",
            "reasons": reasons,
            "warnings": warnings,
        }
        if selected and baseline is not None:
            has_selected = True
            block["layout_units"] = sorted(
                deepcopy(row["layout_units"]),
                key=lambda unit: unit["reading_order"],
            )
            block["layout_unit_omissions"] = ordered_omission_rows
            if omission_rows:
                has_omissions = True
            if native_analysis_status == "partial":
                block["selection_reason"] = (
                    "layout_unit_omissions_present"
                    if omission_rows
                    else "native_bbox_invalid"
                )
        else:
            has_failed = True
            if (
                omission_rows
                and validation_reasons
                == ["native_layout_units_invalid"]
            ):
                block["layout_unit_omissions"] = ordered_omission_rows

        material_key = (
            identity["material_id"],
            identity["case_id"],
            identity["artifact_ref"],
        )
        output_materials[material_key].append(block)

    normalized_materials = []
    for material_key, blocks in output_materials.items():
        blocks.sort(
            key=lambda block: (
                _sort_value(block["locator"]["pdf_page"]),
                _sort_value(block["block_id"]),
            )
        )
        normalized_materials.append(
            {
                "material_id": material_key[0],
                "case_id": material_key[1],
                "artifact_ref": material_key[2],
                "blocks": blocks,
            }
        )
    normalized_materials.sort(
        key=lambda material: (
            _sort_value(material["material_id"]),
            _sort_value(material["case_id"]),
        )
    )
    status = (
        "success"
        if not has_failed and not has_omissions
        else ("partial" if has_selected else "failed")
    )
    return {
        "schema_version": STRUCTURED_SCHEMA_VERSION,
        "status": status,
        "source_provenance": {
            "material_blocks": {
                "schema_version": material_blocks["schema_version"],
                "parser_provenance": material_blocks.get("parser_provenance"),
            },
            "native_analysis": {
                "schema_version": native_analysis["schema_version"],
            },
        },
        "materials": normalized_materials,
    }


def persist_normalized_material_blocks(
    artifact: Mapping[str, Any],
    *,
    repo_root: str | Path,
) -> None:
    """將正規化結果發布到 repo 內固定的 stable runtime 路徑。"""
    publish_runtime_json(
        artifact,
        repo_root=repo_root,
        stable_path=NORMALIZED_BLOCKS_V2_STABLE_PATH,
    )


def _read_stable_json(
    repo_root: str | Path,
    relative_path: str,
) -> dict[str, Any]:
    """讀取 stable JSON 並驗證根節點為 object。"""
    path = resolve_runtime_path(repo_root, relative_path)
    raw = path.read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("runtime_json_root_invalid")
    return value


def _baseline_index(
    materials: Sequence[Any],
) -> dict[tuple[Any, ...], list[dict[str, Any]]]:
    """依完整 identity 建立索引；刻意保留重複項目供 join 驗證拒絕。"""
    result: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for raw_material in materials:
        if not isinstance(raw_material, Mapping):
            continue
        blocks = raw_material.get("blocks")
        if not isinstance(blocks, list):
            continue
        for raw_block in blocks:
            if not isinstance(raw_block, Mapping):
                continue
            locator = raw_block.get("locator")
            locator = locator if isinstance(locator, Mapping) else {}
            row = {
                "material_id": raw_material.get("material_id"),
                "case_id": raw_material.get("case_id"),
                "artifact_ref": raw_material.get("artifact_ref"),
                "block_id": raw_block.get("block_id"),
                "pdf_page": locator.get("pdf_page"),
                "source_ref": locator.get("source_ref"),
                "text": raw_block.get("text"),
                "parser_status": raw_block.get("parser_status"),
            }
            identity = _identity_tuple(row)
            result[identity].append(row)
    return result


def _identity_tuple(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(row.get(field) for field in IDENTITY_FIELDS)


def _available_identity(row: Mapping[str, Any]) -> dict[str, Any]:
    return {field: row.get(field) for field in IDENTITY_FIELDS}


def _row_validation_reasons(row: Mapping[str, Any]) -> list[str]:
    """檢查跨 artifact 配對前不可缺少的 identity、定位與狀態欄位。"""
    identity_invalid = any(
        not isinstance(row.get(field), str) or not row.get(field)
        for field in IDENTITY_FIELDS
        if field != "pdf_page"
    )
    pdf_page = row.get("pdf_page")
    if not isinstance(pdf_page, int) or isinstance(pdf_page, bool) or pdf_page < 1:
        identity_invalid = True
    bbox_invalid = not _valid_page_bbox(row.get("page_bbox"))
    provenance = row.get("provenance")
    provenance_invalid = not isinstance(provenance, Mapping) or not provenance
    status = row.get("status")
    status_invalid = status not in {"success", "partial", "failed"}
    row_reasons = row.get("reasons")
    reasons_invalid = (
        not isinstance(row_reasons, list)
        or not all(isinstance(reason, str) and reason for reason in row_reasons)
        or (status in {"partial", "failed"} and not row_reasons)
    )
    if any(
        (
            identity_invalid,
            bbox_invalid,
            provenance_invalid,
            status_invalid,
            reasons_invalid,
        )
    ):
        return ["native_analysis_row_invalid"]
    validation_reasons: list[str] = []
    if status in {"success", "partial"}:
        if not _valid_layout_units(
            row.get("layout_units"),
            row.get("block_id"),
            row.get("page_bbox"),
        ):
            validation_reasons.append("native_layout_units_invalid")
        omissions = row.get("layout_unit_omissions")
        omissions_valid = _valid_layout_unit_omissions(omissions, row)
        if not omissions_valid:
            validation_reasons.append(
                "native_layout_unit_omissions_invalid"
            )
        elif omissions and status != "partial":
            validation_reasons.append(
                "native_layout_unit_omissions_status_invalid"
            )
    return validation_reasons


def _baseline_usable(row: Mapping[str, Any]) -> bool:
    """確認來源 block 已成功解析且有文字，才可提供正規化內容。"""
    return (
        row.get("parser_status") == "success"
        and isinstance(row.get("text"), str)
        and bool(row["text"])
    )


def _string_list(value: Any) -> list[str]:
    """只保留輸入 list 中的非空字串原因代碼。"""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _valid_page_bbox(value: Any) -> bool:
    """確認 page bbox 由四個有限數值組成，且不將布林值視為座標。"""
    return (
        isinstance(value, list)
        and len(value) == 4
        and all(
            isinstance(coordinate, (int, float))
            and not isinstance(coordinate, bool)
            and math.isfinite(coordinate)
            for coordinate in value
        )
        and value[2] > value[0]
        and value[3] > value[1]
    )


def _valid_layout_units(
    value: Any,
    block_id: Any,
    page_bbox: Any,
) -> bool:
    """確認單頁版面單元的 ID、順序、內容與位置都符合所屬 block 和頁面。"""
    if not isinstance(value, list) or not value:
        return False
    if not isinstance(block_id, str) or not block_id:
        return False
    if not _valid_page_bbox(page_bbox):
        return False
    unit_ids: set[str] = set()
    reading_orders: list[int] = []
    for unit in value:
        if not isinstance(unit, Mapping):
            return False
        unit_id = unit.get("layout_unit_id")
        reading_order = unit.get("reading_order")
        bbox = unit.get("bbox")
        kind = unit.get("kind")
        if not isinstance(unit_id, str) or not unit_id or unit_id in unit_ids:
            return False
        if (
            not isinstance(reading_order, int)
            or isinstance(reading_order, bool)
            or reading_order < 0
        ):
            return False
        if unit.get("parent_block_id") != block_id:
            return False
        if not _valid_page_bbox(bbox) or not _bbox_inside_page(bbox, page_bbox):
            return False
        if kind == "text":
            if not isinstance(unit.get("text"), str) or not unit["text"].strip():
                return False
            if not _valid_style_summary(unit.get("style_summary")):
                return False
        elif kind == "image":
            if "text" in unit or "style_summary" in unit:
                return False
        else:
            return False
        unit_ids.add(unit_id)
        reading_orders.append(reading_order)
    return sorted(reading_orders) == list(range(len(value)))


def _valid_layout_unit_omissions(
    value: Any,
    row: Mapping[str, Any],
) -> bool:
    """確認每筆省略紀錄保留頁面身分、可追查原因與連續順序。"""
    if not isinstance(value, list):
        return False
    expected_identity = {
        field: row.get(field) for field in IDENTITY_FIELDS
    }
    omission_ids: set[str] = set()
    omission_orders: list[int] = []
    for omission in value:
        if not isinstance(omission, Mapping) or set(omission) != {
            "identity",
            "kind",
            "layout_unit_id",
            "locator",
            "provenance",
            "reason",
            "status",
        }:
            return False
        omission_id = omission.get("layout_unit_id")
        locator = omission.get("locator")
        provenance = omission.get("provenance")
        identity = omission.get("identity")
        if (
            not isinstance(omission_id, str)
            or not omission_id
            or omission_id in omission_ids
            or omission.get("kind") not in {"text", "image", "unknown"}
            or omission.get("status") != "omitted"
            or not isinstance(omission.get("reason"), str)
            or not omission["reason"]
            or identity != expected_identity
            or not isinstance(locator, Mapping)
            or set(locator) != {"bbox", "omission_order"}
            or not isinstance(provenance, Mapping)
            or not provenance
            or omission["reason"] not in row.get("reasons", [])
        ):
            return False
        bbox = locator.get("bbox")
        omission_order = locator.get("omission_order")
        if bbox is not None and not _valid_page_bbox(bbox):
            return False
        if (
            not isinstance(omission_order, int)
            or isinstance(omission_order, bool)
            or omission_order < 0
        ):
            return False
        omission_ids.add(omission_id)
        omission_orders.append(omission_order)
    return sorted(omission_orders) == list(range(len(value)))


def _bbox_inside_page(bbox: list[Any], page_bbox: list[Any]) -> bool:
    """以原生分析相同的 0.5 pt 誤差判斷位置框是否仍在頁面範圍內。"""
    tolerance = 0.5
    return (
        bbox[0] >= page_bbox[0] - tolerance
        and bbox[1] >= page_bbox[1] - tolerance
        and bbox[2] <= page_bbox[2] + tolerance
        and bbox[3] <= page_bbox[3] + tolerance
    )


def _valid_style_summary(value: Any) -> bool:
    """確認文字樣式摘要的欄位完整，且字型、行數與字級都可安全使用。"""
    if not isinstance(value, Mapping):
        return False
    if set(value) != {
        "bold",
        "font_names",
        "font_size_max",
        "font_size_min",
        "line_count",
        "monospace",
    }:
        return False
    if not isinstance(value.get("bold"), bool):
        return False
    if not isinstance(value.get("monospace"), bool):
        return False
    fonts = value.get("font_names")
    if not isinstance(fonts, list) or not all(
        isinstance(font, str) and font for font in fonts
    ):
        return False
    line_count = value.get("line_count")
    if (
        not isinstance(line_count, int)
        or isinstance(line_count, bool)
        or line_count < 1
    ):
        return False
    for field in ("font_size_min", "font_size_max"):
        size = value.get(field)
        if size is not None and (
            not isinstance(size, (int, float))
            or isinstance(size, bool)
            or not math.isfinite(size)
            or size <= 0
        ):
            return False
    minimum = value["font_size_min"]
    maximum = value["font_size_max"]
    if (minimum is None) != (maximum is None):
        return False
    return minimum is None or maximum >= minimum


def _sort_value(value: Any) -> tuple[int, int | str]:
    """將頁碼、字串與缺失值轉成可穩定比較的排序鍵。"""
    if isinstance(value, int) and not isinstance(value, bool):
        return (0, value)
    if isinstance(value, str):
        return (1, value)
    return (2, "")
