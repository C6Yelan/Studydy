from copy import deepcopy
import hashlib
import json

import pytest

from pdf_evidence.concept_candidates import (
    CONCEPT_BODY_SCHEMA,
    CONCEPT_PROMPT,
    CONCEPT_PROMPT_SHA256,
    CONCEPT_PROMPT_VERSION,
    adjudicate_concept_candidate,
    build_concept_context,
    build_provisional_concept_candidate,
)
from pdf_evidence.page_alignment import adjudicate_visual_alignment


def _canonical_sha256(value):
    """以 production 的 canonical JSON 規則計算測試 SHA-256。"""
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validated_page_inputs():
    """建立兩個 heading section 與 accepted alignment 的單頁輸入。"""
    source_sha256 = "a" * 64
    page_number = 1
    page_sha256 = hashlib.sha256(
        f"{source_sha256}:{page_number}".encode("ascii")
    ).hexdigest()
    material_ref = f"material:sha256:{source_sha256}"
    page_ref = f"page:sha256:{page_sha256}"
    evidence_ref = f"evidence:sha256:{'b' * 64}"
    geometry = {"visible_points": [0.0, 0.0, 200.0, 100.0]}
    page_evidence = {
        "schema": "page-evidence/v1",
        "status": "succeeded",
        "material_ref": material_ref,
        "page_ref": page_ref,
        "page_number": page_number,
        "evidence_ref": evidence_ref,
        "geometry": geometry,
        "coordinate_transform": {
            "native_coordinate_space": "unrotated_page_points",
            "rotated_to_point": [1.0, 0.0, 0.0, 1.0, 0.0, 0.0],
        },
    }
    page_structure = {
        "schema": "page-structure/v1",
        "material_ref": material_ref,
        "page_ref": page_ref,
        "page_number": page_number,
        "input_evidence_ref": evidence_ref,
        "coordinate_space": "unrotated_page_points",
        "elements": [
            {
                "id": "heading-1",
                "type": "heading",
                "bbox": [10.0, 10.0, 90.0, 20.0],
                "text": "Synthetic arrays",
            },
            {
                "id": "paragraph-1",
                "type": "paragraph",
                "bbox": [10.0, 25.0, 150.0, 40.0],
                "text": "An array stores values in order.",
            },
            {
                "id": "code-1",
                "type": "code",
                "bbox": [10.0, 45.0, 130.0, 58.0],
                "text": "values[0]",
            },
            {
                "id": "heading-2",
                "type": "heading",
                "bbox": [10.0, 65.0, 100.0, 75.0],
                "text": "Synthetic loops",
            },
            {
                "id": "paragraph-2",
                "type": "paragraph",
                "bbox": [10.0, 80.0, 150.0, 95.0],
                "text": "A loop visits each value.",
            },
        ],
        "reading_order": [
            "heading-1",
            "paragraph-1",
            "code-1",
            "heading-2",
            "paragraph-2",
        ],
        "spatial_relations": [],
    }
    page_structure_sha256 = _canonical_sha256(page_structure)
    page_alignment = {
        "schema": "page-alignment/v1",
        "identity": {
            "material_ref": material_ref,
            "page_ref": page_ref,
            "page_number": page_number,
        },
        "input_binding": {
            "evidence_ref": evidence_ref,
            "page_structure_sha256": page_structure_sha256,
            "native_sha256": "c" * 64,
        },
        "processing": "succeeded",
        "quality": "accepted",
        "decision": "retain",
        "reason_code": "ALIGNMENT_ACCEPTED",
        "findings": [],
    }
    return page_structure, page_evidence, page_alignment


def _context_and_body():
    """建立可產生 provisional candidate 的 context 與外部 body。"""
    page_structure, page_evidence, page_alignment = _validated_page_inputs()
    context = build_concept_context(
        page_structure, page_evidence, page_alignment, "heading-1"
    )
    body = {
        "name": "Array indexing",
        "definition": "Accessing an ordered value by its position.",
        "scope": "The heading section about stored values and index access.",
        "evidence_ids": [
            context["evidence"][0]["evidence_id"],
            context["evidence"][2]["evidence_id"],
        ],
    }
    return context, body


def _candidate():
    """建立可供 immutable adjudication 測試使用的 provisional candidate。"""
    context, body = _context_and_body()
    return build_provisional_concept_candidate(
        context,
        body,
        generation_run_id="concept-generation-run-001",
        generation_identity={
            "role": "concept-generator",
            "model": "local-model-revision-001",
        },
    )


def test_prompt_and_body_schema_are_fixed_and_minimal():
    """驗證固定 prompt identity 與 structured body 只含四個批准欄位。"""
    assert CONCEPT_PROMPT_VERSION == "concept-candidate-prompt/v1"
    assert hashlib.sha256(CONCEPT_PROMPT.encode("utf-8")).hexdigest() == (
        CONCEPT_PROMPT_SHA256
    )
    assert CONCEPT_BODY_SCHEMA["additionalProperties"] is False
    assert set(CONCEPT_BODY_SCHEMA["required"]) == {
        "name",
        "definition",
        "scope",
        "evidence_ids",
    }
    assert set(CONCEPT_BODY_SCHEMA["properties"]) == set(
        CONCEPT_BODY_SCHEMA["required"]
    )
    assert "uniqueItems" not in CONCEPT_BODY_SCHEMA["properties"]["evidence_ids"]


def test_builds_one_heading_section_context_without_changing_inputs():
    """驗證 context 只含 anchor 到下一個 heading 前的同頁連續內容。"""
    page_structure, page_evidence, page_alignment = _validated_page_inputs()
    originals = deepcopy((page_structure, page_evidence, page_alignment))

    context = build_concept_context(
        page_structure, page_evidence, page_alignment, "heading-1"
    )

    assert context["schema"] == "concept-context/v1"
    assert context["anchor_element_id"] == "heading-1"
    assert [element["element_id"] for element in context["elements"]] == [
        "heading-1",
        "paragraph-1",
        "code-1",
    ]
    assert [reference["element_id"] for reference in context["evidence"]] == [
        "heading-1",
        "paragraph-1",
        "code-1",
    ]
    assert all(
        reference["schema"] == "evidence-reference/v1"
        for reference in context["evidence"]
    )
    assert context["input_binding"]["page_structure_sha256"] == (
        _canonical_sha256(page_structure)
    )
    assert context["input_binding"]["alignment_sha256"] == (
        _canonical_sha256(page_alignment)
    )
    assert (page_structure, page_evidence, page_alignment) == originals
    serialized = json.dumps(context)
    assert "native_sha256" not in serialized
    assert "render" not in serialized
    assert "provider" not in serialized
    context["evidence"][0]["region"]["bbox"][0] = -1
    assert page_structure == originals[0]


@pytest.mark.parametrize(
    ("readable_element", "expected_text"),
    [
        (
            {
                "id": "diagram-label-1",
                "type": "diagram_label",
                "bbox": [5.0, 60.0, 100.0, 64.0],
                "text": "Input",
            },
            "Input",
        ),
        (
            {
                "id": "list-1",
                "type": "list",
                "bbox": [5.0, 60.0, 100.0, 64.0],
                "items": ["First item", "Second item"],
            },
            "First item\nSecond item",
        ),
        (
            {
                "id": "formula-1",
                "type": "formula",
                "bbox": [5.0, 60.0, 100.0, 64.0],
                "latex": "x^2 + y^2",
            },
            "x^2 + y^2",
        ),
        (
            {
                "id": "matrix-1",
                "type": "matrix",
                "bbox": [5.0, 60.0, 100.0, 64.0],
                "row_count": 2,
                "column_count": 2,
                "cells": [
                    {"row": 2, "column": 2, "text": "d"},
                    {"row": 1, "column": 2, "text": "b"},
                    {"row": 2, "column": 1, "text": "c"},
                    {"row": 1, "column": 1, "text": "a"},
                ],
            },
            "a | b\nc | d",
        ),
        (
            {
                "id": "table-1",
                "type": "table",
                "bbox": [5.0, 60.0, 100.0, 64.0],
                "row_count": 2,
                "column_count": 2,
                "cells": [
                    {
                        "row": 2,
                        "column": 2,
                        "row_span": 1,
                        "column_span": 1,
                        "role": "data",
                        "text": "Right",
                    },
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
                        "text": "Left",
                    },
                ],
            },
            "Header\nLeft | Right",
        ),
    ],
)
def test_builds_readable_element_text_in_stable_order(
    readable_element, expected_text
):
    """驗證合法 element 依原內容與 row、column 順序產生文字。"""
    page_structure, page_evidence, page_alignment = _validated_page_inputs()
    page_structure["elements"].insert(3, readable_element)
    page_structure["reading_order"].insert(3, readable_element["id"])
    page_alignment["input_binding"]["page_structure_sha256"] = _canonical_sha256(
        page_structure
    )
    originals = deepcopy((page_structure, page_evidence, page_alignment))

    context = build_concept_context(
        page_structure, page_evidence, page_alignment, "heading-1"
    )

    assert context is not None
    assert [element["text"] for element in context["elements"][:3]] == [
        "Synthetic arrays",
        "An array stores values in order.",
        "values[0]",
    ]
    assert context["elements"][3] == {
        "element_id": readable_element["id"],
        "type": readable_element["type"],
        "text": expected_text,
    }
    assert context["evidence"][3]["element_id"] == readable_element["id"]
    assert context["evidence"][3]["region"]["bbox"] == readable_element["bbox"]
    assert (page_structure, page_evidence, page_alignment) == originals


def test_context_rejects_heading_with_only_unreadable_region():
    """驗證不可讀 region 不會被猜成文字，也不會單獨撐起 heading section。"""
    page_structure, page_evidence, page_alignment = _validated_page_inputs()
    page_structure["elements"][1:3] = [
        {
            "id": "region-1",
            "type": "other_visible_region",
            "bbox": [10.0, 25.0, 150.0, 55.0],
            "uncertainty_kind": "unreadable",
        }
    ]
    page_structure["reading_order"] = [
        element["id"] for element in page_structure["elements"]
    ]
    page_alignment["input_binding"]["page_structure_sha256"] = _canonical_sha256(
        page_structure
    )

    assert build_concept_context(
        page_structure, page_evidence, page_alignment, "heading-1"
    ) is None


def test_review_accepted_visual_alignment_can_build_context():
    """驗證保留視覺檢查紀錄的已接受複核結果可建立 context。"""
    page_structure, page_evidence, page_alignment = _validated_page_inputs()
    page_alignment.update(
        {
            "quality": "needs_review",
            "decision": "review",
            "reason_code": "VISION_CONTENT_NEEDS_REVIEW",
            "findings": [{"reason_code": "VISION_CONTENT_PRESENT"}],
        }
    )
    reviewed_alignment = adjudicate_visual_alignment(page_alignment, "retain")
    originals = deepcopy((page_structure, page_evidence, reviewed_alignment))

    context = build_concept_context(
        page_structure, page_evidence, reviewed_alignment, "heading-1"
    )

    assert context is not None
    assert context["input_binding"]["alignment_sha256"] == (
        _canonical_sha256(reviewed_alignment)
    )
    assert reviewed_alignment["findings"] == [
        {"reason_code": "VISION_CONTENT_PRESENT"}
    ]
    assert (page_structure, page_evidence, reviewed_alignment) == originals


@pytest.mark.parametrize(
    "alignment_state", ["raw", "rejected", "empty_finding", "nested_finding_field"]
)
def test_unaccepted_visual_alignment_cannot_build_context(alignment_state):
    """驗證未裁決、已拒絕或 findings 斷鏈的視覺對齊結果不會通過。"""
    page_structure, page_evidence, page_alignment = _validated_page_inputs()
    page_alignment.update(
        {
            "quality": "needs_review",
            "decision": "review",
            "reason_code": "VISION_CONTENT_NEEDS_REVIEW",
            "findings": [{"reason_code": "VISION_CONTENT_PRESENT"}],
        }
    )
    if alignment_state == "rejected":
        page_alignment = adjudicate_visual_alignment(page_alignment, "reject")
    elif alignment_state == "empty_finding":
        page_alignment = adjudicate_visual_alignment(page_alignment, "retain")
        page_alignment["findings"] = []
    elif alignment_state == "nested_finding_field":
        page_alignment = adjudicate_visual_alignment(page_alignment, "retain")
        page_alignment["findings"][0]["extra"] = "invalid"

    assert build_concept_context(
        page_structure, page_evidence, page_alignment, "heading-1"
    ) is None


@pytest.mark.parametrize(
    "invalid_input",
    [
        "structure",
        "alignment_status",
        "alignment_identity",
        "alignment_shape",
        "anchor",
        "empty_section",
    ],
)
def test_context_rejects_unvalidated_or_unbounded_inputs(invalid_input):
    """驗證 structure、alignment、identity 或 heading section 不合格時不產生 context。"""
    page_structure, page_evidence, page_alignment = _validated_page_inputs()
    anchor = "heading-1"
    if invalid_input == "structure":
        page_structure["reading_order"].append("heading-1")
    elif invalid_input == "alignment_status":
        page_alignment["quality"] = "needs_review"
        page_alignment["decision"] = "review"
    elif invalid_input == "alignment_identity":
        page_alignment["identity"]["page_number"] = 2
    elif invalid_input == "alignment_shape":
        page_alignment["input_binding"] = "invalid"
    elif invalid_input == "anchor":
        anchor = "paragraph-1"
    else:
        anchor = "heading-2"
        page_structure["elements"].pop()
        page_structure["reading_order"].pop()
        page_alignment["input_binding"]["page_structure_sha256"] = (
            _canonical_sha256(page_structure)
        )

    assert build_concept_context(
        page_structure, page_evidence, page_alignment, anchor
    ) is None


def test_builds_grounded_development_only_provisional_candidate():
    """驗證 valid body 只保存已知 Evidence 子集、生成來源 identity 與 context lineage。"""
    context, body = _context_and_body()
    originals = deepcopy((context, body))

    candidate = build_provisional_concept_candidate(
        context,
        body,
        generation_run_id="concept-generation-run-001",
        generation_identity={
            "role": "concept-generator",
            "model": "local-model-revision-001",
        },
    )

    assert candidate["development_only"] is True
    assert candidate["processing"] == "succeeded"
    assert candidate["quality"] == "needs_review"
    assert candidate["decision"] == "review"
    assert candidate["reason_code"] == "CONCEPT_CANDIDATE_NEEDS_REVIEW"
    assert candidate["generation_run_id"] == "concept-generation-run-001"
    assert candidate["generation_identity"] == {
        "role": "concept-generator",
        "model": "local-model-revision-001",
    }
    assert candidate["prompt_identity"] == {
        "version": CONCEPT_PROMPT_VERSION,
        "sha256": CONCEPT_PROMPT_SHA256,
    }
    assert candidate["context_binding"]["context_sha256"] == _canonical_sha256(
        context
    )
    assert [reference["evidence_id"] for reference in candidate["evidence"]] == (
        body["evidence_ids"]
    )
    assert candidate["evidence"][0]["element_id"] == "heading-1"
    assert candidate["candidate_id"].startswith("concept-candidate:sha256:")
    assert (context, body) == originals
    serialized = json.dumps(candidate)
    assert "native" not in serialized
    assert "provider" not in serialized
    assert "confidence" not in serialized


@pytest.mark.parametrize(
    ("invalid_body", "reason_code"),
    [
        ("shape", "CONCEPT_BODY_INVALID"),
        ("empty", "CONCEPT_BODY_INVALID"),
        ("duplicate", "CONCEPT_BODY_INVALID"),
        ("unknown", "CONCEPT_EVIDENCE_INVALID"),
        ("missing_anchor", "CONCEPT_EVIDENCE_INVALID"),
    ],
)
def test_rejects_invalid_body_and_evidence_ids(invalid_body, reason_code):
    """驗證 body shape、空值、重複、未知或缺少 anchor Evidence 都 fail closed。"""
    context, body = _context_and_body()
    if invalid_body == "shape":
        body["extra"] = "not allowed"
    elif invalid_body == "empty":
        body["definition"] = " "
    elif invalid_body == "duplicate":
        body["evidence_ids"].append(body["evidence_ids"][0])
    elif invalid_body == "unknown":
        body["evidence_ids"].append(f"evidence-reference:sha256:{'d' * 64}")
    else:
        body["evidence_ids"] = [context["evidence"][1]["evidence_id"]]

    result = build_provisional_concept_candidate(
        context,
        body,
        generation_run_id="concept-generation-run-001",
        generation_identity={
            "role": "concept-generator",
            "model": "local-model-revision-001",
        },
    )

    assert result == {
        "schema": "concept-candidate/v1",
        "development_only": True,
        "processing": "failed",
        "quality": "unsupported",
        "decision": "reject",
        "reason_code": reason_code,
    }


@pytest.mark.parametrize("missing_identity", ["generation_run", "role", "model"])
def test_missing_generation_run_or_identity_cannot_succeed(missing_identity):
    """驗證 generation run、生成角色或 model 缺少時不會建立成功 candidate。"""
    context, body = _context_and_body()
    generation_run_id = "concept-generation-run-001"
    generation_identity = {
        "role": "concept-generator",
        "model": "local-model-revision-001",
    }
    if missing_identity == "generation_run":
        generation_run_id = ""
    else:
        generation_identity[missing_identity] = ""

    result = build_provisional_concept_candidate(
        context,
        body,
        generation_run_id=generation_run_id,
        generation_identity=generation_identity,
    )

    assert result["processing"] == "failed"
    assert result["reason_code"] == "CONCEPT_LINEAGE_INVALID"


@pytest.mark.parametrize(
    ("decision", "quality", "reason_code"),
    [
        ("retain", "accepted", "CONCEPT_CANDIDATE_ACCEPTED"),
        ("reject", "unsupported", "CONCEPT_CANDIDATE_REJECTED"),
    ],
)
def test_adjudication_changes_only_quality_decision_and_reason(
    decision, quality, reason_code
):
    """驗證 retain/reject snapshot 不改寫語意、Evidence、candidate ID 或 lineage。"""
    candidate = _candidate()
    original = deepcopy(candidate)

    snapshot = adjudicate_concept_candidate(candidate, decision)

    assert snapshot["quality"] == quality
    assert snapshot["decision"] == decision
    assert snapshot["reason_code"] == reason_code
    unchanged_fields = set(candidate) - {"quality", "decision", "reason_code"}
    assert all(snapshot[field] == candidate[field] for field in unchanged_fields)
    assert candidate == original


def test_adjudication_rejects_unknown_decision_or_tampered_candidate():
    """驗證未知裁決或 semantic content 被改寫時不產生 adjudication snapshot。"""
    candidate = _candidate()
    assert adjudicate_concept_candidate(candidate, "review") is None

    candidate["definition"] = "Tampered definition"
    assert adjudicate_concept_candidate(candidate, "retain") is None
