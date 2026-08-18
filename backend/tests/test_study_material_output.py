from copy import deepcopy

import pytest

from pdf_evidence.concept_evidence_output import build_output
from pdf_evidence.study_material_output import (
    build_study_material_output,
    validate_study_material_output,
)


def producer_output(*, excluded_page: bool = False):
    source_sha256 = "a" * 64
    page_ref = "page:sha256:" + "1" * 64
    evidence_id = "evidence:sha256:" + "2" * 64
    page = {
        "schema": "page-evidence/v1",
        "material_id": f"material:sha256:{source_sha256}",
        "material_revision": "material-revision:sha256:" + "3" * 64,
        "page_ref": page_ref,
        "page_number": 1,
        "coordinate_space": "unrotated_pdf_points",
        "page_evidence_id": "page-evidence:sha256:" + "4" * 64,
        "native_evidence_ref": "native-evidence:sha256:" + "5" * 64,
        "evidence_blocks": [
            {
                "evidence_id": evidence_id,
                "kind": "paragraph",
                "locator": {
                    "page": 1,
                    "block_id": "block:sha256:" + "6" * 64,
                    "region": [72.0, 80.0, 300.0, 120.0],
                },
            }
        ],
        "images": [
            {
                "image_id": "image:sha256:" + "7" * 64,
                "image_hash": "8" * 64,
                "region": [72.0, 140.0, 300.0, 260.0],
                "caption_evidence_ids": [evidence_id],
                "nearby_evidence_ids": [],
            }
        ],
        "reason_codes": ["PAGE_CONTENT_REVIEW_REQUIRED"],
    }
    semantic_page = {
        "page_ref": page_ref,
        "concepts": [
            {
                "concept_id": "concept:sha256:" + "9" * 64,
                "page_ref": page_ref,
                "label": "Public concept",
                "definition": "Public definition",
                "key_points": ["Public key point"],
                "evidence_ids": [evidence_id],
                "processing": "partial",
                "quality": "needs_review",
                "decision": "review",
                "reason_codes": ["SEMANTIC_REVIEW_REQUIRED"],
            }
        ],
        "rejected_candidates": [],
        "reason_codes": ["SEMANTIC_REVIEW_REQUIRED"],
    }
    excluded = (
        [
            {
                "page_ref": "page:sha256:" + "b" * 64,
                "page_number": 2,
                "page_evidence_id": None,
                "last_stage": "page_evidence",
                "processing": "failed",
                "quality": "needs_review",
                "decision": "reject",
                "reason_codes": ["NO_USABLE_EVIDENCE"],
            }
        ]
        if excluded_page
        else []
    )
    return build_output(
        run_id="text-first-run:00000000-0000-4000-8000-000000000001",
        produced_at="2026-08-19T00:00:00Z",
        source_binding={
            "source_sha256": source_sha256,
            "page_numbers": [1, 2] if excluded_page else [1],
        },
        pages=[page],
        semantic_pages=[semantic_page],
        runtime_binding={"schema": "fixed-runtime"},
        run_reasons=[],
        excluded_pages=excluded,
    )


def test_build_v3_keeps_exact_same_page_pdf_locator_and_image_lite():
    source = producer_output()
    output = build_study_material_output(source)
    assert output["schema"] == "study-material-output/v3"
    assert output["processing"] == "succeeded"
    assert output["concepts"][0]["processing"] == "succeeded"
    assert output["concepts"][0]["evidence_ids"] == [
        output["evidence_index"][0]["evidence_id"]
    ]
    assert output["evidence_index"][0]["region"] == {
        "coordinate_space": "unrotated_pdf_points",
        "bbox": [72.0, 80.0, 300.0, 120.0],
    }
    assert output["images"][0]["caption_evidence_ids"] == [
        output["evidence_index"][0]["evidence_id"]
    ]
    assert validate_study_material_output(output, source) is None


def test_excluded_page_is_partial_review_reject_without_silent_truncation():
    output = build_study_material_output(producer_output(excluded_page=True))
    assert output["processing"] == "partial"
    assert [page["page_number"] for page in output["pages"]] == [1]
    assert output["excluded_pages"][0]["page_number"] == 2
    assert (
        output["excluded_pages"][0]["processing"],
        output["excluded_pages"][0]["quality"],
        output["excluded_pages"][0]["decision"],
    ) == ("failed", "needs_review", "reject")


def test_cross_page_or_unknown_evidence_fails_closed():
    source = producer_output()
    source["concepts"][0]["evidence_ids"] = ["evidence:sha256:" + "f" * 64]
    with pytest.raises(ValueError, match="STUDY_MATERIAL_SOURCE_INVALID"):
        build_study_material_output(source)


def test_output_identity_tamper_is_rejected():
    output = build_study_material_output(producer_output())
    tampered = deepcopy(output)
    tampered["concepts"][0]["definition"] = "Changed"
    assert validate_study_material_output(tampered) == "STUDY_MATERIAL_OUTPUT_INVALID"
