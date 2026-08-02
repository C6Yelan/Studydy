from copy import deepcopy
import hashlib
import json

import pytest

from pdf_evidence.concept_candidates import (
    adjudicate_concept_candidate,
    build_concept_context,
    build_provisional_concept_candidate,
)
from pdf_evidence.concept_deduplication import (
    group_concept_candidates,
    normalize_concept_name,
)


def _accepted_candidate(
    material_key,
    page_number,
    name,
    *,
    definition="An ordered collection accessed by position.",
    scope="Stored values and positional access.",
):
    """經由正式 candidate producer 建立可供分組的 accepted snapshot。"""
    source_sha256 = hashlib.sha256(material_key.encode("utf-8")).hexdigest()
    page_sha256 = hashlib.sha256(
        f"{source_sha256}:{page_number}".encode("ascii")
    ).hexdigest()
    material_ref = f"material:sha256:{source_sha256}"
    page_ref = f"page:sha256:{page_sha256}"
    evidence_ref = (
        "evidence:sha256:"
        f"{hashlib.sha256(f'{page_sha256}:evidence'.encode('ascii')).hexdigest()}"
    )
    geometry = {"visible_points": [0.0, 0.0, 200.0, 100.0]}
    page_evidence = {
        "schema": "s1-page-evidence/v1",
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


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("  Ａrray\tINDEX  ", "array index"),
        ("Array-index", "array-index"),
        ("Arrays", "arrays"),
        ("資料　結構", "資料 結構"),
    ],
)
def test_normalizes_only_unicode_case_and_whitespace(name, expected):
    """驗證正規化不會移除標點、轉換單複數或翻譯名稱。"""
    assert normalize_concept_name(name) == expected


@pytest.mark.parametrize("invalid_name", [None, 1, " \t "])
def test_rejects_invalid_normalization_input(invalid_name):
    """驗證非字串或空白名稱不會形成分組鍵。"""
    assert normalize_concept_name(invalid_name) is None


def test_groups_exact_names_with_stable_members_and_lineage():
    """驗證同教材 exact name 分組可重現，並保留頁面與 Evidence lineage。"""
    first = _accepted_candidate("material-a", 2, " Ａrray   INDEX ")
    second = _accepted_candidate("material-a", 1, "array index")
    candidates = [first, second]
    originals = deepcopy(candidates)

    groups = group_concept_candidates(candidates)
    reversed_groups = group_concept_candidates(list(reversed(candidates)))

    assert groups == reversed_groups
    assert len(groups) == 1
    group = groups[0]
    assert group["group_id"].startswith("concept-group:sha256:")
    assert group["normalized_name"] == "array index"
    assert group["processing"] == "succeeded"
    assert group["quality"] == "accepted"
    assert group["decision"] == "retain"
    assert group["reason_code"] == "CONCEPT_GROUP_ACCEPTED"
    assert [member["candidate_id"] for member in group["members"]] == sorted(
        [first["candidate_id"], second["candidate_id"]]
    )
    members_by_id = {
        member["candidate_id"]: member for member in group["members"]
    }
    for candidate in candidates:
        member = members_by_id[candidate["candidate_id"]]
        assert member["source_page"] == candidate["identity"]
        assert member["evidence"] == candidate["evidence"]
    group["members"][0]["evidence"][0]["region"]["bbox"][0] = -1
    assert candidates == originals


def test_separates_materials_and_near_names():
    """驗證教材邊界、標點與單複數差異不會被近似合併。"""
    candidates = [
        _accepted_candidate("material-a", 1, "Array"),
        _accepted_candidate("material-b", 1, "array"),
        _accepted_candidate("material-a", 2, "Array!"),
        _accepted_candidate("material-a", 3, "Arrays"),
    ]

    groups = group_concept_candidates(candidates)

    assert len(groups) == 4
    keys = {(group["material_ref"], group["normalized_name"]) for group in groups}
    assert keys == {
        (candidates[0]["identity"]["material_ref"], "array"),
        (candidates[1]["identity"]["material_ref"], "array"),
        (candidates[2]["identity"]["material_ref"], "array!"),
        (candidates[3]["identity"]["material_ref"], "arrays"),
    }


@pytest.mark.parametrize("conflicting_field", ["definition", "scope"])
def test_semantic_conflicts_require_review_without_canonical_overwrite(
    conflicting_field,
):
    """驗證語意衝突只標記 review，並完整保留各 member 原始內容。"""
    first = _accepted_candidate("material-a", 1, "Array")
    changed = {
        "definition": "A contiguous memory region.",
        "scope": "Memory layout and index access.",
    }
    second = _accepted_candidate(
        "material-a",
        2,
        "array",
        **{conflicting_field: changed[conflicting_field]},
    )

    group = group_concept_candidates([first, second])[0]

    assert group["processing"] == "succeeded"
    assert group["quality"] == "needs_review"
    assert group["decision"] == "review"
    assert group["reason_code"] == "CONCEPT_GROUP_SEMANTIC_CONFLICT"
    assert not {"name", "definition", "scope"} & set(group)
    assert {member[conflicting_field] for member in group["members"]} == {
        first[conflicting_field],
        second[conflicting_field],
    }


@pytest.mark.parametrize(
    "invalid_input",
    ["container", "empty", "status", "identity", "evidence", "duplicate"],
)
def test_invalid_required_input_fails_without_partial_groups(invalid_input):
    """驗證必要欄位或狀態無效時不會發布任何部分群組。"""
    first = _accepted_candidate("material-a", 1, "Array")
    second = _accepted_candidate("material-a", 2, "array")
    candidates = [first, second]
    if invalid_input == "container":
        candidates = {"candidate": first}
    elif invalid_input == "empty":
        candidates = []
    elif invalid_input == "status":
        second["quality"] = "needs_review"
        second["decision"] = "review"
    elif invalid_input == "identity":
        second["identity"]["page_number"] = 0
    elif invalid_input == "evidence":
        second["evidence"] = []
    else:
        second = deepcopy(first)
        candidates = [first, second]
    original = deepcopy(candidates)

    assert group_concept_candidates(candidates) is None
    assert candidates == original
