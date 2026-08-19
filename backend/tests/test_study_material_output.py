from copy import deepcopy
import json
from pathlib import Path

import pytest

from pdf_evidence.concept_evidence_output import build_output
from pdf_evidence.ocr_page_evidence import canonical_sha256
from pdf_evidence.study_material_output import (
    build_study_material_output,
    validate_study_material_output,
)


def producer_output(*, excluded_page: bool = False):
    source_sha256 = "a" * 64
    page_ref = "page:sha256:" + "1" * 64
    evidence_id = "evidence:sha256:" + "2" * 64
    page = {
        "schema": "page-evidence/v2",
        "material_id": f"material:sha256:{source_sha256}",
        "material_revision": "material-revision:sha256:" + "3" * 64,
        "page_ref": page_ref,
        "page_number": 1,
        "coordinate_space": "unrotated_pdf_points",
        "section_id": "section:sha256:" + "4" * 64,
        "native_evidence_ref": "native-evidence:sha256:" + "5" * 64,
        "geometry": {
            "visible_points": [0.0, 0.0, 420.0, 600.0],
            "unrotated_points": [0.0, 0.0, 420.0, 600.0],
            "rotation_degrees": 0,
            "derotation_matrix": [1.0, 0.0, 0.0, 1.0, 0.0, 0.0],
        },
        "render": {
            "schema": "page-render/v1",
            "policy": "pymupdf-rgb-200dpi/v1",
            "dpi": 200,
            "colorspace": "RGB",
            "format": "PNG",
            "coverage": "full_visible_page",
            "pymupdf_version": "1.28.0",
            "width": 1167,
            "height": 1667,
            "sha256": "a" * 64,
        },
        "evidence_blocks": [
            {
                "evidence_id": evidence_id,
                "block_id": "block:sha256:" + "6" * 64,
                "ocr_type": "text",
                "kind": "paragraph",
                "text": "Public evidence",
                "reading_order": 0,
                "locator": {
                    "page": 1,
                    "block_id": "block:sha256:" + "6" * 64,
                    "region": [72.0, 80.0, 300.0, 120.0],
                },
                "render_region": [200.0, 200.0, 800.0, 400.0],
                "source": "unlimited_ocr",
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
        "processing_policy": "unlimited-ocr-page-evidence/v1",
        "normalizer_policy": "ocr-text-nfkc-whitespace/v1",
        "produced_at": "2026-08-19T00:00:00Z",
        "processing": "partial",
        "quality": "needs_review",
        "decision": "review",
        "reason_codes": ["PAGE_CONTENT_REVIEW_REQUIRED"],
    }
    runtime_lock = json.loads(
        (Path(__file__).parents[2] / "local_ai" / "runtime-lock.json").read_text(
            encoding="utf-8"
        )
    )
    page["input_binding"] = {
        "source_sha256": source_sha256,
        "page_number": 1,
        "render_sha256": page["render"]["sha256"],
        "page": runtime_lock["page"],
        "ocr": runtime_lock["ocr"],
    }
    page["page_evidence_id"] = "page-evidence:sha256:" + canonical_sha256(page)
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
        runtime_binding=runtime_lock,
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


def test_recomputed_output_identity_cannot_hide_nested_unexpected_field():
    output = build_study_material_output(producer_output())
    output["concepts"][0]["unexpected_field"] = True
    identity = dict(output)
    identity.pop("output_id")
    output["output_id"] = "study-material-output:sha256:" + canonical_sha256(identity)
    assert validate_study_material_output(output) == "STUDY_MATERIAL_OUTPUT_INVALID"


@pytest.mark.parametrize(
    ("mutation", "value"),
    [
        (("evidence_index", 0, "region", "bbox", 0), float("nan")),
        (("pages", 0, "page_number"), True),
        (("concepts", 0, "evidence_ids"), []),
    ],
)
def test_closed_output_rejects_nonfinite_type_count_and_reference_mutations(mutation, value):
    output = build_study_material_output(producer_output())
    target = output
    for key in mutation[:-1]:
        target = target[key]
    target[mutation[-1]] = value
    assert validate_study_material_output(output) == "STUDY_MATERIAL_OUTPUT_INVALID"
