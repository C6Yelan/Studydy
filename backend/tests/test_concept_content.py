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
    CONCEPT_CONTENT_BODY_SCHEMA,
    CONCEPT_CONTENT_PROMPT,
    CONCEPT_CONTENT_PROMPT_SHA256,
    CONCEPT_CONTENT_SCHEMA,
    CONCEPT_CONTENT_PROMPT_VERSION,
    RELATION_CLUE_KINDS,
    RELATION_DIRECTIONS,
    build_concept_content,
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
        "schema": "page-evidence/v1",
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
        "schema": "page-alignment/v1",
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
        generation_run_id=f"concept-generation-{material_key}-{page_number}",
        generation_identity={
            "role": "concept-generator",
            "model": "local-model-revision-001",
        },
    )
    return adjudicate_concept_candidate(provisional, "retain")


def _groups(names, *, material_key="material-a"):
    """以 accepted candidates 建立跨頁群組。"""
    candidates = [
        _accepted_candidate(material_key, index, name)
        for index, name in enumerate(names, start=1)
    ]
    return group_concept_candidates(candidates)


def _concept_content_body(context):
    """建立可由各測試局部改動的有效摘要與關聯線索。"""
    source_group, target_group = context["groups"]
    source_evidence_id = source_group["members"][0]["evidence_ids"][0]
    target_evidence_id = target_group["members"][0]["evidence_ids"][0]
    return {
        "summary": "Arrays store values, and loops can visit them.",
        "summary_evidence_ids": [source_evidence_id, target_evidence_id],
        "relation_clues": [
            {
                "kind": "application",
                "source_group_id": source_group["group_id"],
                "target_group_id": target_group["group_id"],
                "statement": "A loop can visit values stored in an array.",
                "direction_hint": "source_to_target",
                "evidence_ids": [source_evidence_id, target_evidence_id],
            }
        ],
    }


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


def test_unknown_summary_evidence_fails_without_summary_text():
    """驗證摘要引用未知 Evidence 時不會保留未驗證文字。"""
    context = build_summary_context(_groups(["Array"]))
    body = {
        "summary": "Arrays store ordered values.",
        "evidence_ids": ["evidence-reference:sha256:unknown"],
    }

    result = build_evidence_summary(context, body)

    assert result["processing"] == "failed"
    assert result["quality"] == "unsupported"
    assert result["decision"] == "reject"
    assert result["reason_code"] == "EVIDENCE_SUMMARY_EVIDENCE_INVALID"
    assert "summary" not in result
    assert "evidence_ids" not in result


def test_combined_content_schema_and_prompt_are_fixed_and_bounded():
    """驗證模型輸入契約只暴露批准欄位、列舉與字數上限。"""
    assert CONCEPT_CONTENT_SCHEMA == "concept-content/v3"
    assert CONCEPT_CONTENT_PROMPT_VERSION == "concept-content-prompt/v4"
    assert CONCEPT_CONTENT_PROMPT_SHA256 == hashlib.sha256(
        CONCEPT_CONTENT_PROMPT.encode("utf-8")
    ).hexdigest()
    assert "summary must state only claims found in the source material" in (
        CONCEPT_CONTENT_PROMPT
    )
    assert (
        "Do not use the summary to describe the input, concept-group count, Evidence IDs, "
        "prompt, model, processing state, source availability or sufficiency, or missing context."
        in CONCEPT_CONTENT_PROMPT
    )
    assert "Use similar only when the cited material supports" in CONCEPT_CONTENT_PROMPT
    assert "never convert contrast into confusing" in CONCEPT_CONTENT_PROMPT
    assert "Similar and confusing must use bidirectional" in CONCEPT_CONTENT_PROMPT
    assert (
        "Prerequisite, contains, application, and example must use source_to_target"
        in CONCEPT_CONTENT_PROMPT
    )
    assert CONCEPT_CONTENT_BODY_SCHEMA["required"] == [
        "summary",
        "summary_evidence_ids",
        "relation_clues",
    ]
    assert set(CONCEPT_CONTENT_BODY_SCHEMA["properties"]) == {
        "summary",
        "summary_evidence_ids",
        "relation_clues",
    }
    assert CONCEPT_CONTENT_BODY_SCHEMA["additionalProperties"] is False
    assert CONCEPT_CONTENT_BODY_SCHEMA["properties"]["summary"]["maxLength"] == 1000

    clues_schema = CONCEPT_CONTENT_BODY_SCHEMA["properties"]["relation_clues"]
    clue_schema = clues_schema["items"]
    assert clues_schema["maxItems"] == 8
    assert set(clue_schema["required"]) == {
        "kind",
        "source_group_id",
        "target_group_id",
        "statement",
        "direction_hint",
        "evidence_ids",
    }
    assert set(clue_schema["properties"]) == set(clue_schema["required"])
    assert clue_schema["additionalProperties"] is False
    assert clue_schema["properties"]["statement"]["maxLength"] == 300
    assert clue_schema["properties"]["kind"]["enum"] == list(
        RELATION_CLUE_KINDS
    )
    assert "contains" in RELATION_CLUE_KINDS
    assert "similar" in RELATION_CLUE_KINDS
    assert "confusing" in RELATION_CLUE_KINDS
    assert clue_schema["properties"]["direction_hint"]["enum"] == list(
        RELATION_DIRECTIONS
    )


def test_valid_combined_content_is_evidence_bound_and_needs_review():
    """驗證有效內容只保留最小 clue，並維持 development review 狀態。"""
    context = build_summary_context(_groups(["Array", "Loop"]))
    body = _concept_content_body(context)
    originals = deepcopy((context, body))

    result = build_concept_content(context, body)

    assert result["development_only"] is True
    assert result["material_ref"] == context["material_ref"]
    assert result["source_group_ids"] == [
        group["group_id"] for group in context["groups"]
    ]
    assert result["summary"] == body["summary"]
    assert result["summary_evidence_ids"] == body["summary_evidence_ids"]
    assert result["relation_clues"] == body["relation_clues"]
    assert set(result["relation_clues"][0]) == {
        "kind",
        "source_group_id",
        "target_group_id",
        "statement",
        "direction_hint",
        "evidence_ids",
    }
    assert result["processing"] == "succeeded"
    assert result["quality"] == "needs_review"
    assert result["decision"] == "review"
    assert result["reason_code"] == "CONCEPT_CONTENT_NEEDS_REVIEW"
    result["summary_evidence_ids"].append("changed")
    result["relation_clues"][0]["evidence_ids"].append("changed")
    assert (context, body) == originals


@pytest.mark.parametrize("kind", ["similar", "confusing"])
def test_symmetric_clues_are_bidirectional_and_evidence_bound(kind):
    """對稱線索只有雙向且引用兩端 Evidence 時才能保留。"""
    context = build_summary_context(_groups(["Array", "Loop"]))
    body = _concept_content_body(context)
    clue = body["relation_clues"][0]
    clue["kind"] = kind
    clue["direction_hint"] = "bidirectional"

    result = build_concept_content(context, body)

    assert result["processing"] == "succeeded"
    assert result["relation_clues"] == body["relation_clues"]


@pytest.mark.parametrize("kind", ["similar", "confusing"])
def test_symmetric_clues_fail_closed_for_wrong_direction_or_endpoint(kind):
    context = build_summary_context(_groups(["Array", "Loop"]))
    body = _concept_content_body(context)
    clue = body["relation_clues"][0]
    clue["kind"] = kind

    wrong_direction = build_concept_content(context, body)
    assert wrong_direction["reason_code"] == "CONCEPT_CONTENT_BODY_INVALID"
    assert "relation_clues" not in wrong_direction

    clue["direction_hint"] = "bidirectional"
    clue["target_group_id"] = clue["source_group_id"]
    same_endpoint = build_concept_content(context, body)
    assert same_endpoint["reason_code"] == "CONCEPT_CONTENT_GROUP_INVALID"
    assert "relation_clues" not in same_endpoint


@pytest.mark.parametrize("kind", ["similar", "confusing"])
def test_symmetric_clues_require_evidence_from_both_groups(kind):
    context = build_summary_context(_groups(["Array", "Loop"]))
    body = _concept_content_body(context)
    clue = body["relation_clues"][0]
    clue["kind"] = kind
    clue["direction_hint"] = "bidirectional"
    clue["evidence_ids"] = [clue["evidence_ids"][0]]

    result = build_concept_content(context, body)

    assert result["reason_code"] == "CONCEPT_CONTENT_EVIDENCE_INVALID"
    assert "relation_clues" not in result


def test_empty_relation_clues_succeed_with_explicit_reason():
    """驗證沒有可靠 clue 時誠實回報原因，不建立假 Relation。"""
    context = build_summary_context(_groups(["Array", "Loop"]))
    body = _concept_content_body(context)
    body["relation_clues"] = []

    result = build_concept_content(context, body)

    assert result["processing"] == "succeeded"
    assert result["quality"] == "needs_review"
    assert result["decision"] == "review"
    assert result["relation_clues"] == []
    assert result["reason_code"] == "CONCEPT_CONTENT_NO_RELATION_CLUES"
    assert "relations" not in result
    assert "graph" not in result


def test_reversed_group_order_is_not_a_duplicate_clue():
    """驗證 source 與 target 順序有意義，反向線索可個別保留。"""
    context = build_summary_context(_groups(["Array", "Loop"]))
    body = _concept_content_body(context)
    reversed_clue = deepcopy(body["relation_clues"][0])
    reversed_clue["source_group_id"], reversed_clue["target_group_id"] = (
        reversed_clue["target_group_id"],
        reversed_clue["source_group_id"],
    )
    body["relation_clues"].append(reversed_clue)

    result = build_concept_content(context, body)

    assert result["processing"] == "succeeded"
    assert len(result["relation_clues"]) == 2


@pytest.mark.parametrize("kind", ["similar", "confusing"])
def test_symmetric_semantic_duplicate_rejects_reverse_order_and_new_statement(kind):
    context = build_summary_context(_groups(["Array", "Loop"]))
    body = _concept_content_body(context)
    clue = body["relation_clues"][0]
    clue["kind"] = kind
    clue["direction_hint"] = "bidirectional"
    reversed_clue = deepcopy(clue)
    reversed_clue["source_group_id"], reversed_clue["target_group_id"] = (
        reversed_clue["target_group_id"],
        reversed_clue["source_group_id"],
    )
    reversed_clue["statement"] = "A distinct statement cannot bypass the pair gate."
    body["relation_clues"].append(reversed_clue)

    result = build_concept_content(context, body)

    assert result["reason_code"] == "CONCEPT_CONTENT_CLUE_DUPLICATE"
    assert "relation_clues" not in result


def test_missing_target_evidence_fails_without_unvalidated_text():
    """驗證 relation clue 缺少 target Evidence 時不會帶出未驗證文字。"""
    context = build_summary_context(_groups(["Array", "Loop"]))
    body = _concept_content_body(context)
    clue = body["relation_clues"][0]
    clue["evidence_ids"] = [clue["evidence_ids"][0]]

    result = build_concept_content(context, body)

    assert result["processing"] == "failed"
    assert result["quality"] == "unsupported"
    assert result["decision"] == "reject"
    assert result["reason_code"] == "CONCEPT_CONTENT_EVIDENCE_INVALID"
    assert "summary" not in result
    assert "summary_evidence_ids" not in result
    assert "relation_clues" not in result
    assert "statement" not in result


@pytest.mark.parametrize(
    ("count", "expected_count", "quality", "decision", "reason_code"),
    [
        (2, 2, "accepted", "retain", "CONCEPT_KEYWORDS_ACCEPTED"),
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
        "schema": "concept-keywords/v1",
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
