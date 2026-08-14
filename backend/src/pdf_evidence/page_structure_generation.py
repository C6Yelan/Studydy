from __future__ import annotations

from copy import deepcopy
import hashlib
import math
from typing import Any

from .page_structure import PAGE_STRUCTURE_SCHEMA, validate_page_structure


PAGE_STRUCTURE_PROMPT_VERSION = "page-structure-prompt/v4"
PAGE_STRUCTURE_PROMPT = (
    "Inspect the target page image and describe only its visible page structure. Adjacent page images "
    "may be supplied as context for understanding continued content, but do not copy their elements "
    "into the target page output. Ground every element in visible target-page evidence, preserve reading "
    "and spatial relationships, mark uncertain regions explicitly, and do not invent content. Every bbox "
    "uses normalized_render_1000 coordinates ordered [x0, y0, x1, y1] with values from 0 to 1000; "
    "x increases rightward, y increases downward, x0 < x1, and y0 < y1. Include every heading, "
    "paragraph, list, code, formula, matrix, table, diagram_label, and other_visible_region exactly once "
    "in reading_order; omit diagram_node and arrow from reading_order. Every relation ID must reference "
    "an existing element. Do not add duplicate, self, or inverse relations. A directed_arrow must use a "
    "distinct arrow element, and each arrow element may be used by at most one directed_arrow. When a "
    "diagram has visible internal nodes or connections, describe those parts instead of treating the whole "
    "diagram as one node. Transcribe visible text exactly; if it cannot be read reliably, use an uncertain "
    "region instead of guessing."
)
PAGE_STRUCTURE_BODY_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["elements", "reading_order", "spatial_relations"],
    "$defs": {"nonempty_string": {"type": "string", "minLength": 1, "pattern": "^[\\s\\S]*\\S[\\s\\S]*$"},
        "bbox": {
            "type": "array", "items": {"type": "number", "minimum": 0, "maximum": 1000},
            "minItems": 4, "maxItems": 4,
        },
        "matrix_cell": {"type": "object", "additionalProperties": False, "required": ["row", "column", "text"],
            "properties": {
                "row": {"type": "integer", "minimum": 1}, "column": {"type": "integer", "minimum": 1},
                "text": {"type": "string"},
            },
        },
        "table_cell": {"type": "object", "additionalProperties": False, "required": ["row", "column", "row_span", "column_span", "role", "text"],
            "properties": {
                "row": {"type": "integer", "minimum": 1}, "column": {"type": "integer", "minimum": 1},
                "row_span": {"type": "integer", "minimum": 1}, "column_span": {"type": "integer", "minimum": 1},
                "role": {"type": "string", "enum": ["header", "data"]},
                "text": {"type": "string"},
            },
        },
    },
    "properties": {
        "elements": {"type": "array", "items": {"anyOf": [
                    {
                        "type": "object", "additionalProperties": False, "required": ["id", "type", "bbox", "text"],
                        "properties": {
                            "id": {"$ref": "#/$defs/nonempty_string"}, "type": {"type": "string", "enum": ["heading", "paragraph", "code"]},
                            "bbox": {"$ref": "#/$defs/bbox"},
                            "text": {"$ref": "#/$defs/nonempty_string"},
                        },
                    },
                    {
                        "type": "object", "additionalProperties": False, "required": ["id", "type", "bbox", "items"],
                        "properties": {
                            "id": {"$ref": "#/$defs/nonempty_string"}, "type": {"type": "string", "const": "list"},
                            "bbox": {"$ref": "#/$defs/bbox"},
                            "items": {"type": "array", "items": {"$ref": "#/$defs/nonempty_string"}, "minItems": 1},
                        },
                    },
                    {
                        "type": "object", "additionalProperties": False, "required": ["id", "type", "bbox", "latex"],
                        "properties": {
                            "id": {"$ref": "#/$defs/nonempty_string"}, "type": {"type": "string", "const": "formula"},
                            "bbox": {"$ref": "#/$defs/bbox"}, "latex": {"$ref": "#/$defs/nonempty_string"},
                        },
                    },
                    {
                        "type": "object", "additionalProperties": False, "required": ["id", "type", "bbox", "row_count", "column_count", "cells"],
                        "properties": {
                            "id": {"$ref": "#/$defs/nonempty_string"}, "type": {"type": "string", "const": "matrix"},
                            "bbox": {"$ref": "#/$defs/bbox"},
                            "row_count": {"type": "integer", "minimum": 1}, "column_count": {"type": "integer", "minimum": 1},
                            "cells": {"type": "array", "items": {"$ref": "#/$defs/matrix_cell"}, "minItems": 1},
                        },
                    },
                    {
                        "type": "object", "additionalProperties": False, "required": ["id", "type", "bbox", "row_count", "column_count", "cells"],
                        "properties": {
                            "id": {"$ref": "#/$defs/nonempty_string"}, "type": {"type": "string", "const": "table"},
                            "bbox": {"$ref": "#/$defs/bbox"},
                            "row_count": {"type": "integer", "minimum": 1}, "column_count": {"type": "integer", "minimum": 1},
                            "cells": {"type": "array", "items": {"$ref": "#/$defs/table_cell"}, "minItems": 1},
                        },
                    },
                    {
                        "type": "object", "additionalProperties": False, "required": ["id", "type", "bbox"],
                        "properties": {
                            "id": {"$ref": "#/$defs/nonempty_string"}, "type": {"type": "string", "enum": ["diagram_node", "arrow"]},
                            "bbox": {"$ref": "#/$defs/bbox"},
                        },
                    },
                    {
                        "type": "object", "additionalProperties": False, "required": ["id", "type", "bbox", "text"],
                        "properties": {
                            "id": {"$ref": "#/$defs/nonempty_string"}, "type": {"type": "string", "const": "diagram_label"},
                            "bbox": {"$ref": "#/$defs/bbox"}, "text": {"$ref": "#/$defs/nonempty_string"},
                        },
                    },
                    {
                        "type": "object", "additionalProperties": False, "required": ["id", "type", "bbox", "text", "node_id"],
                        "properties": {
                            "id": {"$ref": "#/$defs/nonempty_string"}, "type": {"type": "string", "const": "diagram_label"},
                            "bbox": {"$ref": "#/$defs/bbox"}, "text": {"$ref": "#/$defs/nonempty_string"},
                            "node_id": {"$ref": "#/$defs/nonempty_string"},
                        },
                    },
                    {
                        "type": "object", "additionalProperties": False, "required": ["id", "type", "bbox", "uncertainty_kind"],
                        "properties": {
                            "id": {"$ref": "#/$defs/nonempty_string"}, "type": {"type": "string", "const": "other_visible_region"},
                            "bbox": {"$ref": "#/$defs/bbox"},
                            "uncertainty_kind": {"type": "string", "enum": ["uncertain", "cropped", "unreadable", "conflicting"]},
                        },
                    },
                ]
            },
        },
        "reading_order": {"type": "array", "items": {"type": "string"}},
        "spatial_relations": {"type": "array", "items": {"anyOf": [
                    {
                        "type": "object", "additionalProperties": False, "required": ["type", "source_id", "target_id"],
                        "properties": {
                            "type": {"type": "string", "enum": ["left_of", "above", "contains"]},
                            "source_id": {"$ref": "#/$defs/nonempty_string"},
                            "target_id": {"$ref": "#/$defs/nonempty_string"},
                        },
                    },
                    {
                        "type": "object", "additionalProperties": False, "required": ["type", "source_id", "target_id", "arrow_id"],
                        "properties": {
                            "type": {"type": "string", "const": "directed_arrow"},
                            "source_id": {"$ref": "#/$defs/nonempty_string"},
                            "target_id": {"$ref": "#/$defs/nonempty_string"},
                            "arrow_id": {"$ref": "#/$defs/nonempty_string"},
                        },
                    },
                ]
            },
        },
    },
}


def _valid_sha256(value: Any) -> bool:
    """檢查值是否為小寫 SHA-256 字串。"""
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _finite_number(value: Any) -> bool:
    """排除 bool 並檢查有限的整數或浮點數。"""
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
    )


def _validate_page_evidence(page_evidence: Any) -> bool:
    """檢查座標轉換與 root binding 所需的 Page Evidence。"""
    if not isinstance(page_evidence, dict):
        return False
    if (
        page_evidence.get("schema") != "page-evidence/v1"
        or page_evidence.get("status") != "succeeded"
    ):
        return False
    page_number = page_evidence.get("page_number")
    if isinstance(page_number, bool) or not isinstance(page_number, int) or page_number < 1:
        return False

    hashes = page_evidence.get("hashes")
    render = page_evidence.get("render")
    geometry = page_evidence.get("geometry")
    transform = page_evidence.get("coordinate_transform")
    if not all(isinstance(value, dict) for value in (hashes, render, geometry, transform)):
        return False
    source_sha256 = hashes.get("source_sha256")
    native_sha256 = hashes.get("native_sha256")
    render_sha256 = hashes.get("render_sha256")
    if not all(
        _valid_sha256(value)
        for value in (source_sha256, native_sha256, render_sha256)
    ):
        return False
    page_ref_hash = hashlib.sha256(
        f"{source_sha256}:{page_number}".encode("ascii")
    ).hexdigest()
    evidence_hash = hashlib.sha256(
        f"{source_sha256}:{page_number}:{native_sha256}:{render_sha256}".encode(
            "ascii"
        )
    ).hexdigest()
    if (
        page_evidence.get("material_ref")
        != f"material:sha256:{source_sha256}"
        or page_evidence.get("page_ref") != f"page:sha256:{page_ref_hash}"
        or page_evidence.get("evidence_ref")
        != f"evidence:sha256:{evidence_hash}"
    ):
        return False
    if not isinstance(render.get("schema"), str) or not render["schema"]:
        return False
    if not _finite_number(render.get("width_pixels")) or render["width_pixels"] <= 0:
        return False
    if not _finite_number(render.get("height_pixels")) or render["height_pixels"] <= 0:
        return False

    visible = geometry.get("visible_points")
    matrix = transform.get("rotated_to_point")
    if (
        transform.get("native_coordinate_space") != "unrotated_page_points"
        or not isinstance(visible, list)
        or len(visible) != 4
        or not isinstance(matrix, list)
        or len(matrix) != 6
        or not all(_finite_number(value) for value in [*visible, *matrix])
    ):
        return False
    return visible[2] > visible[0] and visible[3] > visible[1]


def _build_page_structure_artifact(
    model_body: dict[str, Any], page_evidence: dict[str, Any]
) -> dict[str, Any] | None:
    """將 normalized bbox 轉為 native 座標並補上可信 root binding。"""
    elements = deepcopy(model_body["elements"])
    if not isinstance(elements, list):
        return None
    render = page_evidence["render"]
    visible = page_evidence["geometry"]["visible_points"]
    matrix = page_evidence["coordinate_transform"]["rotated_to_point"]
    width = render["width_pixels"]
    height = render["height_pixels"]
    visible_x0, visible_y0, visible_x1, visible_y1 = visible
    a, b, c, d, e, f = matrix

    for element in elements:
        if not isinstance(element, dict):
            return None
        bbox = element.get("bbox")
        if (
            not isinstance(bbox, list)
            or len(bbox) != 4
            or not all(_finite_number(value) and 0 <= value <= 1000 for value in bbox)
            or bbox[2] <= bbox[0]
            or bbox[3] <= bbox[1]
        ):
            return None
        native_corners = []
        for normalized_x, normalized_y in (
            (bbox[0], bbox[1]),
            (bbox[2], bbox[1]),
            (bbox[0], bbox[3]),
            (bbox[2], bbox[3]),
        ):
            pixel_x = normalized_x / 1000 * width
            pixel_y = normalized_y / 1000 * height
            rotated_x = visible_x0 + pixel_x / width * (visible_x1 - visible_x0)
            rotated_y = visible_y0 + pixel_y / height * (visible_y1 - visible_y0)
            native_corners.append(
                (
                    rotated_x * a + rotated_y * c + e,
                    rotated_x * b + rotated_y * d + f,
                )
            )
        if not all(
            math.isfinite(value) for point in native_corners for value in point
        ):
            return None
        element["bbox"] = [
            min(point[0] for point in native_corners),
            min(point[1] for point in native_corners),
            max(point[0] for point in native_corners),
            max(point[1] for point in native_corners),
        ]

    page_structure = {
        "schema": PAGE_STRUCTURE_SCHEMA,
        "material_ref": page_evidence["material_ref"],
        "page_ref": page_evidence["page_ref"],
        "page_number": page_evidence["page_number"],
        "input_evidence_ref": page_evidence["evidence_ref"],
        "coordinate_space": "unrotated_page_points",
        "elements": elements,
        "reading_order": deepcopy(model_body["reading_order"]),
        "spatial_relations": deepcopy(model_body["spatial_relations"]),
    }
    return page_structure


def finalize_page_structure(
    model_body: Any, page_evidence: Any
) -> dict[str, Any] | None:
    """純函式驗證 model body，轉換 bbox 並綁回同頁 Evidence。"""
    if (
        not isinstance(model_body, dict)
        or set(model_body)
        != {"elements", "reading_order", "spatial_relations"}
        or not _validate_page_evidence(page_evidence)
    ):
        return None
    page_structure = _build_page_structure_artifact(model_body, page_evidence)
    return (
        page_structure
        if page_structure is not None
        and validate_page_structure(page_structure, page_evidence) is None
        else None
    )
