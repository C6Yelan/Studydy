from runtime.storage.material_review_outputs import _binding_is_valid


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
        "reason_codes": ["SEMANTIC_REVIEW_REQUIRED"],
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
