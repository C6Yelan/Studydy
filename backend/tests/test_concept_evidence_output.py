from copy import deepcopy
from pathlib import Path

import pytest

import pdf_evidence.text_first_bundle as output_module
from pdf_evidence.artifact_reason_codes import (
    FORMAL_REASON_CODES,
    formal_reason_code,
)
from pdf_evidence.concept_evidence_output import validate_output_document
from pdf_evidence.ocr_page_evidence import canonical_sha256
from pdf_evidence.text_first_bundle import build_producer_bundle
from test_study_material_output import producer_output


def _reidentify_output(output):
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
    second_page["evidence_blocks"][0]["locator"]["page"] = 2
    second_page["images"][0]["image_id"] = "image:sha256:" + "d" * 64
    second_page["images"][0]["caption_evidence_ids"] = [second_evidence_id]
    _reidentify_page(second_page)

    second_concept = deepcopy(output["concepts"][0])
    second_concept["concept_id"] = "concept:sha256:" + "e" * 64
    second_concept["page_ref"] = second_page_ref
    second_concept["evidence_ids"] = [second_evidence_id]
    output["pages"].append(second_page)
    output["concepts"].append(second_concept)
    output["source_binding"]["page_numbers"] = [1, 2]
    _reidentify_output(output)
    return output


def test_formal_reason_taxonomy_and_detailed_mapping_are_exact():
    expected_formal = {
        "SOURCE_INVALID",
        "SOURCE_READ_FAILED",
        "SOURCE_HASH_MISMATCH",
        "PDF_ENCRYPTED",
        "RUNTIME_INVALID",
        "RUNTIME_BUSY",
        "RESOURCE_LIMIT_EXCEEDED",
        "PROCESS_TIMEOUT",
        "PROCESS_FAILED",
        "OCR_OUTPUT_INVALID",
        "EVIDENCE_GROUNDING_INVALID",
        "PAGE_CONTENT_UNUSABLE",
        "MODEL_OUTPUT_INVALID",
        "CACHE_RECOVERED",
        "STORAGE_WRITE_FAILED",
        "ARTIFACT_COLLISION",
        "ARTIFACT_INVALID",
        "CONTENT_REVIEW_REQUIRED",
        "PAGE_CONTENT_EXCLUDED",
        "INTERNAL_FAILURE",
    }
    expected_mapping = {
        "MEDIA_TYPE_INVALID": "SOURCE_INVALID",
        "PDF_INVALID": "SOURCE_INVALID",
        "PAGE_SELECTION_INVALID": "SOURCE_INVALID",
        "RUNTIME_BINDING_INVALID": "RUNTIME_INVALID",
        "PROTOCOL_LIMIT_EXCEEDED": "RESOURCE_LIMIT_EXCEEDED",
        "MODEL_INPUT_TOO_LARGE": "RESOURCE_LIMIT_EXCEEDED",
        "CHILD_TIMEOUT": "PROCESS_TIMEOUT",
        "CHILD_EXITED": "PROCESS_FAILED",
        "CHILD_RESPONSE_INVALID": "PROCESS_FAILED",
        "MODEL_OOM": "PROCESS_FAILED",
        "MODEL_GENERATION_FAILED": "PROCESS_FAILED",
        "OCR_LOCATOR_INVALID": "EVIDENCE_GROUNDING_INVALID",
        "INVALID_EVIDENCE_REFERENCES": "EVIDENCE_GROUNDING_INVALID",
        "DUPLICATE_EVIDENCE_REFERENCE": "EVIDENCE_GROUNDING_INVALID",
        "UNKNOWN_EVIDENCE_ID": "EVIDENCE_GROUNDING_INVALID",
        "NO_USABLE_EVIDENCE": "PAGE_CONTENT_UNUSABLE",
        "NO_USABLE_CONCEPT": "PAGE_CONTENT_UNUSABLE",
        "MODEL_OUTPUT_TOO_LARGE": "MODEL_OUTPUT_INVALID",
        "MODEL_OUTPUT_INVALID_JSON": "MODEL_OUTPUT_INVALID",
        "MODEL_OUTPUT_TRUNCATED": "MODEL_OUTPUT_INVALID",
        "CANDIDATE_SCHEMA_INVALID": "MODEL_OUTPUT_INVALID",
        "INVALID_CONCEPT_COUNT": "MODEL_OUTPUT_INVALID",
        "INVALID_TEXT_FIELD": "MODEL_OUTPUT_INVALID",
        "INVALID_KEY_POINTS": "MODEL_OUTPUT_INVALID",
        "CACHE_INVALID": "CACHE_RECOVERED",
        "CACHE_WRITE_FAILED": "STORAGE_WRITE_FAILED",
        "FINAL_OUTPUT_WRITE_FAILED": "STORAGE_WRITE_FAILED",
        "PRODUCER_BUNDLE_WRITE_FAILED": "STORAGE_WRITE_FAILED",
        "PRODUCER_BUNDLE_INVALID": "ARTIFACT_INVALID",
        "PAGE_CONTENT_REVIEW_REQUIRED": "CONTENT_REVIEW_REQUIRED",
        "TRAILING_QUOTE_REMOVED": "CONTENT_REVIEW_REQUIRED",
        "SEMANTIC_REVIEW_REQUIRED": "CONTENT_REVIEW_REQUIRED",
        "KNOWLEDGE_MAP_REVIEW_REQUIRED": "CONTENT_REVIEW_REQUIRED",
    }
    assert set(FORMAL_REASON_CODES) == expected_formal
    assert all(formal_reason_code(reason) == reason for reason in expected_formal)
    assert {
        reason: formal_reason_code(reason) for reason in expected_mapping
    } == expected_mapping
    assert formal_reason_code("NOT_A_REASON") == "INTERNAL_FAILURE"


def bundle_for(output):
    return build_producer_bundle(
        run_id=output["run_id"],
        produced_at=output["produced_at"],
        output=output,
        runtime_binding_sha256="a" * 64,
        reasons=output["reason_codes"],
        duration_ms=1,
        ocr_calls=1,
        concept_calls=1,
        ocr_loads=1,
        concept_loads=1,
        page_count=1,
    )


def test_atomic_publish_writes_exact_success_files(tmp_path):
    output = producer_output()
    bundle = bundle_for(output)
    destination = output_module.publish_run(tmp_path, bundle, output)
    assert {item.name for item in destination.iterdir()} == {
        "concept-evidence-output.json",
        "producer-bundle.json",
    }
    with pytest.raises(FileExistsError):
        output_module.publish_run(tmp_path, bundle, output)


def test_output_write_failure_never_leaves_published_bundle(tmp_path, monkeypatch):
    output = producer_output()
    bundle = bundle_for(output)
    monkeypatch.setattr(
        output_module,
        "_write_new",
        lambda path, encoded: (_ for _ in ()).throw(OSError()),
    )
    with pytest.raises(OSError, match="FINAL_OUTPUT_WRITE_FAILED"):
        output_module.publish_run(tmp_path, bundle, output)
    assert not (tmp_path / "runs" / output["run_id"]).exists()


def test_bundle_write_failure_never_leaves_published_run(tmp_path, monkeypatch):
    output = producer_output()
    bundle = build_producer_bundle(
        run_id=output["run_id"],
        produced_at=output["produced_at"],
        output=None,
        runtime_binding_sha256="a" * 64,
        reasons=["INTERNAL_FAILURE"],
        duration_ms=1,
        ocr_calls=0,
        concept_calls=0,
        page_count=1,
    )
    monkeypatch.setattr(
        output_module,
        "_write_new",
        lambda path, encoded: (_ for _ in ()).throw(OSError()),
    )

    with pytest.raises(OSError, match="PRODUCER_BUNDLE_WRITE_FAILED"):
        output_module.publish_run(tmp_path, bundle, None)
    assert not (tmp_path / "runs" / output["run_id"]).exists()


def test_verified_reader_rejects_tamper_symlink_and_traversal(tmp_path):
    output = producer_output()
    bundle = build_producer_bundle(
        run_id=output["run_id"], produced_at=output["produced_at"], output=None,
        runtime_binding_sha256="a" * 64, reasons=["INTERNAL_FAILURE"], duration_ms=1,
        ocr_calls=0, concept_calls=0, page_count=1,
    )
    destination = output_module.publish_run(tmp_path, bundle, None)
    assert {item.name for item in destination.iterdir()} == {"producer-bundle.json"}
    assert output_module.read_producer_bundle(tmp_path, output["run_id"])["bundle"] == bundle
    bundle_path = destination / "producer-bundle.json"
    bundle_path.write_text(
        '{"tampered":true}', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="PRODUCER_BUNDLE_INVALID"):
        output_module.read_producer_bundle(tmp_path, output["run_id"])
    bundle_path.unlink()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    bundle_path.symlink_to(outside)
    with pytest.raises(ValueError, match="PRODUCER_BUNDLE_INVALID"):
        output_module.read_producer_bundle(tmp_path, output["run_id"])
    with pytest.raises(ValueError, match="PRODUCER_BUNDLE_INVALID"):
        output_module.read_producer_bundle(tmp_path, "../run-one")


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
    output["concepts"][0]["evidence_ids"] = ["evidence:sha256:" + "f" * 64]
    _reidentify_output(output)
    assert validate_output_document(output) is False


def test_every_included_page_requires_a_usable_concept():
    output = _two_page_output()
    output["concepts"].pop()
    _reidentify_output(output)
    assert validate_output_document(output) is False


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
    _reidentify_output(output)

    assert validate_output_document(output) is False


def test_bundle_is_closed_and_rejects_type_count_and_nonfinite_values():
    output = producer_output()
    bundle = bundle_for(output)
    bundle["unexpected_field"] = True
    assert output_module.validate_bundle_documents(
        bundle, output, output["run_id"]
    ) is False
    bundle.pop("unexpected_field")
    bundle["ocr_calls"] = True
    assert output_module.validate_bundle_documents(
        bundle, output, output["run_id"]
    ) is False
    bundle["ocr_calls"] = 33
    assert output_module.validate_bundle_documents(
        bundle, output, output["run_id"]
    ) is False


def test_formal_artifacts_reject_detailed_reason_compatibility():
    output = producer_output()
    output["reason_codes"] = ["SEMANTIC_REVIEW_REQUIRED"]
    _reidentify_output(output)
    assert validate_output_document(output) is False

    output = producer_output()
    bundle = bundle_for(output)
    bundle["reason_codes"] = ["SEMANTIC_REVIEW_REQUIRED"]
    identity = dict(bundle)
    identity.pop("bundle_id")
    bundle["bundle_id"] = (
        "text-first-producer-bundle:sha256:" + canonical_sha256(identity)
    )
    assert output_module.validate_bundle_documents(
        bundle, output, output["run_id"]
    ) is False
