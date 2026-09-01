from copy import deepcopy

from pdf_evidence.artifact_reason_codes import formal_reason_code
from pdf_evidence.concept_evidence_output import validate_output_document
from pdf_evidence.ocr_page_evidence import canonical_sha256
from test_study_material_output import producer_output


def _reidentify(output):
    identity = dict(output)
    identity.pop("output_id")
    output["output_id"] = (
        "concept-evidence-output:sha256:" + canonical_sha256(identity)
    )


def test_reason_codes_are_normalized_at_the_formal_boundary():
    assert formal_reason_code("MEDIA_TYPE_INVALID") == "SOURCE_INVALID"
    assert formal_reason_code("CHILD_TIMEOUT") == "PROCESS_TIMEOUT"
    assert formal_reason_code("UNKNOWN_EVIDENCE_ID") == "EVIDENCE_GROUNDING_INVALID"
    assert formal_reason_code("MODEL_OUTPUT_INVALID_JSON") == "MODEL_OUTPUT_INVALID"
    assert formal_reason_code("FINAL_OUTPUT_WRITE_FAILED") == "STORAGE_WRITE_FAILED"


def test_valid_output_uses_claims_and_semantic_page_outcome():
    output = producer_output()
    assert validate_output_document(output) is True
    assert output["schema"] == "concept-evidence-output/v6"
    assert set(output["concepts"][0]) == {
        "concept_id", "page_ref", "label", "claims", "processing", "quality",
        "decision", "reason_codes",
    }
    assert output["semantic_page_outcomes"] == [{
        "page_ref": output["pages"][0]["page_ref"],
        "processing": "partial",
        "quality": "needs_review",
        "decision": "review",
        "reason_codes": ["CONTENT_REVIEW_REQUIRED"],
    }]


def test_concept_failure_keeps_page_context_and_records_failed_outcome():
    source = producer_output()
    output = deepcopy(source)
    output["concepts"] = []
    output["semantic_page_outcomes"] = [{
        "page_ref": output["pages"][0]["page_ref"],
        "processing": "failed",
        "quality": "needs_review",
        "decision": "reject",
        "reason_codes": ["PROCESS_TIMEOUT"],
    }]
    output["processing"] = "partial"
    output["reason_codes"] = ["CONTENT_REVIEW_REQUIRED", "PAGE_CONTENT_UNUSABLE"]
    _reidentify(output)
    assert validate_output_document(output) is True
    assert output["excluded_pages"] == []
    assert len(output["pages"]) == 1
    assert len(output["document_contexts"]) == 1
    assert output["concepts"] == []
    assert output["semantic_page_outcomes"][0]["processing"] == "failed"
    assert output["semantic_page_outcomes"][0]["decision"] == "reject"
    assert output["processing"] == "partial"


def test_recomputed_identity_cannot_hide_unexpected_field():
    output = producer_output()
    output["concepts"][0]["unexpected"] = True
    _reidentify(output)
    assert validate_output_document(output) is False


def test_cross_page_evidence_reference_is_rejected():
    output = producer_output()
    output["concepts"][0]["claims"][0]["evidence_ids"] = [
        "evidence:sha256:" + "f" * 64
    ]
    _reidentify(output)
    assert validate_output_document(output) is False


def test_failed_outcome_cannot_claim_review_decision():
    output = producer_output()
    output["semantic_page_outcomes"][0]["processing"] = "failed"
    output["semantic_page_outcomes"][0]["decision"] = "review"
    _reidentify(output)
    assert validate_output_document(output) is False
