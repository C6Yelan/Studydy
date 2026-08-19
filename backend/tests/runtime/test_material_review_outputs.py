from uuid import UUID

import runtime.storage.material_review_outputs as output_storage
from pdf_evidence.text_first_bundle import build_producer_bundle
from runtime.storage.material_review_outputs import _binding_is_valid
from test_study_material_output import producer_output


def _binding():
    return {
        "schema": "material-run-output-binding/v2",
        "producer_bundle_id": "text-first-producer-bundle:sha256:" + "1" * 64,
        "producer_run_id": "text-first-run:00000000-0000-4000-8000-000000000001",
        "concept_evidence_output_id": "concept-evidence-output:sha256:" + "2" * 64,
        "study_material_output_revision": "study-material-output:sha256:" + "3" * 64,
        "knowledge_map_revision": "knowledge-map:sha256:" + "4" * 64,
        "runtime_binding_sha256": "5" * 64,
        "page_count": 1,
        "processing": "succeeded",
        "quality": "needs_review",
        "decision": "review",
        "reason_codes": ["CONTENT_REVIEW_REQUIRED"],
        "ocr_calls": 1,
        "concept_calls": 1,
    }


def test_persisted_binding_is_closed_and_bool_is_not_an_integer():
    binding = _binding()
    assert _binding_is_valid(binding)
    binding["unexpected_field"] = True
    assert not _binding_is_valid(binding)
    binding.pop("unexpected_field")
    binding["page_count"] = True
    assert not _binding_is_valid(binding)


def test_db_cutover_uses_one_canonical_bundle_validation(monkeypatch):
    output = producer_output()
    bundle = build_producer_bundle(
        run_id=output["run_id"],
        produced_at=output["produced_at"],
        output=output,
        runtime_binding_sha256="a" * 64,
        reasons=output["reason_codes"],
        duration_ms=1,
        ocr_calls=1,
        concept_calls=1,
        page_count=1,
    )
    calls = 0
    canonical_validation = output_storage.validate_bundle_documents

    def counted_validation(*arguments):
        nonlocal calls
        calls += 1
        return canonical_validation(*arguments)

    monkeypatch.setattr(
        output_storage, "validate_bundle_documents", counted_validation
    )
    validated = output_storage._validated_producer(
        {"bundle": bundle, "output": output},
        UUID("00000000-0000-4000-8000-000000000001"),
    )

    assert validated == (bundle, output)
    assert calls == 1
