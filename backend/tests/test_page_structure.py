from copy import deepcopy

import pytest

from pdf_evidence.page_structure import validate_page_structure


def _successful_page_evidence():
    """建立 identity binding 檢查所需的成功 Page Evidence fixture。"""
    return {
        "schema": "page-evidence/v1",
        "status": "succeeded",
        "material_ref": f"material:sha256:{'a' * 64}",
        "page_ref": f"page:sha256:{'b' * 64}",
        "page_number": 1,
        "evidence_ref": f"evidence:sha256:{'c' * 64}",
        "geometry": {"visible_points": [0.0, 0.0, 200.0, 100.0]},
        "coordinate_transform": {
            "native_coordinate_space": "unrotated_page_points",
            "rotated_to_point": [0.0, -1.0, 1.0, 0.0, 0.0, 200.0],
        },
    }


def _page_structure():
    """建立涵蓋所有 element type、relation 與 reading order 的 fixture。"""
    elements = [
        {"id": "heading", "type": "heading", "bbox": [5, 5, 40, 12], "text": "Title"},
        {"id": "paragraph", "type": "paragraph", "bbox": [5, 15, 90, 25], "text": "Text"},
        {"id": "list", "type": "list", "bbox": [5, 28, 90, 40], "items": ["first", "second"]},
        {"id": "code", "type": "code", "bbox": [5, 43, 90, 53], "text": "print('x')"},
        {"id": "formula", "type": "formula", "bbox": [5, 56, 90, 66], "latex": "x^2"},
        {
            "id": "matrix",
            "type": "matrix",
            "bbox": [5, 69, 45, 89],
            "row_count": 2,
            "column_count": 2,
            "cells": [
                {"row": 1, "column": 1, "text": "a"},
                {"row": 1, "column": 2, "text": "b"},
                {"row": 2, "column": 1, "text": "c"},
                {"row": 2, "column": 2, "text": "d"},
            ],
        },
        {
            "id": "table",
            "type": "table",
            "bbox": [50, 69, 95, 99],
            "row_count": 2,
            "column_count": 2,
            "cells": [
                {
                    "row": 1,
                    "column": 1,
                    "row_span": 1,
                    "column_span": 2,
                    "role": "header",
                    "text": "Header",
                },
                {
                    "row": 2,
                    "column": 1,
                    "row_span": 1,
                    "column_span": 1,
                    "role": "data",
                    "text": "A",
                },
                {
                    "row": 2,
                    "column": 2,
                    "row_span": 1,
                    "column_span": 1,
                    "role": "data",
                    "text": "B",
                },
            ],
        },
        {"id": "node", "type": "diagram_node", "bbox": [5, 105, 35, 125]},
        {
            "id": "label",
            "type": "diagram_label",
            "bbox": [10, 110, 30, 120],
            "text": "Node",
            "node_id": "node",
        },
        {"id": "arrow", "type": "arrow", "bbox": [35, 112, 55, 118]},
        {
            "id": "uncertain",
            "type": "other_visible_region",
            "bbox": [5, 135, 45, 150],
            "uncertainty_kind": "unreadable",
        },
    ]
    return {
        "schema": "page-structure/v1",
        "material_ref": f"material:sha256:{'a' * 64}",
        "page_ref": f"page:sha256:{'b' * 64}",
        "page_number": 1,
        "input_evidence_ref": f"evidence:sha256:{'c' * 64}",
        "coordinate_space": "unrotated_page_points",
        "elements": elements,
        "reading_order": [
            "heading",
            "paragraph",
            "list",
            "code",
            "formula",
            "matrix",
            "table",
            "label",
            "uncertain",
        ],
        "spatial_relations": [
            {"type": "left_of", "source_id": "node", "target_id": "label"},
            {"type": "above", "source_id": "heading", "target_id": "paragraph"},
            {"type": "contains", "source_id": "node", "target_id": "label"},
            {
                "type": "directed_arrow",
                "source_id": "node",
                "target_id": "label",
                "arrow_id": "arrow",
            },
        ],
    }


def _find_element(page_structure, element_id):
    """依 ID 取得測試 fixture 中的 element。"""
    return next(element for element in page_structure["elements"] if element["id"] == element_id)


def test_accepts_complete_page_structure_without_changing_input():
    """驗證完整 synthetic 結構通過且 validator 不改寫輸入。"""
    page_structure = _page_structure()
    page_evidence = _successful_page_evidence()
    original_page_structure = deepcopy(page_structure)

    assert validate_page_structure(page_structure, page_evidence) is None
    assert page_structure == original_page_structure


@pytest.mark.parametrize(
    "mutate, reason",
    [
        (
            lambda page_structure, evidence: page_structure.update(page_number=2),
            "PAGE_EVIDENCE_BINDING_INVALID",
        ),
        (
            lambda page_structure, evidence: _find_element(
                page_structure, "matrix"
            )["cells"].pop(),
            "ELEMENT_SHAPE_INVALID",
        ),
        (
            lambda page_structure, evidence: _find_element(
                page_structure, "table"
            )["cells"][0].update(column_span=3),
            "ELEMENT_SHAPE_INVALID",
        ),
        (
            lambda page_structure, evidence: page_structure["spatial_relations"][
                3
            ].update(arrow_id="node"),
            "SPATIAL_RELATION_INVALID",
        ),
        (
            lambda page_structure, evidence: _find_element(
                page_structure, "arrow"
            ).update(source_id="node"),
            "ELEMENT_SHAPE_INVALID",
        ),
    ],
)
def test_rejects_invalid_page_structure(mutate, reason):
    """驗證各類無效結構皆回傳預期的固定原因。"""
    page_structure = _page_structure()
    page_evidence = _successful_page_evidence()
    mutate(page_structure, page_evidence)

    assert validate_page_structure(page_structure, page_evidence) == reason
