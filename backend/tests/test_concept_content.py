from copy import deepcopy
import hashlib
import json

import pytest

from pdf_evidence.concept_candidates import (
    adjudicate_concept_candidate,
    build_concept_context,
    build_provisional_concept_candidate,
)
from pdf_evidence.concept_content import (
    build_concept_keywords,
    build_evidence_summary,
    build_summary_context,
)
from pdf_evidence.concept_deduplication import group_concept_candidates


def _accepted_candidate(
    material_key,
    page_number,
    name,
    *,
    definition="An ordered collection accessed by position.",
    scope="Stored values and positional access.",
):
    """經由既有 candidate producer 建立 accepted snapshot。"""
    source_sha256 = hashlib.sha256(material_key.encode("utf-8")).hexdigest()
    page_sha256 = hashlib.sha256(
        f"{source_sha256}:{page_number}".encode("ascii")
    ).hexdigest()
    material_ref = f"material:sha256:{source_sha256}"
    page_ref = f"page:sha256:{page_sha256}"
    evidence_sha256 = hashlib.sha256(
        f"{page_sha256}:evidence".encode("ascii")
    ).hexdigest()
    evidence_ref = f"evidence:sha256:{evidence_sha256}"
    page_evidence = {
        "schema": "s1-page-evidence/v1",
        "status": "succeeded",
        "material_ref": material_ref,
        "page_ref": page_ref,
        "page_number": page_number,
        "evidence_ref": evidence_ref,
        "geometry": {"visible_points": [0.0, 0.0, 200.0, 100.0]},
        "coordinate_transform": {
            "native_coordinate_space": "unrotated_page_points",
            "rotated_to_point": [1.0, 0.0, 0.0, 1.0, 0.0, 0.0],
        },
    }
    page_structure = {
        "schema": "s1-page-structure/v1",
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
                "text": name,
            },
            {
                "id": "paragraph-1",
                "type": "paragraph",
                "bbox": [10.0, 25.0, 180.0, 45.0],
                "text": definition,
            },
        ],
        "reading_order": ["heading-1", "paragraph-1"],
        "spatial_relations": [],
    }
    structure_sha256 = hashlib.sha256(
        json.dumps(
            page_structure,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    page_alignment = {
        "schema": "s1-page-alignment/v1",
        "identity": {
            "material_ref": material_ref,
            "page_ref": page_ref,
            "page_number": page_number,
        },
        "input_binding": {
            "evidence_ref": evidence_ref,
            "page_structure_sha256": structure_sha256,
            "native_sha256": "c" * 64,
        },
        "processing": "succeeded",
        "quality": "accepted",
        "decision": "retain",
        "reason_code": "ALIGNMENT_ACCEPTED",
        "findings": [],
    }
    context = build_concept_context(
        page_structure, page_evidence, page_alignment, "heading-1"
    )
    provisional = build_provisional_concept_candidate(
        context,
        {
            "name": name,
            "definition": definition,
            "scope": scope,
            "evidence_ids": [
                reference["evidence_id"] for reference in context["evidence"]
            ],
        },
        handoff_id=f"candidate-handoff-{material_key}-{page_number}",
        sol_identity={"role": "candidate-producer", "model": "fixture-model"},
    )
    return adjudicate_concept_candidate(provisional, "retain")


def _groups(names, *, material_key="material-a"):
    """以 accepted candidates 建立跨頁群組。"""
    candidates = [
        _accepted_candidate(material_key, index, name)
        for index, name in enumerate(names, start=1)
    ]
    return group_concept_candidates(candidates)


def test_builds_minimal_same_material_summary_context_without_mutation():
    """驗證摘要 context 只保留必要內容、頁面 identity 與 Evidence ID。"""
    groups = _groups(["Array", "Loop"])
    originals = deepcopy(groups)

    context = build_summary_context(groups)

    assert set(context) == {"schema", "material_ref", "groups"}
    assert context["material_ref"] == groups[0]["material_ref"]
    assert len(context["groups"]) == 2
    for group in context["groups"]:
        assert set(group) == {"group_id", "normalized_name", "members"}
        for member in group["members"]:
            assert set(member) == {
                "candidate_id",
                "page_ref",
                "page_number",
                "name",
                "definition",
                "scope",
                "evidence_ids",
            }
    serialized = json.dumps(context)
    assert "bbox" not in serialized
    assert "native" not in serialized
    assert "render" not in serialized
    assert "payload" not in serialized
    context["groups"][0]["members"][0]["evidence_ids"].append("changed")
    assert groups == originals


def test_summary_context_accepts_eight_groups_and_rejects_unbounded_input():
    """驗證摘要 context 的群組數量上限固定為八。"""
    bounded_context = build_summary_context(
        _groups([f"Concept {index}" for index in range(8)])
    )
    assert len(bounded_context["groups"]) == 8
    unbounded_groups = _groups([f"Concept {index}" for index in range(9)])
    assert build_summary_context(unbounded_groups) is None


def test_summary_context_rejects_mixed_materials():
    """驗證單一摘要 context 不會混合不同教材。"""
    first = _groups(["Array"], material_key="material-a")[0]
    second = _groups(["Loop"], material_key="material-b")[0]

    assert build_summary_context([first, second]) is None


def test_valid_summary_is_evidence_bound_and_needs_review():
    """驗證有效摘要只綁定已知 Evidence，並固定等待 review。"""
    context = build_summary_context(_groups(["Array", "Loop"]))
    evidence_ids = [
        context["groups"][0]["members"][0]["evidence_ids"][0],
        context["groups"][1]["members"][0]["evidence_ids"][0],
    ]
    body = {
        "summary": "Arrays store values, while loops visit them.",
        "evidence_ids": evidence_ids,
    }
    originals = deepcopy((context, body))

    result = build_evidence_summary(context, body)

    assert result["development_only"] is True
    assert result["material_ref"] == context["material_ref"]
    assert result["source_group_ids"] == [
        group["group_id"] for group in context["groups"]
    ]
    assert result["summary"] == body["summary"]
    assert result["evidence_ids"] == evidence_ids
    assert result["processing"] == "succeeded"
    assert result["quality"] == "needs_review"
    assert result["decision"] == "review"
    assert result["reason_code"] == "EVIDENCE_SUMMARY_NEEDS_REVIEW"
    result["evidence_ids"].append("changed")
    assert (context, body) == originals


@pytest.mark.parametrize(
    ("invalid_body", "reason_code"),
    [
        ("extra", "EVIDENCE_SUMMARY_BODY_INVALID"),
        ("missing", "EVIDENCE_SUMMARY_BODY_INVALID"),
        ("empty_summary", "EVIDENCE_SUMMARY_BODY_INVALID"),
        ("empty_evidence", "EVIDENCE_SUMMARY_BODY_INVALID"),
        ("unknown_evidence", "EVIDENCE_SUMMARY_EVIDENCE_INVALID"),
    ],
)
def test_invalid_summary_body_fails_without_summary_text(
    invalid_body, reason_code
):
    """驗證摘要 body 或 Evidence 無效時不會保留未驗證文字。"""
    context = build_summary_context(_groups(["Array"]))
    body = {
        "summary": "Arrays store ordered values.",
        "evidence_ids": [
            context["groups"][0]["members"][0]["evidence_ids"][0]
        ],
    }
    if invalid_body == "extra":
        body["extra"] = "invalid"
    elif invalid_body == "missing":
        del body["summary"]
    elif invalid_body == "empty_summary":
        body["summary"] = " "
    elif invalid_body == "empty_evidence":
        body["evidence_ids"] = []
    else:
        body["evidence_ids"] = ["evidence-reference:sha256:unknown"]

    result = build_evidence_summary(context, body)

    assert result["processing"] == "failed"
    assert result["quality"] == "unsupported"
    assert result["decision"] == "reject"
    assert result["reason_code"] == reason_code
    assert "summary" not in result
    assert "evidence_ids" not in result


@pytest.mark.parametrize(
    ("count", "expected_count", "quality", "decision", "reason_code"),
    [
        (2, 2, "accepted", "retain", "CONCEPT_KEYWORDS_ACCEPTED"),
        (3, 3, "accepted", "retain", "CONCEPT_KEYWORDS_ACCEPTED"),
        (9, 8, "needs_review", "review", "CONCEPT_KEYWORDS_LIMIT_APPLIED"),
    ],
)
def test_keywords_preserve_small_counts_and_apply_deterministic_limit(
    count, expected_count, quality, decision, reason_code
):
    """驗證關鍵字不補數，超過八個時只取排序後前八個。"""
    groups = _groups([f"Concept {index:02d}" for index in range(count)])
    originals = deepcopy(groups)

    result = build_concept_keywords(list(reversed(groups)))

    assert len(result["keywords"]) == expected_count
    assert [keyword["keyword"] for keyword in result["keywords"]] == [
        f"concept {index:02d}" for index in range(expected_count)
    ]
    assert all(keyword["evidence_ids"] for keyword in result["keywords"])
    assert result["processing"] == "succeeded"
    assert result["quality"] == quality
    assert result["decision"] == decision
    assert result["reason_code"] == reason_code
    assert groups == originals


def test_zero_keywords_returns_fixed_missing_evidence_reason():
    """驗證沒有群組時不建立假 Keyword，並回傳固定失敗原因。"""
    assert build_concept_keywords([]) == {
        "schema": "s1-concept-keywords/v1",
        "keywords": [],
        "processing": "failed",
        "quality": "unsupported",
        "decision": "reject",
        "reason_code": "CONCEPT_KEYWORDS_EVIDENCE_MISSING",
    }


def test_group_review_status_propagates_to_keywords_and_summary():
    """驗證來源語意衝突可進入 context，且衍生內容維持 review。"""
    first = _accepted_candidate("material-a", 1, "Array")
    second = _accepted_candidate(
        "material-a",
        2,
        "array",
        definition="A contiguous memory region.",
    )
    groups = group_concept_candidates([first, second])

    context = build_summary_context(groups)
    summary = build_evidence_summary(
        context,
        {
            "summary": "The source definitions require review.",
            "evidence_ids": [
                context["groups"][0]["members"][0]["evidence_ids"][0]
            ],
        },
    )
    keywords = build_concept_keywords(groups)

    assert summary["quality"] == "needs_review"
    assert summary["decision"] == "review"
    assert keywords["quality"] == "needs_review"
    assert keywords["decision"] == "review"
    assert keywords["reason_code"] == "CONCEPT_KEYWORDS_SOURCE_NEEDS_REVIEW"
