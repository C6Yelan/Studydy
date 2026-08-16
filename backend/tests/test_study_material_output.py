from copy import deepcopy
import hashlib
import json

import pytest

from knowledge_map.artifacts import (
    build_initial_learning_path,
    build_knowledge_map,
)
from pdf_evidence.study_material_output import (
    build_study_material_output,
    validate_study_material_output,
)


def _sha256_ref(prefix, value):
    """建立只用於 contract fixture 的有效 SHA-256 reference。"""
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"{prefix}{digest}"


def _rebind_output_id(output):
    """在 tamper 測試中重算整份輸出的 canonical ID。"""
    content = {key: value for key, value in output.items() if key != "output_id"}
    encoded = json.dumps(
        content,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    output["output_id"] = (
        f"study-material-output:sha256:{hashlib.sha256(encoded).hexdigest()}"
    )


def _page_items(material_ref, page_number, concept_name):
    """建立可通過既有 Page Structure validator 的單頁 snapshot。"""
    page_ref = _sha256_ref("page:sha256:", f"page-{page_number}")
    page_evidence_ref = _sha256_ref(
        "evidence:sha256:", f"page-evidence-{page_number}"
    )
    page_evidence = {
        "schema": "page-evidence/v1",
        "status": "succeeded",
        "material_ref": material_ref,
        "page_ref": page_ref,
        "page_number": page_number,
        "evidence_ref": page_evidence_ref,
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
        "input_evidence_ref": page_evidence_ref,
        "coordinate_space": "unrotated_page_points",
        "elements": [
            {
                "id": "heading-1",
                "type": "heading",
                "bbox": [10.0, 10.0, 180.0, 30.0],
                "text": concept_name,
            }
        ],
        "reading_order": ["heading-1"],
        "spatial_relations": [],
    }
    return page_evidence, page_structure


def _concept_group(page_evidence, concept_name):
    """建立一個 retained Concept group 與同頁 Evidence locator。"""
    normalized_name = concept_name.casefold()
    evidence_id = _sha256_ref(
        "evidence-reference:sha256:", f"evidence-{normalized_name}"
    )
    group_id = _sha256_ref(
        "concept-group:sha256:", f"group-{normalized_name}"
    )
    candidate_id = _sha256_ref(
        "concept-candidate:sha256:", f"candidate-{normalized_name}"
    )
    group = {
        "schema": "concept-group/v1",
        "group_id": group_id,
        "material_ref": page_evidence["material_ref"],
        "normalized_name": normalized_name,
        "members": [
            {
                "candidate_id": candidate_id,
                "source_page": {
                    "material_ref": page_evidence["material_ref"],
                    "page_ref": page_evidence["page_ref"],
                    "page_number": page_evidence["page_number"],
                },
                "name": concept_name,
                "definition": f"A concise definition of {concept_name}.",
                "scope": f"The use of {concept_name} in this section.",
                "evidence": [
                    {
                        "evidence_id": evidence_id,
                        "schema": "evidence-reference/v1",
                        "material_ref": page_evidence["material_ref"],
                        "page_ref": page_evidence["page_ref"],
                        "page_number": page_evidence["page_number"],
                        "input_evidence_ref": page_evidence["evidence_ref"],
                        "element_id": "heading-1",
                        "region": {
                            "coordinate_space": "unrotated_page_points",
                            "bbox": [10.0, 10.0, 180.0, 30.0],
                        },
                    }
                ],
            }
        ],
        "processing": "succeeded",
        "quality": "accepted",
        "decision": "retain",
        "reason_code": "CONCEPT_GROUP_ACCEPTED",
    }
    return group, evidence_id


def _valid_inputs():
    """建立兩頁、兩個 Concept 與一批已驗證內容的最小輸入。"""
    material_ref = _sha256_ref("material:sha256:", "material-a")
    page_one, structure_one = _page_items(material_ref, 1, "Array")
    page_two, structure_two = _page_items(material_ref, 2, "Loop")
    page_three, structure_three = _page_items(
        material_ref, 3, "Unavailable section"
    )
    array_group, array_evidence_id = _concept_group(page_one, "Array")
    loop_group, loop_evidence_id = _concept_group(page_two, "Loop")
    source_group_ids = [array_group["group_id"], loop_group["group_id"]]
    content = {
        "schema": "concept-content/v3",
        "development_only": True,
        "material_ref": material_ref,
        "source_group_ids": source_group_ids,
        "summary": "Arrays store values, and loops can visit them.",
        "summary_evidence_ids": [array_evidence_id, loop_evidence_id],
        "relation_clues": [
            {
                "kind": "application",
                "source_group_id": array_group["group_id"],
                "target_group_id": loop_group["group_id"],
                "statement": "A loop can visit values stored in an array.",
                "direction_hint": "source_to_target",
                "evidence_ids": [array_evidence_id, loop_evidence_id],
            }
        ],
        "processing": "succeeded",
        "quality": "needs_review",
        "decision": "review",
        "reason_code": "CONCEPT_CONTENT_NEEDS_REVIEW",
    }
    keywords = {
        "schema": "concept-keywords/v1",
        "material_ref": material_ref,
        "keywords": [
            {
                "keyword": array_group["normalized_name"],
                "group_id": array_group["group_id"],
                "evidence_ids": [array_evidence_id],
            },
            {
                "keyword": loop_group["normalized_name"],
                "group_id": loop_group["group_id"],
                "evidence_ids": [loop_evidence_id],
            },
        ],
        "processing": "succeeded",
        "quality": "accepted",
        "decision": "retain",
        "reason_code": "CONCEPT_KEYWORDS_ACCEPTED",
    }
    return {
        "page_evidence_items": [page_one, page_two, page_three],
        "page_structure_items": [
            structure_one,
            structure_two,
            structure_three,
        ],
        "concept_groups": [array_group, loop_group],
        "concept_content_items": [content],
        "concept_keyword_items": [keywords],
        "handoff_id": "study-material-handoff-primary-pages",
        "produced_at": "2026-08-04T12:00:00+08:00",
        "page_limitations": [
            {
                "reason_code": "CONCEPT_CONTEXT_UNAVAILABLE",
                "affected_page_refs": [page_three["page_ref"]],
            },
            {
                "reason_code": "FORMAL_PROVIDER_DEFERRED",
                "affected_page_refs": [
                    page_one["page_ref"],
                    page_two["page_ref"],
                    page_three["page_ref"],
                ],
            },
        ],
        "provenance": {
            "page_evidence": "page-evidence/v1;PyMuPDF-1.28.0",
            "page_structure": "page-structure/v1;structured-generation-loopback/v1",
            "concepts": "concept-group/v1;candidate-prompt-v1",
            "content": "concept-content/v3;concept-content-prompt/v4",
        },
    }


def _output_with_symmetric_clue(
    relation_type,
    *,
    direction_hint="bidirectional",
    include_reverse_duplicate=False,
):
    """建立含相似或易混淆線索的最小跨階段輸出。"""
    inputs = _valid_inputs()
    clue = inputs["concept_content_items"][0]["relation_clues"][0]
    clue["kind"] = relation_type
    clue["direction_hint"] = direction_hint
    clue["statement"] = f"The concepts have a grounded {relation_type} relation."
    if include_reverse_duplicate:
        reverse_clue = deepcopy(clue)
        reverse_clue["source_group_id"], reverse_clue["target_group_id"] = (
            reverse_clue["target_group_id"],
            reverse_clue["source_group_id"],
        )
        reverse_clue["statement"] = (
            f"A differently worded reverse {relation_type} observation."
        )
        inputs["concept_content_items"][0]["relation_clues"].append(
            reverse_clue
        )
    return build_study_material_output(**inputs)


def test_builds_evidence_self_contained_partial_output():
    """驗證合法 partial 輸出欄位、排序、Evidence union 與 review 狀態。"""
    output = build_study_material_output(**_valid_inputs())

    assert set(output) == {
        "schema",
        "output_id",
        "development_only",
        "handoff_id",
        "produced_at",
        "material_ref",
        "pages",
        "concepts",
        "evidence_index",
        "summaries",
        "keywords",
        "relation_clues",
        "known_limitations",
        "provenance",
        "processing",
        "quality",
        "decision",
        "reason_code",
    }
    assert output["output_id"].startswith("study-material-output:sha256:")
    assert [page["page_number"] for page in output["pages"]] == [1, 2, 3]
    assert all(
        set(page)
        == {
            "page_ref",
            "page_number",
            "page_evidence_ref",
            "page_structure_ref",
        }
        for page in output["pages"]
    )
    concept_evidence_ids = {
        evidence_id
        for concept in output["concepts"]
        for member in concept["members"]
        for evidence_id in member["evidence_ids"]
    }
    assert {item["evidence_id"] for item in output["evidence_index"]} == (
        concept_evidence_ids
    )
    assert all(
        set(item)
        == {
            "evidence_id",
            "material_ref",
            "page_ref",
            "page_number",
            "element_id",
            "region",
        }
        for item in output["evidence_index"]
    )
    assert set(output["relation_clues"][0]) == {
        "kind",
        "source_concept_id",
        "target_concept_id",
        "statement",
        "direction_hint",
        "evidence_ids",
    }
    assert "source_concept_ids" in output["summaries"][0]
    assert "concept_id" in output["keywords"][0]
    assert output["processing"] == "partial"
    assert output["quality"] == "needs_review"
    assert output["decision"] == "review"
    assert output["reason_code"] == "DEVELOPMENT_FULL_DOCUMENT_PARTIAL"
    assert validate_study_material_output(output) is None


@pytest.mark.parametrize("relation_type", ["similar", "confusing"])
@pytest.mark.parametrize("include_reverse_duplicate", [False, True])
def test_symmetric_clues_build_one_canonical_relation_without_changing_path(
    relation_type,
    include_reverse_duplicate,
):
    """雙向線索固定端點、語意去重，且不影響 prerequisite 路徑。"""
    source = _output_with_symmetric_clue(
        relation_type,
        include_reverse_duplicate=include_reverse_duplicate,
    )
    knowledge_map = build_knowledge_map(source)
    relations = [
        relation
        for relation in knowledge_map["relations"]
        if relation["type"] == relation_type
    ]

    assert len(relations) == 1
    assert relations[0]["source_concept_id"] < relations[0]["target_concept_id"]
    assert relations[0]["quality"] == "accepted"
    assert relations[0]["decision"] == "retain"

    baseline_map = build_knowledge_map(
        build_study_material_output(**_valid_inputs())
    )
    assert build_initial_learning_path(knowledge_map)[
        "ordered_concept_ids"
    ] == build_initial_learning_path(baseline_map)["ordered_concept_ids"]


@pytest.mark.parametrize("relation_type", ["similar", "confusing"])
def test_directional_symmetric_clue_stays_review_only(relation_type):
    """相似與易混淆若不是 bidirectional，不得升格為正式 Relation。"""
    source = _output_with_symmetric_clue(
        relation_type,
        direction_hint="source_to_target",
    )
    knowledge_map = build_knowledge_map(source)

    assert not any(
        relation["type"] == relation_type
        for relation in knowledge_map["relations"]
    )
    assert any(
        clue["kind"] == relation_type
        and clue["reason_code"] == "RELATION_DIRECTION_NEEDS_REVIEW"
        for clue in knowledge_map["review_items"]
    )


def test_excluded_page_keeps_closed_evidence_lineage_and_partial_status():
    """被排除頁不發布 Structure/Concept，但仍保留可驗的頁面原因。"""
    inputs = _valid_inputs()
    excluded_evidence = inputs["page_evidence_items"].pop()
    inputs["page_structure_items"].pop()
    source_sha256 = excluded_evidence["material_ref"].removeprefix(
        "material:sha256:"
    )
    excluded_evidence["page_ref"] = "page:sha256:" + hashlib.sha256(
        f"{source_sha256}:{excluded_evidence['page_number']}".encode("ascii")
    ).hexdigest()
    retained_refs = [
        page["page_ref"] for page in inputs["page_evidence_items"]
    ]
    inputs["page_limitations"] = [
        {
            "reason_code": "FORMAL_PROVIDER_DEFERRED",
            "affected_page_refs": retained_refs,
        },
        {
            "reason_code": "PAGE_CONTENT_EXCLUDED",
            "affected_pages": [
                {
                    "page_ref": excluded_evidence["page_ref"],
                    "page_number": excluded_evidence["page_number"],
                    "page_evidence_ref": excluded_evidence["evidence_ref"],
                    "last_stage": "page_structure",
                    "processing": "failed",
                    "quality": "unsupported",
                    "decision": "reject",
                    "reason_code": "PAGE_STRUCTURE_INVALID",
                }
            ],
        },
    ]

    output = build_study_material_output(**inputs)

    assert validate_study_material_output(output) is None
    assert [page["page_number"] for page in output["pages"]] == [1, 2]
    assert output["known_limitations"][1]["affected_pages"][0][
        "page_evidence_ref"
    ] == excluded_evidence["evidence_ref"]
    assert (
        output["processing"], output["quality"], output["decision"],
        output["reason_code"],
    ) == (
        "partial", "needs_review", "review",
        "DEVELOPMENT_FULL_DOCUMENT_PARTIAL",
    )

    tampered = deepcopy(output)
    tampered["known_limitations"][1]["affected_pages"][0]["page_number"] = 2
    _rebind_output_id(tampered)
    assert (
        validate_study_material_output(tampered)
        == "STUDY_MATERIAL_OUTPUT_LIMITATION_INVALID"
    )

    evidence_collision = deepcopy(output)
    evidence_collision["known_limitations"][1]["affected_pages"][0][
        "page_evidence_ref"
    ] = evidence_collision["pages"][0]["page_evidence_ref"]
    _rebind_output_id(evidence_collision)
    assert (
        validate_study_material_output(evidence_collision)
        == "STUDY_MATERIAL_OUTPUT_LIMITATION_INVALID"
    )


def test_unavailable_page_cannot_still_publish_a_concept():
    """同頁若已有 Concept，就不能同時宣稱 context unavailable。"""
    output = build_study_material_output(**_valid_inputs())
    unavailable = next(
        item
        for item in output["known_limitations"]
        if item["reason_code"] == "CONCEPT_CONTEXT_UNAVAILABLE"
    )
    unavailable["affected_page_refs"] = [
        output["concepts"][0]["members"][0]["page_ref"]
    ]
    _rebind_output_id(output)

    assert (
        validate_study_material_output(output)
        == "STUDY_MATERIAL_OUTPUT_STATUS_INVALID"
    )


def test_unsupported_contract_values_fail_closed():
    """builder 與 validator 都拒絕不受支援的契約值。"""
    inputs = _valid_inputs()
    inputs["concept_content_items"][0]["schema"] = "concept-content/unsupported"

    assert build_study_material_output(**inputs) == {
        "schema": "study-material-output/v2",
        "development_only": True,
        "processing": "failed",
        "quality": "unsupported",
        "decision": "reject",
        "reason_code": "STUDY_MATERIAL_OUTPUT_CONTENT_INPUT_INVALID",
    }

    output = build_study_material_output(**_valid_inputs())
    output["schema"] = "study-material-output/unsupported"
    _rebind_output_id(output)
    assert validate_study_material_output(output) == "STUDY_MATERIAL_OUTPUT_ROOT_INVALID"

    output = build_study_material_output(**_valid_inputs())
    output["relation_clues"][0]["kind"] = "unsupported"
    _rebind_output_id(output)
    assert (
        validate_study_material_output(output)
        == "STUDY_MATERIAL_OUTPUT_REFERENCE_INVALID"
    )

    output = build_study_material_output(**_valid_inputs())
    output["provenance"]["content"] = (
        "concept-content/unsupported;concept-content-prompt/unsupported"
    )
    _rebind_output_id(output)
    assert validate_study_material_output(output) == "STUDY_MATERIAL_OUTPUT_ROOT_INVALID"


def test_output_is_deterministic_and_does_not_mutate_inputs():
    """驗證相同內容不受輸入順序影響，且 builder 不改寫上游 snapshot。"""
    inputs = _valid_inputs()
    original = deepcopy(inputs)
    reordered = deepcopy(inputs)
    reordered["page_evidence_items"].reverse()
    reordered["page_structure_items"].reverse()
    reordered["concept_groups"].reverse()

    first = build_study_material_output(**inputs)
    second = build_study_material_output(**reordered)

    assert first == second
    assert inputs == original


def test_validator_detects_output_id_tamper():
    """驗證任何已輸出內容被改寫時 canonical output ID 會失配。"""
    output = build_study_material_output(**_valid_inputs())
    output["summaries"][0]["summary"] = "Tampered summary."

    assert (
        validate_study_material_output(output)
        == "STUDY_MATERIAL_OUTPUT_ID_INVALID"
    )


def test_validator_rejects_unknown_reference_after_id_rebind():
    """驗證重算 ID 也不能掩蓋 packaged reference 斷鏈。"""
    output = build_study_material_output(**_valid_inputs())
    output["concepts"][0]["members"][0]["evidence_ids"] = [
        _sha256_ref("evidence-reference:sha256:", "unknown")
    ]
    _rebind_output_id(output)

    assert (
        validate_study_material_output(output)
        == "STUDY_MATERIAL_OUTPUT_REFERENCE_INVALID"
    )


def test_relation_clue_missing_target_evidence_fails_closed():
    """驗證 relation clue 的 target Evidence 斷鏈不會洩漏語意內容。"""
    inputs = _valid_inputs()
    clue = inputs["concept_content_items"][0]["relation_clues"][0]
    clue["evidence_ids"] = clue["evidence_ids"][:1]

    output = build_study_material_output(**inputs)

    assert output == {
        "schema": "study-material-output/v2",
        "development_only": True,
        "processing": "failed",
        "quality": "unsupported",
        "decision": "reject",
        "reason_code": "STUDY_MATERIAL_OUTPUT_REFERENCE_INVALID",
    }
    assert "concepts" not in output
    assert "summaries" not in output
    assert "relation_clues" not in output


def test_only_formal_provider_deferred_can_report_succeeded_review():
    """驗證無 context 缺口時仍只回報 development review，不宣稱 stable。"""
    inputs = _valid_inputs()
    inputs["page_limitations"] = [inputs["page_limitations"][1]]

    output = build_study_material_output(**inputs)

    assert output["processing"] == "succeeded"
    assert output["quality"] == "needs_review"
    assert output["decision"] == "review"
    assert output["reason_code"] == "DEVELOPMENT_OUTPUT_NEEDS_REVIEW"
    assert output["known_limitations"] == [
        {
            "reason_code": "FORMAL_PROVIDER_DEFERRED",
            "affected_page_refs": sorted(
                page["page_ref"] for page in inputs["page_evidence_items"]
            ),
        }
    ]
    assert validate_study_material_output(output) is None


def test_completed_stages_accept_empty_limitations_and_replay():
    """全部 current stages完成時，以空限制表達 truthful accepted output。"""
    inputs = _valid_inputs()
    inputs["page_limitations"] = []

    output = build_study_material_output(**inputs)

    assert output["known_limitations"] == []
    assert (
        output["processing"],
        output["quality"],
        output["decision"],
        output["reason_code"],
    ) == ("succeeded", "accepted", "retain", "DEVELOPMENT_OUTPUT_ACCEPTED")
    assert validate_study_material_output(output) is None


def test_completed_status_tamper_fails_closed():
    inputs = _valid_inputs()
    inputs["page_limitations"] = []
    output = build_study_material_output(**inputs)
    output["quality"] = "needs_review"
    output["decision"] = "review"
    output["reason_code"] = "DEVELOPMENT_OUTPUT_NEEDS_REVIEW"
    _rebind_output_id(output)

    assert (
        validate_study_material_output(output)
        == "STUDY_MATERIAL_OUTPUT_STATUS_INVALID"
    )


def test_unknown_limitation_fails_closed():
    inputs = _valid_inputs()
    inputs["page_limitations"] = [
        {
            "reason_code": "UNKNOWN",
            "affected_page_refs": ["page:sha256:" + "0" * 64],
        }
    ]

    output = build_study_material_output(**inputs)

    assert output["processing"] == "failed"
    assert output["quality"] == "unsupported"
    assert output["decision"] == "reject"
    assert output["reason_code"].startswith("STUDY_MATERIAL_OUTPUT_")


def test_empty_limitations_cannot_accept_missing_content_stage():
    inputs = _valid_inputs()
    inputs["page_limitations"] = []
    inputs["concept_content_items"] = []

    output = build_study_material_output(**inputs)

    assert output == {
        "schema": "study-material-output/v2",
        "development_only": True,
        "processing": "failed",
        "quality": "unsupported",
        "decision": "reject",
        "reason_code": "STUDY_MATERIAL_OUTPUT_CONTENT_INPUT_INVALID",
    }
