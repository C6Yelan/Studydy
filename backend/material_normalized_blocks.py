from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from material_runtime_files import publish_runtime_json, resolve_runtime_path


SCHEMA_VERSION = "normalized-material-blocks/v1"
MATERIAL_BLOCKS_STABLE_PATH = (
    ".studydy-runtime/materials/blocks/stable/material-blocks.v1.json"
)
NATIVE_ANALYSIS_STABLE_PATH = (
    ".studydy-runtime/materials/native-analysis/stable/"
    "material-native-analysis.v1.json"
)
NORMALIZED_BLOCKS_STABLE_PATH = (
    ".studydy-runtime/materials/normalized-blocks/stable/"
    "normalized-material-blocks.v1.json"
)
IDENTITY_FIELDS = (
    "material_id",
    "case_id",
    "artifact_ref",
    "block_id",
    "pdf_page",
    "source_ref",
)


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
    if native_analysis.get("schema_version") != "material-native-analysis/v1":
        raise ValueError("native_analysis_schema_mismatch")
    materials = material_blocks.get("materials")
    pages = native_analysis.get("pages")
    if not isinstance(materials, list):
        raise ValueError("materials_invalid")
    if not isinstance(pages, list):
        raise ValueError("native_analysis_pages_invalid")
    if native_analysis.get("page_count") != len(pages):
        raise ValueError("native_analysis_page_count_invalid")

    # 索引保留重複 identity，後續必須恰好配對一筆，避免接錯來源文字。
    baseline_rows = _baseline_index(materials)
    output_materials: dict[
        tuple[str | None, str | None, str | None], list[dict[str, Any]]
    ] = defaultdict(list)
    has_selected = False
    has_failed = False

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
        warnings = (
            ["native_bbox_invalid"]
            if native_analysis_status == "partial"
            and "native_bbox_invalid" in native_analysis_reasons
            else []
        )
        # 只有 bbox 異常不影響既有文字內容；其他 partial/failed 狀態一律不放行。
        selected = not validation_reasons and (
            native_analysis_status == "success"
            or (
                native_analysis_status == "partial"
                and "native_bbox_invalid" in native_analysis_reasons
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
            block["text"] = baseline["text"]
            if native_analysis_status == "partial":
                block["selection_reason"] = "native_bbox_invalid"
        else:
            has_failed = True

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
    status = "success" if not has_failed else ("partial" if has_selected else "failed")
    return {
        "schema_version": SCHEMA_VERSION,
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
        stable_path=NORMALIZED_BLOCKS_STABLE_PATH,
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
    return []


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
    )


def _sort_value(value: Any) -> tuple[int, int | str]:
    """將頁碼、字串與缺失值轉成可穩定比較的排序鍵。"""
    if isinstance(value, int) and not isinstance(value, bool):
        return (0, value)
    if isinstance(value, str):
        return (1, value)
    return (2, "")
