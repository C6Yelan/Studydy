from copy import deepcopy
import hashlib
import json

import pytest

from pdf_evidence.page_alignment import (
    adjudicate_visual_alignment,
    assess_page_structure_alignment,
)


def _canonical_sha256(value):
    """以 production 使用的 canonical JSON 規則計算測試資料 SHA-256。"""
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _rebind_native(page_evidence, native_page):
    """更新 synthetic native 與 Page Evidence 的 canonical hash 綁定。"""
    native_sha256 = _canonical_sha256(native_page)
    page_evidence["hashes"]["native_sha256"] = native_sha256
    source_sha256 = page_evidence["hashes"]["source_sha256"]
    render_sha256 = page_evidence["hashes"]["render_sha256"]
    page_number = page_evidence["page_number"]
    evidence_sha256 = hashlib.sha256(
        f"{source_sha256}:{page_number}:{native_sha256}:{render_sha256}".encode(
            "ascii"
        )
    ).hexdigest()
    page_evidence["evidence_ref"] = f"evidence:sha256:{evidence_sha256}"


def _simple_inputs():
    """建立兩段簡單文字可逐一對回 native spans 的完整輸入。"""
    source_sha256 = "a" * 64
    page_number = 1
    page_ref_sha256 = hashlib.sha256(
        f"{source_sha256}:{page_number}".encode("ascii")
    ).hexdigest()
    material_ref = f"material:sha256:{source_sha256}"
    page_ref = f"page:sha256:{page_ref_sha256}"
    geometry = {
        "mediabox_points": [0.0, 0.0, 200.0, 100.0],
        "cropbox_points": [0.0, 0.0, 200.0, 100.0],
        "visible_points": [0.0, 0.0, 200.0, 100.0],
        "rotation_degrees": 0,
    }
    native_page = {
        "schema": "page-native/v1",
        "material_ref": material_ref,
        "page_ref": page_ref,
        "page_number": page_number,
        "geometry": geometry,
        "spans": [
            {"text": "Synthetic title", "bbox": [10.0, 10.0, 80.0, 20.0]},
            {"text": "Synthetic paragraph", "bbox": [10.0, 30.0, 120.0, 42.0]},
        ],
        "images": [],
        "drawings": [],
    }
    page_evidence = {
        "schema": "page-evidence/v1",
        "status": "succeeded",
        "reason": "EVIDENCE_READY",
        "material_ref": material_ref,
        "page_ref": page_ref,
        "page_number": page_number,
        "evidence_ref": "",
        "hashes": {
            "source_sha256": source_sha256,
            "native_sha256": "",
            "render_sha256": "b" * 64,
        },
        "geometry": geometry,
        "coordinate_transform": {
            "native_coordinate_space": "unrotated_page_points",
            "rotated_to_point": [1.0, 0.0, 0.0, 1.0, 0.0, 0.0],
        },
    }
    _rebind_native(page_evidence, native_page)
    page_structure = {
        "schema": "page-structure/v1",
        "material_ref": material_ref,
        "page_ref": page_ref,
        "page_number": page_number,
        "input_evidence_ref": page_evidence["evidence_ref"],
        "coordinate_space": "unrotated_page_points",
        "elements": [
            {
                "id": "heading-1",
                "type": "heading",
                "bbox": [10.0, 10.0, 80.0, 20.0],
                "text": "Synthetic title",
            },
            {
                "id": "paragraph-1",
                "type": "paragraph",
                "bbox": [10.0, 30.0, 120.0, 42.0],
                "text": "Synthetic paragraph",
            },
        ],
        "reading_order": ["heading-1", "paragraph-1"],
        "spatial_relations": [],
    }
    return page_structure, page_evidence, native_page


def test_accepts_only_fully_grounded_simple_text_without_changing_inputs():
    """驗證簡單文字、bbox 與 reading order 全部確定時才接受且保持純函式。"""
    page_structure, page_evidence, native_page = _simple_inputs()
    originals = deepcopy((page_structure, page_evidence, native_page))

    result = assess_page_structure_alignment(
        page_structure, page_evidence, native_page
    )

    assert result == {
        "schema": "page-alignment/v1",
        "identity": {
            "material_ref": page_structure["material_ref"],
            "page_ref": page_structure["page_ref"],
            "page_number": 1,
        },
        "input_binding": {
            "evidence_ref": page_evidence["evidence_ref"],
            "page_structure_sha256": _canonical_sha256(page_structure),
            "native_sha256": page_evidence["hashes"]["native_sha256"],
        },
        "processing": "succeeded",
        "quality": "accepted",
        "decision": "retain",
        "reason_code": "ALIGNMENT_ACCEPTED",
        "findings": [],
    }
    assert (page_structure, page_evidence, native_page) == originals
    assert "confidence" not in json.dumps(result)
    assert '"reason"' not in json.dumps(result)
    assert "Synthetic" not in json.dumps(result)


@pytest.mark.parametrize(
    "element",
    [
        {
            "id": "formula-1",
            "type": "formula",
            "bbox": [10.0, 10.0, 80.0, 20.0],
            "latex": "x^2",
        },
        {
            "id": "matrix-1",
            "type": "matrix",
            "bbox": [10.0, 10.0, 80.0, 40.0],
            "row_count": 1,
            "column_count": 1,
            "cells": [{"row": 1, "column": 1, "text": "x"}],
        },
        {
            "id": "table-1",
            "type": "table",
            "bbox": [10.0, 10.0, 80.0, 40.0],
            "row_count": 1,
            "column_count": 1,
            "cells": [
                {
                    "row": 1,
                    "column": 1,
                    "row_span": 1,
                    "column_span": 1,
                    "role": "data",
                    "text": "x",
                }
            ],
        },
        {
            "id": "node-1",
            "type": "diagram_node",
            "bbox": [10.0, 10.0, 80.0, 40.0],
        },
        {
            "id": "arrow-1",
            "type": "arrow",
            "bbox": [10.0, 10.0, 80.0, 20.0],
        },
        {
            "id": "uncertain-1",
            "type": "other_visible_region",
            "bbox": [10.0, 10.0, 80.0, 20.0],
            "uncertainty_kind": "unreadable",
        },
    ],
    ids=["formula", "matrix", "table", "diagram", "arrow", "uncertain"],
)
def test_complex_elements_need_review(element):
    """驗證複雜或不確定 element 不會由簡單對齊自動接受。"""
    page_structure, page_evidence, native_page = _simple_inputs()
    page_structure["elements"] = [element]
    page_structure["reading_order"] = (
        [] if element["type"] in {"diagram_node", "arrow"} else [element["id"]]
    )

    result = assess_page_structure_alignment(
        page_structure, page_evidence, native_page
    )

    assert result["processing"] == "succeeded"
    assert result["quality"] == "needs_review"
    assert result["decision"] == "review"
    assert result["reason_code"] == "COMPLEX_CONTENT_NEEDS_REVIEW"
    assert all("reason_code" in finding for finding in result["findings"])
    assert all("reason" not in finding for finding in result["findings"])


def test_vision_only_native_content_needs_review():
    """驗證 native image 或 drawing 存在時不會宣稱文字對齊已足夠。"""
    page_structure, page_evidence, native_page = _simple_inputs()
    native_page["images"] = [{"xref": 1}]
    _rebind_native(page_evidence, native_page)
    page_structure["input_evidence_ref"] = page_evidence["evidence_ref"]

    result = assess_page_structure_alignment(
        page_structure, page_evidence, native_page
    )

    assert result["quality"] == "needs_review"
    assert result["reason_code"] == "VISION_CONTENT_NEEDS_REVIEW"
    assert result["findings"] == [{"reason_code": "VISION_CONTENT_PRESENT"}]


@pytest.mark.parametrize(
    ("decision", "quality", "outcome", "reason_code"),
    [
        (
            "retain",
            "accepted",
            "retain",
            "VISUAL_ALIGNMENT_REVIEW_ACCEPTED",
        ),
        (
            "reject",
            "unsupported",
            "reject",
            "VISUAL_ALIGNMENT_REVIEW_REJECTED",
        ),
    ],
)
def test_visual_alignment_adjudication_preserves_identity_and_findings(
    decision, quality, outcome, reason_code
):
    """驗證複核裁決只改寫狀態，並保留來源綁定與 findings。"""
    page_structure, page_evidence, native_page = _simple_inputs()
    native_page["images"] = [{"xref": 1}]
    _rebind_native(page_evidence, native_page)
    page_structure["input_evidence_ref"] = page_evidence["evidence_ref"]
    alignment = assess_page_structure_alignment(
        page_structure, page_evidence, native_page
    )
    original = deepcopy(alignment)

    result = adjudicate_visual_alignment(alignment, decision)

    assert set(result) == set(alignment)
    assert result["processing"] == "succeeded"
    assert result["quality"] == quality
    assert result["decision"] == outcome
    assert result["reason_code"] == reason_code
    assert result["identity"] == alignment["identity"]
    assert result["input_binding"] == alignment["input_binding"]
    assert result["findings"] == alignment["findings"]
    result["findings"][0]["reason_code"] = "changed"
    assert alignment == original


@pytest.mark.parametrize(
    "invalid_case",
    [
        "decision",
        "shape",
        "failed",
        "automatic",
        "other_review",
        "empty_findings",
        "other_finding",
        "nested_finding_field",
        "already_adjudicated",
    ],
)
def test_visual_alignment_adjudication_rejects_invalid_inputs(invalid_case):
    """驗證非原始視覺複核結果或未知裁決一律 fail closed。"""
    page_structure, page_evidence, native_page = _simple_inputs()
    native_page["drawings"] = [{"type": "line"}]
    _rebind_native(page_evidence, native_page)
    page_structure["input_evidence_ref"] = page_evidence["evidence_ref"]
    alignment = assess_page_structure_alignment(
        page_structure, page_evidence, native_page
    )
    decision = "retain"
    if invalid_case == "decision":
        decision = "review"
    elif invalid_case == "shape":
        alignment["extra"] = "invalid"
    elif invalid_case == "failed":
        alignment.update(
            {
                "processing": "failed",
                "quality": "unsupported",
                "decision": "reject",
                "reason_code": "NATIVE_PAGE_INVALID",
                "findings": [],
            }
        )
    elif invalid_case == "automatic":
        alignment.update(
            {
                "quality": "accepted",
                "decision": "retain",
                "reason_code": "ALIGNMENT_ACCEPTED",
                "findings": [],
            }
        )
    elif invalid_case == "other_review":
        alignment["reason_code"] = "COMPLEX_CONTENT_NEEDS_REVIEW"
    elif invalid_case == "empty_findings":
        alignment["findings"] = []
    elif invalid_case == "other_finding":
        alignment["findings"] = [{"reason_code": "COMPLEX_ELEMENT_PRESENT"}]
    elif invalid_case == "nested_finding_field":
        alignment["findings"][0]["extra"] = "invalid"
    else:
        alignment = adjudicate_visual_alignment(alignment, "retain")
    original = deepcopy(alignment)

    assert adjudicate_visual_alignment(alignment, decision) is None
    assert alignment == original


@pytest.mark.parametrize("mismatch", ["text", "bbox", "ambiguous"])
def test_text_or_bbox_without_unique_native_span_needs_review(mismatch):
    """驗證文字或 bbox 無法唯一對回 native span 時要求複核。"""
    page_structure, page_evidence, native_page = _simple_inputs()
    if mismatch == "text":
        page_structure["elements"][0]["text"] = "Different synthetic title"
    elif mismatch == "bbox":
        page_structure["elements"][0]["bbox"] = [10.0, 10.0, 81.0, 20.0]
    else:
        native_page["spans"].append(deepcopy(native_page["spans"][0]))
        _rebind_native(page_evidence, native_page)
        page_structure["input_evidence_ref"] = page_evidence["evidence_ref"]

    result = assess_page_structure_alignment(
        page_structure, page_evidence, native_page
    )

    assert result["quality"] == "needs_review"
    assert result["reason_code"] == "TEXT_ALIGNMENT_NEEDS_REVIEW"


def test_native_span_order_conflict_needs_review():
    """驗證 structure reading order 與 native span 順序衝突時要求複核。"""
    page_structure, page_evidence, native_page = _simple_inputs()
    native_page["spans"].reverse()
    _rebind_native(page_evidence, native_page)
    page_structure["input_evidence_ref"] = page_evidence["evidence_ref"]

    result = assess_page_structure_alignment(
        page_structure, page_evidence, native_page
    )

    assert result["quality"] == "needs_review"
    assert result["reason_code"] == "READING_ORDER_NEEDS_REVIEW"


def test_simple_text_with_spatial_relation_needs_review():
    """驗證 native spans 無法證明的 spatial relation 不會自動接受。"""
    page_structure, page_evidence, native_page = _simple_inputs()
    page_structure["spatial_relations"] = [
        {
            "type": "above",
            "source_id": "heading-1",
            "target_id": "paragraph-1",
        }
    ]

    result = assess_page_structure_alignment(
        page_structure, page_evidence, native_page
    )

    assert result["quality"] == "needs_review"
    assert result["reason_code"] == "SPATIAL_RELATION_NEEDS_REVIEW"


def test_existing_page_structure_validator_failure_is_preserved():
    """驗證既有 validator 的固定失敗原因直接阻止 alignment 成功。"""
    page_structure, page_evidence, native_page = _simple_inputs()
    page_structure["reading_order"].append("heading-1")

    result = assess_page_structure_alignment(
        page_structure, page_evidence, native_page
    )

    assert result["processing"] == "failed"
    assert result["quality"] == "unsupported"
    assert result["decision"] == "reject"
    assert result["reason_code"] == "READING_ORDER_INVALID"


@pytest.mark.parametrize(
    ("mutation", "expected_reason_code"),
    [
        ("schema", "NATIVE_PAGE_INVALID"),
        ("geometry", "NATIVE_PAGE_BINDING_INVALID"),
        ("material_ref", "NATIVE_PAGE_BINDING_INVALID"),
        ("page_ref", "NATIVE_PAGE_BINDING_INVALID"),
        ("page_number", "NATIVE_PAGE_BINDING_INVALID"),
        ("canonical_material", "PAGE_EVIDENCE_HASH_BINDING_INVALID"),
        ("canonical_page", "PAGE_EVIDENCE_HASH_BINDING_INVALID"),
        ("native_hash", "NATIVE_HASH_MISMATCH"),
        ("evidence_hash", "PAGE_EVIDENCE_HASH_BINDING_INVALID"),
    ],
)
def test_native_identity_and_canonical_hash_mismatches_fail_closed(
    mutation, expected_reason_code
):
    """驗證 native schema、頁面 identity 與 canonical hash 錯誤全部 fail closed。"""
    page_structure, page_evidence, native_page = _simple_inputs()
    if mutation == "schema":
        native_page["schema"] = "page-native/v2"
    elif mutation == "geometry":
        native_page["geometry"] = deepcopy(native_page["geometry"])
        native_page["geometry"]["visible_points"] = [0.0, 0.0, 201.0, 100.0]
    elif mutation == "material_ref":
        native_page["material_ref"] = f"material:sha256:{'c' * 64}"
    elif mutation == "page_ref":
        native_page["page_ref"] = f"page:sha256:{'c' * 64}"
    elif mutation == "page_number":
        native_page["page_number"] = 2
    elif mutation == "canonical_material":
        material_ref = f"material:sha256:{'c' * 64}"
        page_structure["material_ref"] = material_ref
        page_evidence["material_ref"] = material_ref
        native_page["material_ref"] = material_ref
        _rebind_native(page_evidence, native_page)
        page_structure["input_evidence_ref"] = page_evidence["evidence_ref"]
    elif mutation == "canonical_page":
        page_ref = f"page:sha256:{'c' * 64}"
        page_structure["page_ref"] = page_ref
        page_evidence["page_ref"] = page_ref
        native_page["page_ref"] = page_ref
        _rebind_native(page_evidence, native_page)
        page_structure["input_evidence_ref"] = page_evidence["evidence_ref"]
    elif mutation == "native_hash":
        native_page["spans"][0]["text"] = "Changed after binding"
    else:
        page_evidence["hashes"]["render_sha256"] = "c" * 64

    result = assess_page_structure_alignment(
        page_structure, page_evidence, native_page
    )

    assert result["processing"] == "failed"
    assert result["quality"] == "unsupported"
    assert result["decision"] == "reject"
    assert result["reason_code"] == expected_reason_code
    assert result["findings"] == []
