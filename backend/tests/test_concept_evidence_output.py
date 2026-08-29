from copy import deepcopy

from pdf_evidence.artifact_reason_codes import formal_reason_code
from pdf_evidence.concept_evidence_output import validate_output_document
from pdf_evidence.concept_generation import claim_id, concept_id
from pdf_evidence.document_context import build_document_contexts
from pdf_evidence.ocr_page_evidence import canonical_sha256
from test_study_material_output import producer_output


def _reidentify_output(output):
    output["document_contexts"] = build_document_contexts(output["pages"])
    contexts_by_page = {
        context["page_ref"]: context for context in output["document_contexts"]
    }
    for batch in output["semantic_batches"]:
        batch["source_context_id"] = contexts_by_page[batch["page_ref"]][
            "context_id"
        ]
    identity = dict(output)
    identity.pop("output_id")
    output["output_id"] = (
        "concept-evidence-output:sha256:" + canonical_sha256(identity)
    )


def _reidentify_page(page):
    identity = dict(page)
    identity.pop("page_evidence_id")
    page["page_evidence_id"] = "page-evidence:sha256:" + canonical_sha256(
        identity
    )


def _two_page_output():
    output = producer_output()
    second_page = deepcopy(output["pages"][0])
    second_page_ref = "page:sha256:" + "b" * 64
    second_evidence_id = "evidence:sha256:" + "c" * 64
    second_page["page_ref"] = second_page_ref
    second_page["page_number"] = 2
    second_page["input_binding"]["page_number"] = 2
    second_page["evidence_blocks"][0]["evidence_id"] = second_evidence_id
    second_block_id = "block:sha256:" + "e" * 64
    second_page["evidence_blocks"][0]["block_id"] = second_block_id
    second_page["evidence_blocks"][0]["locator"]["block_id"] = second_block_id
    second_page["evidence_blocks"][0]["locator"]["page"] = 2
    second_page["images"][0]["image_id"] = "image:sha256:" + "d" * 64
    second_page["images"][0]["caption_evidence_ids"] = [second_evidence_id]
    _reidentify_page(second_page)

    second_concept = deepcopy(output["concepts"][0])
    second_concept["page_ref"] = second_page_ref
    second_concept["definition"]["evidence_ids"] = [second_evidence_id]
    second_concept["key_points"][0]["evidence_ids"] = [second_evidence_id]
    second_concept["definition"]["claim_id"] = claim_id(
        second_page_ref,
        "definition",
        {
            "text": second_concept["definition"]["text"],
            "evidence_ids": [second_evidence_id],
        },
    )
    second_concept["key_points"][0]["claim_id"] = claim_id(
        second_page_ref,
        "key_point",
        {
            "text": second_concept["key_points"][0]["text"],
            "evidence_ids": [second_evidence_id],
        },
        index=0,
    )
    second_concept["concept_id"] = concept_id(
        second_page_ref,
        second_concept["label"],
        second_concept["definition"],
        second_concept["key_points"],
    )
    output["pages"].append(second_page)
    output["concepts"].append(second_concept)
    output["semantic_batches"].append(
        {
            "page_ref": second_page_ref,
            "batch_index": 0,
            "semantic_request_sha256": "e" * 64,
            "document_context_id": "concept-context:sha256:" + "d" * 64,
            "source_context_id": "document-context:sha256:" + "c" * 64,
        }
    )
    output["source_binding"]["page_numbers"] = [1, 2]
    _reidentify_output(output)
    return output


def test_reason_codes_are_normalized_at_the_formal_boundary():
    assert formal_reason_code("SOURCE_INVALID") == "SOURCE_INVALID"
    assert formal_reason_code("MEDIA_TYPE_INVALID") == "SOURCE_INVALID"
    assert formal_reason_code("CHILD_TIMEOUT") == "PROCESS_TIMEOUT"
    assert formal_reason_code("UNKNOWN_EVIDENCE_ID") == "EVIDENCE_GROUNDING_INVALID"
    assert formal_reason_code("MODEL_OUTPUT_INVALID_JSON") == "MODEL_OUTPUT_INVALID"
    assert formal_reason_code("FINAL_OUTPUT_WRITE_FAILED") == "STORAGE_WRITE_FAILED"
    assert formal_reason_code("SEMANTIC_REVIEW_REQUIRED") == "CONTENT_REVIEW_REQUIRED"
    assert formal_reason_code("NOT_A_REASON") == "INTERNAL_FAILURE"


def test_recomputed_page_and_output_identity_cannot_hide_unexpected_field():
    output = producer_output()
    output["pages"][0]["evidence_blocks"][0]["unexpected_field"] = True
    page_identity = dict(output["pages"][0])
    page_identity.pop("page_evidence_id")
    output["pages"][0]["page_evidence_id"] = (
        "page-evidence:sha256:" + canonical_sha256(page_identity)
    )
    output_identity = dict(output)
    output_identity.pop("output_id")
    output["output_id"] = "concept-evidence-output:sha256:" + canonical_sha256(
        output_identity
    )
    assert validate_output_document(output) is False


def test_page_and_concept_references_remain_page_local_after_reidentification():
    output = producer_output()
    page = output["pages"][0]
    page["images"][0]["caption_evidence_ids"] = ["evidence:sha256:" + "f" * 64]
    _reidentify_page(page)
    _reidentify_output(output)
    assert validate_output_document(output) is False

    output = producer_output()
    output["concepts"][0]["definition"]["evidence_ids"] = ["evidence:sha256:" + "f" * 64]
    _reidentify_output(output)
    assert validate_output_document(output) is False


def test_included_page_does_not_require_a_concept():
    output = _two_page_output()
    output["concepts"].pop()
    _reidentify_output(output)
    output["processing"] = "partial"
    output["reason_codes"] = ["CONTENT_REVIEW_REQUIRED", "PAGE_CONTENT_UNUSABLE"]
    _reidentify_output(output)
    assert validate_output_document(output) is True


def test_evidence_ids_must_be_unique_across_included_pages():
    output = _two_page_output()
    assert validate_output_document(output) is True

    duplicate_block = deepcopy(output["pages"][1]["evidence_blocks"][0])
    duplicate_block["block_id"] = "block:sha256:" + "f" * 64
    duplicate_block["locator"]["page"] = 1
    duplicate_block["locator"]["block_id"] = duplicate_block["block_id"]
    duplicate_block["reading_order"] = 1
    output["pages"][0]["evidence_blocks"].append(duplicate_block)
    _reidentify_page(output["pages"][0])
    identity = dict(output)
    identity.pop("output_id")
    output["output_id"] = (
        "concept-evidence-output:sha256:" + canonical_sha256(identity)
    )

    assert validate_output_document(output) is False


def test_presemantic_context_keeps_excluded_sibling_lineage():
    output = _two_page_output()
    first_page, second_page = output["pages"]
    first_context = build_document_contexts([first_page, second_page])[0]
    output["pages"] = [first_page]
    output["concepts"] = [
        concept
        for concept in output["concepts"]
        if concept["page_ref"] == first_page["page_ref"]
    ]
    output["document_contexts"] = [first_context]
    output["semantic_batches"] = [
        batch
        for batch in output["semantic_batches"]
        if batch["page_ref"] == first_page["page_ref"]
    ]
    output["semantic_batches"][0]["source_context_id"] = first_context[
        "context_id"
    ]
    output["excluded_pages"] = [{
        "page_ref": second_page["page_ref"],
        "page_number": second_page["page_number"],
        "page_evidence_id": second_page["page_evidence_id"],
        "last_stage": "concept",
        "processing": "failed",
        "quality": "needs_review",
        "decision": "reject",
        "reason_codes": ["PAGE_CONTENT_UNUSABLE"],
    }]
    output["processing"] = "partial"
    output["reason_codes"] = [
        "CONTENT_REVIEW_REQUIRED",
        "PAGE_CONTENT_EXCLUDED",
    ]
    identity = dict(output)
    identity.pop("output_id")
    output["output_id"] = (
        "concept-evidence-output:sha256:" + canonical_sha256(identity)
    )

    assert validate_output_document(output) is True
    assert any(
        block["page_ref"] == second_page["page_ref"]
        for block in output["document_contexts"][0]["context_blocks"]
    )


def test_output_rejects_detailed_reason_code():
    output = producer_output()
    output["reason_codes"] = ["SEMANTIC_REVIEW_REQUIRED"]
    _reidentify_output(output)
    assert validate_output_document(output) is False


def test_durable_context_tamper_fails_even_with_recomputed_output_identity():
    output = producer_output()
    context = output["document_contexts"][0]
    context["current_blocks"][0]["block_id"] = "block:sha256:" + "f" * 64
    context_identity = dict(context)
    context_identity.pop("context_id")
    context["context_id"] = (
        "document-context:sha256:" + canonical_sha256(context_identity)
    )
    identity = dict(output)
    identity.pop("output_id")
    output["output_id"] = (
        "concept-evidence-output:sha256:" + canonical_sha256(identity)
    )

    assert validate_output_document(output) is False
