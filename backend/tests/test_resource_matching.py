from copy import deepcopy
import hashlib
import json

import pytest

from learning_resources.catalog import (
    build_controlled_resource_catalog,
    validate_controlled_resource_catalog,
)
from learning_resources.matching import (
    build_learning_resource_result,
    validate_learning_resource_result,
    validate_resource_match_review,
)
from pdf_evidence.study_material_output import build_study_material_output

RESULT_RUN = {
    "produced_at": "2026-08-08T12:30:00+08:00",
    "run_id": "learning-resource-test-run",
}


def _candidate(
    artifact_path,
    *,
    subject="data_structures",
    title="Open Data Structures",
    topics=None,
    keywords=None,
    source_locator="https://materials.university.edu/open-data-structures",
):
    return {
        "assessment": "accepted",
        "subject": subject,
        "title": title,
        "topics": topics or ["array", "stack"],
        "keywords": keywords or ["data structure", "complexity"],
        "source_locator": source_locator,
        "artifact_ref": artifact_path.name,
        "artifact_sha256": hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
        "license_status": "cc_by",
        "use_boundary": "attribution_required",
        "checked_at": "2026-08-08T12:00:00+08:00",
        "learning_use": "primary",
    }


def _rebind_catalog(catalog):
    content = {
        key: value for key, value in catalog.items() if key != "catalog_revision"
    }
    encoded = json.dumps(
        content,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    catalog["catalog_revision"] = (
        "resource-catalog:sha256:" + hashlib.sha256(encoded).hexdigest()
    )
    return catalog


def _sha256_ref(prefix, value):
    return prefix + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _study_material_output(concept_name="array", summary="Arrays store values."):
    """用既有 S2 builder 建立可由 matching 公開 validator 讀取的 fixture。"""
    material_ref = _sha256_ref("material:sha256:", "learning-resource-material")
    page_ref = _sha256_ref("page:sha256:", "learning-resource-page")
    page_evidence_ref = _sha256_ref(
        "evidence:sha256:", "learning-resource-page-evidence"
    )
    evidence_id = _sha256_ref(
        "evidence-reference:sha256:", "learning-resource-evidence"
    )
    concept_id = _sha256_ref(
        "concept-group:sha256:", f"learning-resource-{concept_name}"
    )
    page_evidence = {
        "schema": "page-evidence/v1",
        "status": "succeeded",
        "material_ref": material_ref,
        "page_ref": page_ref,
        "page_number": 1,
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
        "page_number": 1,
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
    concept_group = {
        "schema": "concept-group/v1",
        "group_id": concept_id,
        "material_ref": material_ref,
        "normalized_name": concept_name.casefold(),
        "members": [
            {
                "candidate_id": _sha256_ref(
                    "concept-candidate:sha256:",
                    f"learning-resource-{concept_name}",
                ),
                "source_page": {
                    "material_ref": material_ref,
                    "page_ref": page_ref,
                    "page_number": 1,
                },
                "name": concept_name,
                "definition": f"A concise definition of {concept_name}.",
                "scope": f"The use of {concept_name} in this section.",
                "evidence": [
                    {
                        "evidence_id": evidence_id,
                        "schema": "evidence-reference/v1",
                        "material_ref": material_ref,
                        "page_ref": page_ref,
                        "page_number": 1,
                        "input_evidence_ref": page_evidence_ref,
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
    content = {
        "schema": "concept-content/v2",
        "development_only": True,
        "material_ref": material_ref,
        "source_group_ids": [concept_id],
        "summary": summary,
        "summary_evidence_ids": [evidence_id],
        "relation_clues": [],
        "processing": "succeeded",
        "quality": "needs_review",
        "decision": "review",
        "reason_code": "CONCEPT_CONTENT_NO_RELATION_CLUES",
    }
    keyword = {
        "schema": "concept-keywords/v1",
        "material_ref": material_ref,
        "keywords": [
            {
                "keyword": concept_name.casefold(),
                "group_id": concept_id,
                "evidence_ids": [evidence_id],
            }
        ],
        "processing": "succeeded",
        "quality": "accepted",
        "decision": "retain",
        "reason_code": "CONCEPT_KEYWORDS_ACCEPTED",
    }
    return build_study_material_output(
        [page_evidence],
        [page_structure],
        [concept_group],
        [content],
        [keyword],
        handoff_id="learning-resource-s2-fixture",
        produced_at="2026-08-08T12:00:00+08:00",
        page_limitations=[
            {
                "reason_code": "FORMAL_PROVIDER_DEFERRED",
                "affected_page_refs": [page_ref],
            }
        ],
        provenance={
            "page_evidence": "page-evidence/v1;PyMuPDF-1.28.0",
            "page_structure": "page-structure/v1;prompt-v1",
            "concepts": "concept-group/v1;candidate-prompt-v1",
            "content": "concept-content/v2;concept-content-prompt/v3",
        },
    )


def _canonical_id(prefix, content):
    encoded = json.dumps(
        content,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    return prefix + hashlib.sha256(encoded).hexdigest()


def _review(study_material_output, catalog, evidence_terms):
    resource = catalog["resources"][0]
    content = {
        "schema": "resource-match-review/v1",
        "source_s2_revision": study_material_output["output_id"],
        "catalog_revision": catalog["catalog_revision"],
        "concept_id": study_material_output["concepts"][0]["concept_id"],
        "resource_key": resource["resource_key"],
        "review_basis": "summary",
        "evidence_terms": evidence_terms,
        "reviewed_at": "2026-08-08T12:20:00+08:00",
        "review_run_id": "resource-review-test-run",
        "processing": "succeeded",
        "quality": "accepted",
        "decision": "retain",
        "reason_code": "RESOURCE_MATCH_REVIEW_ACCEPTED",
    }
    return {
        "review_id": _canonical_id("resource-match-review:sha256:", content),
        **content,
    }


def _rebind_result_revision(result):
    content = {
        key: value
        for key, value in result.items()
        if key not in {"result_revision", "produced_at", "run_id"}
    }
    result["result_revision"] = _canonical_id(
        "learning-resource-result:sha256:", content
    )
    return result

@pytest.mark.parametrize(
    ("title", "topics", "keywords", "match_basis"),
    [
        ("Array learning", ["sequence"], ["index"], "explicit_name"),
        ("Indexing guide", ["sequence"], ["array"], "explicit_keyword"),
    ],
)
def test_matches_explicit_concept_text(
    tmp_path, title, topics, keywords, match_basis
):
    """明確名稱或 keyword 只以輸入文字配對，並保留原始資源欄位。"""
    artifact_path = tmp_path / "source-list.xlsx"
    artifact_path.write_bytes(b"approved source list")
    catalog = build_controlled_resource_catalog(
        [
            _candidate(
                artifact_path,
                title=title,
                topics=topics,
                keywords=keywords,
            )
        ],
        tmp_path,
    )
    study_material_output = _study_material_output()

    result = build_learning_resource_result(
        study_material_output, catalog, tmp_path, "data_structures", **RESULT_RUN
    )
    repeated = build_learning_resource_result(
        study_material_output, catalog, tmp_path, "data_structures", **RESULT_RUN
    )
    comparison_run = build_learning_resource_result(
        study_material_output,
        catalog,
        tmp_path,
        "data_structures",
        produced_at="2026-08-08T13:00:00+08:00",
        run_id="learning-resource-comparison-run",
    )

    assert validate_learning_resource_result(
        result, study_material_output, catalog, tmp_path
    ) is None
    assert result == repeated
    assert result != comparison_run
    assert result["result_revision"] == comparison_run["result_revision"]
    assert len(result["resources"]) == 1
    resource = result["resources"][0]
    assert resource["subject"] == "data_structures"
    assert "source_name" not in resource
    assert "license_status" not in resource
    assert resource["match_basis"] == match_basis
    assert resource["matched_terms"] == ["array"]
    assert resource["resource_id"].startswith("learning-resource:sha256:")
    for field in (
        "title",
        "source_locator",
        "artifact_sha256",
        "use_boundary",
    ):
        assert resource[field] == catalog["resources"][0][field]


def test_subject_filter_runs_before_text_matching(tmp_path):
    artifact_path = tmp_path / "source-list.xlsx"
    artifact_path.write_bytes(b"approved source list")
    candidates = [
        _candidate(artifact_path, keywords=["array"]),
        _candidate(
            artifact_path,
            subject="e_commerce",
            title="Array commerce",
            topics=["array"],
            keywords=["array"],
            source_locator="https://materials.university.edu/open-e-commerce",
        ),
    ]
    catalog = build_controlled_resource_catalog(candidates, tmp_path)
    study_material_output = _study_material_output()

    result = build_learning_resource_result(
        study_material_output, catalog, tmp_path, "data_structures", **RESULT_RUN
    )

    assert len(catalog["resources"]) == 2
    assert len(result["resources"]) == 1
    expected = next(
        resource
        for resource in catalog["resources"]
        if resource["subject"] == "data_structures"
    )
    assert result["resources"][0]["resource_key"] == expected["resource_key"]


def test_summary_only_match_requires_bound_accepted_review(tmp_path):
    artifact_path = tmp_path / "source-list.xlsx"
    artifact_path.write_bytes(b"approved source list")
    catalog = build_controlled_resource_catalog(
        [
            _candidate(
                artifact_path,
                title="Queue fundamentals",
                topics=["queue"],
                keywords=["queue"],
            )
        ],
        tmp_path,
    )
    study_material_output = _study_material_output(
        "fifo", "A queue provides first-in first-out ordering."
    )
    review = _review(study_material_output, catalog, ["queue"])

    assert review["review_basis"] == "summary"
    assert review["evidence_terms"] == ["queue"]
    assert review["review_run_id"] == "resource-review-test-run"

    without_review = build_learning_resource_result(
        study_material_output, catalog, tmp_path, "data_structures", **RESULT_RUN
    )
    with_review = build_learning_resource_result(
        study_material_output,
        catalog,
        tmp_path,
        "data_structures",
        [review],
        **RESULT_RUN,
    )

    assert without_review["resources"] == []
    assert validate_resource_match_review(
        review, study_material_output, catalog, tmp_path
    ) is None
    assert with_review["resources"][0]["match_basis"] == "approved_summary_review"
    assert validate_learning_resource_result(
        with_review, study_material_output, catalog, tmp_path, [review]
    ) is None


def test_no_match_succeeds_without_changing_s2(tmp_path):
    artifact_path = tmp_path / "source-list.xlsx"
    artifact_path.write_bytes(b"approved source list")
    catalog = build_controlled_resource_catalog(
        [
            _candidate(
                artifact_path,
                title="Graph theory",
                topics=["graph"],
                keywords=["graph"],
            )
        ],
        tmp_path,
    )
    study_material_output = _study_material_output()
    before = json.dumps(
        study_material_output,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    source_status = (
        study_material_output["output_id"],
        study_material_output["processing"],
        study_material_output["quality"],
        study_material_output["decision"],
        study_material_output["reason_code"],
    )

    result = build_learning_resource_result(
        study_material_output, catalog, tmp_path, "data_structures", **RESULT_RUN
    )

    after = json.dumps(
        study_material_output,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    assert result["resources"] == []
    assert (
        result["processing"],
        result["quality"],
        result["decision"],
        result["reason_code"],
    ) == ("succeeded", "accepted", "retain", "NO_CONTROLLED_RESOURCE_MATCH")
    assert before == after
    assert source_status == (
        study_material_output["output_id"],
        study_material_output["processing"],
        study_material_output["quality"],
        study_material_output["decision"],
        study_material_output["reason_code"],
    )


def test_review_and_problem_sources_never_become_matches(tmp_path):
    artifact_path = tmp_path / "source-list.xlsx"
    artifact_path.write_bytes(b"approved source list")
    catalog = build_controlled_resource_catalog(
        [
            {
                "assessment": "review",
                "title": "Array review source",
            },
            {
                "assessment": "problem",
                "title": "Array problem source",
            },
        ],
        tmp_path,
    )
    study_material_output = _study_material_output()

    result = build_learning_resource_result(
        study_material_output, catalog, tmp_path, "data_structures", **RESULT_RUN
    )

    assert catalog["resources"] == []
    assert [item["reason_code"] for item in catalog["exclusions"]] == [
        "RESOURCE_SOURCE_NEEDS_REVIEW",
        "RESOURCE_SOURCE_PROBLEM",
    ]
    assert result["resources"] == []
    assert (
        result["processing"],
        result["quality"],
        result["decision"],
        result["reason_code"],
    ) == ("succeeded", "accepted", "retain", "NO_CONTROLLED_RESOURCE_MATCH")


def test_result_tamper_and_changed_artifact_fail_closed(tmp_path):
    artifact_path = tmp_path / "source-list.xlsx"
    artifact_path.write_bytes(b"approved source list")
    catalog = build_controlled_resource_catalog([_candidate(artifact_path)], tmp_path)
    study_material_output = _study_material_output()
    result = build_learning_resource_result(
        study_material_output, catalog, tmp_path, "data_structures", **RESULT_RUN
    )

    wrong_reference = deepcopy(result)
    wrong_reference["source_s2_revision"] = (
        "study-material-output:sha256:" + "0" * 64
    )
    assert (
        validate_learning_resource_result(
            _rebind_result_revision(wrong_reference),
            study_material_output,
            catalog,
            tmp_path,
        )
        == "LEARNING_RESOURCE_BINDING_INVALID"
    )
    wrong_revision = deepcopy(result)
    wrong_revision["result_revision"] = (
        "learning-resource-result:sha256:" + "0" * 64
    )
    assert (
        validate_learning_resource_result(
            wrong_revision, study_material_output, catalog, tmp_path
        )
        == "LEARNING_RESOURCE_REVISION_INVALID"
    )
    wrong_artifact_hash = deepcopy(result)
    wrong_artifact_hash["resources"][0]["artifact_sha256"] = "0" * 64
    assert (
        validate_learning_resource_result(
            _rebind_result_revision(wrong_artifact_hash),
            study_material_output,
            catalog,
            tmp_path,
        )
        == "LEARNING_RESOURCE_BINDING_INVALID"
    )
    omitted_match = deepcopy(result)
    omitted_match["resources"] = []
    assert (
        validate_learning_resource_result(
            _rebind_result_revision(omitted_match),
            study_material_output,
            catalog,
            tmp_path,
        )
        == "LEARNING_RESOURCE_MATCH_INVALID"
    )
    artifact_path.write_bytes(b"changed after matching")
    assert (
        validate_learning_resource_result(
            result, study_material_output, catalog, tmp_path
        )
        == "LEARNING_RESOURCE_CATALOG_INVALID"
    )


def test_review_binding_and_terms_tamper_fail_closed(tmp_path):
    artifact_path = tmp_path / "source-list.xlsx"
    artifact_path.write_bytes(b"approved source list")
    catalog = build_controlled_resource_catalog(
        [
            _candidate(
                artifact_path,
                title="Queue fundamentals",
                topics=["queue"],
                keywords=["queue"],
            )
        ],
        tmp_path,
    )
    study_material_output = _study_material_output(
        "fifo", "A queue provides first-in first-out ordering."
    )
    review = _review(study_material_output, catalog, ["queue"])
    wrong_binding = deepcopy(review)
    wrong_binding["source_s2_revision"] = (
        "study-material-output:sha256:" + "0" * 64
    )
    wrong_binding["review_id"] = _canonical_id(
        "resource-match-review:sha256:",
        {key: value for key, value in wrong_binding.items() if key != "review_id"},
    )
    invented_term = _review(study_material_output, catalog, ["invented"])

    assert (
        validate_resource_match_review(
            wrong_binding, study_material_output, catalog, tmp_path
        )
        == "RESOURCE_MATCH_REVIEW_BINDING_INVALID"
    )
    assert (
        validate_resource_match_review(
            invented_term, study_material_output, catalog, tmp_path
        )
        == "RESOURCE_MATCH_REVIEW_TERMS_INVALID"
    )
    assert build_learning_resource_result(
        study_material_output,
        catalog,
        tmp_path,
        "data_structures",
        [wrong_binding],
        **RESULT_RUN,
    ) == {
        "schema": "learning-resource-result/v1",
        "processing": "failed",
        "quality": "unsupported",
        "decision": "reject",
        "reason_code": "RESOURCE_MATCH_REVIEW_INVALID",
    }
