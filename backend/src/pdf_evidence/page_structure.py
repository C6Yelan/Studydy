from __future__ import annotations

import math
from typing import Any


PAGE_STRUCTURE_SCHEMA = "page-structure/v1"
EVIDENCE_SCHEMA = "page-evidence/v1"
COORDINATE_SPACE = "unrotated_page_points"

ELEMENT_TYPES = {
    "heading",
    "paragraph",
    "list",
    "code",
    "formula",
    "matrix",
    "table",
    "diagram_node",
    "diagram_label",
    "arrow",
    "other_visible_region",
}
RELATION_TYPES = {"left_of", "above", "contains", "directed_arrow"}
UNCERTAINTY_KINDS = {"uncertain", "cropped", "unreadable", "conflicting"}
ROOT_FIELDS = {
    "schema",
    "material_ref",
    "page_ref",
    "page_number",
    "input_evidence_ref",
    "coordinate_space",
    "elements",
    "reading_order",
    "spatial_relations",
}


def _nonempty_string(value: Any) -> bool:
    """判斷值是否為去除空白後仍有內容的字串。"""
    return isinstance(value, str) and bool(value.strip())


def _valid_sha256_ref(value: Any, prefix: str) -> bool:
    """檢查 reference 是否包含正確的資料類型前綴與 SHA-256 雜湊。"""
    if not isinstance(value, str) or not value.startswith(prefix):
        return False
    digest = value.removeprefix(prefix)
    return len(digest) == 64 and all(character in "0123456789abcdef" for character in digest)


def _validate_page_structure(page_structure: Any) -> str | None:
    """檢查 page_structure 的根欄位與型別，成功回傳 None，失敗回傳固定原因。"""
    if not isinstance(page_structure, dict) or set(page_structure) != ROOT_FIELDS:
        return "PAGE_STRUCTURE_INVALID"
    if page_structure["schema"] != PAGE_STRUCTURE_SCHEMA:
        return "PAGE_STRUCTURE_INVALID"
    if (
        not _nonempty_string(page_structure["material_ref"])
        or not _nonempty_string(page_structure["page_ref"])
        or not _nonempty_string(page_structure["input_evidence_ref"])
    ):
        return "PAGE_STRUCTURE_INVALID"

    page_number = page_structure["page_number"]
    if (
        isinstance(page_number, bool)
        or not isinstance(page_number, int)
        or page_number < 1
    ):
        return "PAGE_STRUCTURE_INVALID"
    if page_structure["coordinate_space"] != COORDINATE_SPACE:
        return "PAGE_STRUCTURE_INVALID"
    if (
        not isinstance(page_structure["elements"], list)
        or not isinstance(page_structure["reading_order"], list)
        or not isinstance(page_structure["spatial_relations"], list)
    ):
        return "PAGE_STRUCTURE_INVALID"
    return None


def _get_native_page_bbox(page_evidence: Any) -> list[float] | None:
    """檢查 Page Evidence 的頁面資訊；成功回傳未旋轉頁面範圍，失敗回傳 None。"""
    if not isinstance(page_evidence, dict):
        return None
    if (
        page_evidence.get("schema") != EVIDENCE_SCHEMA
        or page_evidence.get("status") != "succeeded"
    ):
        return None
    if (
        not _valid_sha256_ref(
            page_evidence.get("material_ref"), "material:sha256:"
        )
        or not _valid_sha256_ref(page_evidence.get("page_ref"), "page:sha256:")
        or not _valid_sha256_ref(
            page_evidence.get("evidence_ref"), "evidence:sha256:"
        )
    ):
        return None

    page_number = page_evidence.get("page_number")
    if (
        isinstance(page_number, bool)
        or not isinstance(page_number, int)
        or page_number < 1
    ):
        return None

    geometry = page_evidence.get("geometry")
    transform = page_evidence.get("coordinate_transform")
    if not isinstance(geometry, dict) or not isinstance(transform, dict):
        return None

    visible = geometry.get("visible_points")
    matrix = transform.get("rotated_to_point")
    if transform.get("native_coordinate_space") != COORDINATE_SPACE:
        return None
    if (
        not isinstance(visible, list)
        or len(visible) != 4
        or not isinstance(matrix, list)
        or len(matrix) != 6
    ):
        return None

    numbers = [*visible, *matrix]
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        for value in numbers
    ):
        return None
    x0, y0, x1, y1 = visible
    if x1 <= x0 or y1 <= y0:
        return None

    a, b, c, d, e, f = matrix
    corners = [
        (x0 * a + y0 * c + e, x0 * b + y0 * d + f),
        (x1 * a + y0 * c + e, x1 * b + y0 * d + f),
        (x0 * a + y1 * c + e, x0 * b + y1 * d + f),
        (x1 * a + y1 * c + e, x1 * b + y1 * d + f),
    ]
    native = [
        min(point[0] for point in corners),
        min(point[1] for point in corners),
        max(point[0] for point in corners),
        max(point[1] for point in corners),
    ]
    if native[2] <= native[0] or native[3] <= native[1]:
        return None
    return native


def _validate_page_evidence_binding(
    page_structure: dict[str, Any], page_evidence: Any
) -> tuple[list[float] | None, str | None]:
    """驗證 page_structure 與成功 Page Evidence 的 identity binding。"""
    native_page_bbox = _get_native_page_bbox(page_evidence)
    if native_page_bbox is None:
        return None, "PAGE_EVIDENCE_BINDING_INVALID"
    evidence_binding_fields = {
        "material_ref": "material_ref",
        "page_ref": "page_ref",
        "page_number": "page_number",
        "input_evidence_ref": "evidence_ref",
    }
    if any(
        page_structure[target] != page_evidence[source]
        for target, source in evidence_binding_fields.items()
    ):
        return None, "PAGE_EVIDENCE_BINDING_INVALID"
    return native_page_bbox, None


def _validate_element_ids_and_bboxes(
    elements: list[Any], native_page_bbox: list[float]
) -> tuple[dict[str, dict[str, Any]] | None, str | None]:
    """驗證 element ID 唯一性及未旋轉頁面座標邊界。"""
    elements_by_id: dict[str, dict[str, Any]] = {}
    for element in elements:
        if not isinstance(element, dict):
            return None, "ELEMENT_ID_INVALID"
        element_id = element.get("id")
        if not _nonempty_string(element_id) or element_id in elements_by_id:
            return None, "ELEMENT_ID_INVALID"
        elements_by_id[element_id] = element

    page_x0, page_y0, page_x1, page_y1 = native_page_bbox
    for element in elements:
        bbox = element.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            return None, "ELEMENT_BBOX_INVALID"
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in bbox
        ):
            return None, "ELEMENT_BBOX_INVALID"
        x0, y0, x1, y1 = bbox
        if (
            x1 <= x0
            or y1 <= y0
            or x0 < page_x0
            or y0 < page_y0
            or x1 > page_x1
            or y1 > page_y1
        ):
            return None, "ELEMENT_BBOX_INVALID"
    return elements_by_id, None


def _validate_matrix(element: dict[str, Any]) -> bool:
    """檢查 matrix 維度與 cells 的完整性、唯一性及邊界，回傳是否符合。"""
    if set(element) != {"id", "type", "bbox", "row_count", "column_count", "cells"}:
        return False
    rows = element["row_count"]
    columns = element["column_count"]
    cells = element["cells"]
    if (
        isinstance(rows, bool)
        or not isinstance(rows, int)
        or rows < 1
        or isinstance(columns, bool)
        or not isinstance(columns, int)
        or columns < 1
    ):
        return False
    if not isinstance(cells, list) or len(cells) != rows * columns:
        return False

    positions: set[tuple[int, int]] = set()
    for cell in cells:
        if not isinstance(cell, dict) or set(cell) != {"row", "column", "text"}:
            return False
        row = cell["row"]
        column = cell["column"]
        if (
            isinstance(row, bool)
            or not isinstance(row, int)
            or not 1 <= row <= rows
            or isinstance(column, bool)
            or not isinstance(column, int)
            or not 1 <= column <= columns
        ):
            return False
        if not isinstance(cell["text"], str):
            return False
        if (row, column) in positions:
            return False
        positions.add((row, column))
    return len(positions) == rows * columns


def _validate_table(element: dict[str, Any]) -> bool:
    """檢查 table 的 row、column、role、span、重疊及完整覆蓋，回傳是否符合。"""
    if set(element) != {"id", "type", "bbox", "row_count", "column_count", "cells"}:
        return False
    rows = element["row_count"]
    columns = element["column_count"]
    cells = element["cells"]
    if (
        isinstance(rows, bool)
        or not isinstance(rows, int)
        or rows < 1
        or isinstance(columns, bool)
        or not isinstance(columns, int)
        or columns < 1
    ):
        return False
    if not isinstance(cells, list) or not cells:
        return False

    occupied: set[tuple[int, int]] = set()
    cell_fields = {
        "row",
        "column",
        "row_span",
        "column_span",
        "role",
        "text",
    }
    for cell in cells:
        if not isinstance(cell, dict) or set(cell) != cell_fields:
            return False
        row = cell["row"]
        column = cell["column"]
        row_span = cell["row_span"]
        column_span = cell["column_span"]
        integers = (row, column, row_span, column_span)
        if any(isinstance(value, bool) or not isinstance(value, int) for value in integers):
            return False
        if row < 1 or column < 1:
            return False
        if row_span < 1 or column_span < 1:
            return False
        if row + row_span - 1 > rows or column + column_span - 1 > columns:
            return False
        if cell["role"] not in {"header", "data"}:
            return False
        if not isinstance(cell["text"], str):
            return False
        covered = {
            (covered_row, covered_column)
            for covered_row in range(row, row + row_span)
            for covered_column in range(column, column + column_span)
        }
        if occupied & covered:
            return False
        occupied.update(covered)
    return len(occupied) == rows * columns


def _validate_element_fields(elements: list[dict[str, Any]]) -> str | None:
    """檢查 elements 的 type 與固定形狀，成功回傳 None，失敗回傳固定原因。"""
    base_fields = {"id", "type", "bbox"}
    for element in elements:
        element_type = element.get("type")
        if element_type not in ELEMENT_TYPES:
            return "ELEMENT_SHAPE_INVALID"
        if element_type in {"heading", "paragraph", "code"}:
            valid = set(element) == base_fields | {"text"} and _nonempty_string(
                element.get("text")
            )
        elif element_type == "list":
            items = element.get("items")
            valid = (
                set(element) == base_fields | {"items"}
                and isinstance(items, list)
                and bool(items)
                and all(_nonempty_string(item) for item in items)
            )
        elif element_type == "formula":
            valid = set(element) == base_fields | {"latex"} and _nonempty_string(
                element.get("latex")
            )
        elif element_type == "matrix":
            valid = _validate_matrix(element)
        elif element_type == "table":
            valid = _validate_table(element)
        elif element_type in {"diagram_node", "arrow"}:
            valid = set(element) == base_fields
        elif element_type == "diagram_label":
            valid_fields = set(element) in (
                base_fields | {"text"},
                base_fields | {"text", "node_id"},
            )
            valid = valid_fields and _nonempty_string(element.get("text"))
            if "node_id" in element:
                valid = valid and _nonempty_string(element["node_id"])
        else:
            valid = (
                set(element) == base_fields | {"uncertainty_kind"}
                and element.get("uncertainty_kind") in UNCERTAINTY_KINDS
            )
        if not valid:
            return "ELEMENT_SHAPE_INVALID"
    return None


def _validate_spatial_relations(
    relations: list[Any], elements_by_id: dict[str, dict[str, Any]]
) -> str | None:
    """檢查 relations 的 references、重複、自指與反向衝突；成功回傳 None，失敗回傳固定原因。"""
    for element in elements_by_id.values():
        if element["type"] == "diagram_label" and "node_id" in element:
            node = elements_by_id.get(element["node_id"])
            if node is None or node["type"] != "diagram_node":
                return "SPATIAL_RELATION_INVALID"

    seen_relations: set[tuple[str, ...]] = set()
    seen_directional: set[tuple[str, str, str]] = set()
    used_arrow_ids: set[str] = set()
    for relation in relations:
        if not isinstance(relation, dict):
            return "SPATIAL_RELATION_INVALID"
        relation_type = relation.get("type")
        if relation_type not in RELATION_TYPES:
            return "SPATIAL_RELATION_INVALID"

        expected_fields = {"type", "source_id", "target_id"}
        if relation_type == "directed_arrow":
            expected_fields.add("arrow_id")
        if set(relation) != expected_fields:
            return "SPATIAL_RELATION_INVALID"

        source_id = relation["source_id"]
        target_id = relation["target_id"]
        if not _nonempty_string(source_id) or not _nonempty_string(target_id):
            return "SPATIAL_RELATION_INVALID"
        if source_id not in elements_by_id or target_id not in elements_by_id:
            return "SPATIAL_RELATION_INVALID"
        if source_id == target_id:
            return "SPATIAL_RELATION_INVALID"
        relation_key = (
            relation_type,
            source_id,
            target_id,
        )
        if relation_type == "directed_arrow":
            arrow_id = relation["arrow_id"]
            if not _nonempty_string(arrow_id):
                return "SPATIAL_RELATION_INVALID"
            arrow = elements_by_id.get(arrow_id)
            if arrow is None or arrow["type"] != "arrow":
                return "SPATIAL_RELATION_INVALID"
            if arrow_id in {source_id, target_id} or arrow_id in used_arrow_ids:
                return "SPATIAL_RELATION_INVALID"
            used_arrow_ids.add(arrow_id)
            relation_key += (arrow_id,)
        else:
            inverse_key = (relation_type, target_id, source_id)
            if inverse_key in seen_directional:
                return "SPATIAL_RELATION_INVALID"
        if relation_key in seen_relations:
            return "SPATIAL_RELATION_INVALID"
        seen_relations.add(relation_key)
        if relation_type != "directed_arrow":
            seen_directional.add(relation_key)
    return None


def _validate_reading_order(
    reading_order: list[Any], elements_by_id: dict[str, dict[str, Any]]
) -> str | None:
    """驗證 reading_order references、唯一性與必要覆蓋。"""
    if (
        any(not isinstance(element_id, str) for element_id in reading_order)
        or len(reading_order) != len(set(reading_order))
        or any(element_id not in elements_by_id for element_id in reading_order)
    ):
        return "READING_ORDER_INVALID"
    required = {
        element_id
        for element_id, element in elements_by_id.items()
        if element["type"] not in {"arrow", "diagram_node"}
    }
    if not required.issubset(reading_order):
        return "READING_ORDER_INVALID"
    return None


def validate_page_structure(
    page_structure: Any, page_evidence: Any
) -> str | None:
    """依序檢查 page_structure 與 page_evidence，成功回傳 None，失敗回傳固定原因。"""
    reason = _validate_page_structure(page_structure)
    if reason is not None:
        return reason

    native_page_bbox, reason = _validate_page_evidence_binding(page_structure, page_evidence)
    if reason is not None:
        return reason

    elements_by_id, reason = _validate_element_ids_and_bboxes(
        page_structure["elements"], native_page_bbox
    )
    if reason is not None:
        return reason

    reason = _validate_element_fields(page_structure["elements"])
    if reason is not None:
        return reason
    reason = _validate_spatial_relations(page_structure["spatial_relations"], elements_by_id)
    if reason is not None:
        return reason
    reason = _validate_reading_order(page_structure["reading_order"], elements_by_id)
    return reason
