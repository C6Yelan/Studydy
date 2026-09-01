from copy import deepcopy
import json
from pathlib import Path

import pytest

from pdf_evidence.concept_evidence_output import build_output
from pdf_evidence.concept_generation import (
    build_semantic_request,
    claim_id,
    concept_id,
)
from pdf_evidence.document_context import build_document_contexts
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
        "schema": "page-evidence/v3",
        "material_id": f"material:sha256:{source_sha256}",
        "material_revision": "material-revision:sha256:" + "3" * 64,
        "page_ref": page_ref,
        "page_number": 1,
        "coordinate_space": "unrotated_pdf_points",
        "section_id": "section:sha256:" + "4" * 64,
        "native_evidence_ref": "native-evidence:sha256:" + "5" * 64,
        "route": "OCR_needed",
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
        "processing_policy": "native-first-page-evidence/v2",
        "normalizer_policy": "ocr-text-nfc-line-preserving/v1",
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
        "route": "OCR_needed",
        "page": runtime_lock["page"],
        "ocr": runtime_lock["ocr"],
    }
    page["page_evidence_id"] = "page-evidence:sha256:" + canonical_sha256(page)
    first_claim = {
        "text": "Public definition",
        "evidence_ids": [evidence_id],
    }
    first_claim = {
        "claim_id": claim_id(page_ref, first_claim, index=0),
        **first_claim,
    }
    point = {
        "text": "Public key point",
        "evidence_ids": [evidence_id],
    }
    point = {
        "claim_id": claim_id(page_ref, point, index=1),
        **point,
    }
    claims = [first_claim, point]
    semantic_page = {
        "schema": "semantic-page-concepts/v4",
        "page_ref": page_ref,
        "concepts": [
            {
                "concept_id": concept_id(
                    page_ref, "Public concept", claims
                ),
                "page_ref": page_ref,
                "label": "Public concept",
                "claims": claims,
                "processing": "partial",
                "quality": "needs_review",
                "decision": "review",
                "reason_codes": ["SEMANTIC_REVIEW_REQUIRED"],
            }
        ],
        "rejected_candidates": [],
        "processing": "partial",
        "quality": "needs_review",
        "decision": "review",
        "reason_codes": ["SEMANTIC_REVIEW_REQUIRED"],
        "attempt": 1,
        "processing_policy": "claim-grounded-concept-review/v9",
    }
    document_contexts = build_document_contexts([page])
    semantic_request, _ = build_semantic_request(page, document_contexts[0])
    semantic_page["input_binding"] = {
        "batch_bindings": [{
            "batch_index": 0,
            "semantic_request_sha256": canonical_sha256(semantic_request),
            "semantic_request": deepcopy(semantic_request),
        }]
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
        context_pages=[page],
        document_contexts=document_contexts,
        semantic_pages=[semantic_page],
        runtime_binding=runtime_lock,
        run_reasons=[],
        excluded_pages=excluded,
    )


def test_rejected_semantic_candidate_marks_page_partial_for_downstream():
    source = producer_output()
    page = deepcopy(source["pages"][0])
    page["processing"] = "succeeded"
    page["reason_codes"] = ["CONTENT_REVIEW_REQUIRED"]
    page_identity = dict(page)
    page_identity.pop("page_evidence_id")
    page["page_evidence_id"] = (
        "page-evidence:sha256:" + canonical_sha256(page_identity)
    )
    document_contexts = build_document_contexts([page])
    semantic_request, _ = build_semantic_request(page, document_contexts[0])
    output = build_output(
        run_id=source["run_id"],
        produced_at=source["produced_at"],
        source_binding=source["source_binding"],
        pages=[page],
        context_pages=[page],
        document_contexts=document_contexts,
            semantic_pages=[{
                "schema": "semantic-page-concepts/v4",
                "page_ref": page["page_ref"],
            "concepts": [],
            "rejected_candidates": [{
                "candidate_index": 0,
                "processing": "failed",
                "quality": "needs_review",
                "decision": "reject",
                "reason_codes": ["CLAIM_EVIDENCE_UNSUPPORTED"],
            }],
                "processing": "partial",
                "quality": "needs_review",
                "decision": "review",
                "reason_codes": ["SEMANTIC_REVIEW_REQUIRED"],
                "attempt": 1,
                "processing_policy": "claim-grounded-concept-review/v9",
            "input_binding": {
                "batch_bindings": [{
                    "batch_index": 0,
                    "semantic_request_sha256": canonical_sha256(
                        semantic_request
                    ),
                    "semantic_request": deepcopy(semantic_request),
                }]
            },
        }],
        runtime_binding=source["runtime_binding"],
        run_reasons=[],
    )

    assert output["processing"] == "partial"
    assert output["pages"][0]["processing"] == "partial"
    assert build_study_material_output(output)["processing"] == "partial"


def test_build_v8_keeps_context_evidence_claim_locator_and_image_lite():
    source = producer_output()
    output = build_study_material_output(source)
    assert output["schema"] == "study-material-output/v8"
    assert output["document_contexts"] == source["document_contexts"]
    assert output["document_contexts"][0]["current_blocks"][0]["block_id"] == (
        source["pages"][0]["evidence_blocks"][0]["block_id"]
    )
    assert output["processing"] == "partial"
    assert output["pages"][0]["processing"] == "partial"
    assert output["concepts"][0]["processing"] == "partial"
    assert output["concepts"][0]["claims"][0]["evidence_ids"] == [
        output["evidence_index"][0]["evidence_id"]
    ]
    assert output["evidence_index"][0]["region"] == {
        "coordinate_space": "unrotated_pdf_points",
        "bbox": [72.0, 80.0, 300.0, 120.0],
    }
    assert output["evidence_text_index"] == [
        {
            "evidence_id": output["evidence_index"][0]["evidence_id"],
            "text": "Public evidence",
        }
    ]
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
    source["concepts"][0]["claims"][0]["evidence_ids"] = ["evidence:sha256:" + "f" * 64]
    with pytest.raises(ValueError, match="STUDY_MATERIAL_SOURCE_INVALID"):
        build_study_material_output(source)


def test_output_identity_tamper_is_rejected():
    output = build_study_material_output(producer_output())
    tampered = deepcopy(output)
    tampered["concepts"][0]["claims"][0]["text"] = "Changed"
    assert validate_study_material_output(tampered) == "STUDY_MATERIAL_OUTPUT_INVALID"


def test_recomputed_output_identity_cannot_hide_nested_unexpected_field():
    output = build_study_material_output(producer_output())
    output["concepts"][0]["unexpected_field"] = True
    identity = dict(output)
    identity.pop("output_id")
    output["output_id"] = "study-material-output:sha256:" + canonical_sha256(identity)
    assert validate_study_material_output(output) == "STUDY_MATERIAL_OUTPUT_INVALID"


def test_recomputed_identity_cannot_hide_context_evidence_tamper():
    output = build_study_material_output(producer_output())
    context = output["document_contexts"][0]
    context["current_blocks"][0]["evidence_id"] = (
        "evidence:sha256:" + "f" * 64
    )
    context_identity = dict(context)
    context_identity.pop("context_id")
    context["context_id"] = (
        "document-context:sha256:" + canonical_sha256(context_identity)
    )
    identity = dict(output)
    identity.pop("output_id")
    output["output_id"] = (
        "study-material-output:sha256:" + canonical_sha256(identity)
    )

    assert validate_study_material_output(output) == "STUDY_MATERIAL_OUTPUT_INVALID"


@pytest.mark.parametrize(
    ("mutation", "value"),
    [
        (("evidence_index", 0, "region", "bbox", 0), float("nan")),
        (("pages", 0, "page_number"), True),
        (("concepts", 0, "claims", 0, "evidence_ids"), []),
        (("evidence_text_index", 0, "text"), ""),
        (("evidence_text_index", 0, "evidence_id"), "evidence:sha256:" + "f" * 64),
    ],
)
def test_closed_output_rejects_nonfinite_type_count_and_reference_mutations(mutation, value):
    output = build_study_material_output(producer_output())
    target = output
    for key in mutation[:-1]:
        target = target[key]
    target[mutation[-1]] = value
    assert validate_study_material_output(output) == "STUDY_MATERIAL_OUTPUT_INVALID"


def test_zero_concepts_preserves_the_page_and_stays_partial_review():
    source = producer_output()
    source["concepts"] = []
    source["processing"] = "partial"
    source["reason_codes"] = ["CONTENT_REVIEW_REQUIRED", "PAGE_CONTENT_UNUSABLE"]
    identity = dict(source)
    identity.pop("output_id")
    source["output_id"] = "concept-evidence-output:sha256:" + canonical_sha256(identity)
    output = build_study_material_output(source)
    assert output["concepts"] == []
    assert [page["page_number"] for page in output["pages"]] == [1]
    assert output["processing"] == "partial"
