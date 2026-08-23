from copy import deepcopy
from pathlib import Path
import unicodedata

import pytest

from learning_resources import map_resources
from learning_resources.map_resources import (
    build_map_resource_context,
    build_resource_library,
    load_bundled_resource_library,
    validate_map_resource_context,
    validate_resource_library,
)
from pdf_evidence.ocr_page_evidence import canonical_bytes, canonical_sha256
from pdf_evidence.concept_generation import claim_id, concept_id


def _source(source_sha256, title="Physics Notes"):
    return {
        "source_sha256": source_sha256,
        "page_count": 4,
        "title": title,
        "authors": ["Ada Student"],
        "source_url": "https://example.edu/physics.pdf",
        "citation": f"Ada Student. {title}. https://example.edu/physics.pdf",
        "license": "CC BY 4.0 International",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "use_boundary": "Attribution required.",
    }


def _entry(source_sha256, label="Quantum Field Theory", page_number=2, left=40.0):
    return {
        "source_sha256": source_sha256,
        "page_number": page_number,
        "label": label,
        "evidence": [{
            "quote": f"{label} connects fields with particle observations.",
            "region": {
                "coordinate_space": "unrotated_pdf_points",
                "bbox": [left, 80.0, left + 240.0, 104.0],
            },
        }],
    }


def _study_output(label, source_sha256="f" * 64):
    page_ref = "page:sha256:" + "1" * 64
    evidence_id = "evidence:sha256:" + "2" * 64
    definition = {
        "text": "A reviewed Study-side concept.",
        "evidence_ids": [evidence_id],
    }
    definition = {
        "claim_id": claim_id(page_ref, "definition", definition),
        **definition,
    }
    point = {
        "text": "The page provides direct evidence.",
        "evidence_ids": [evidence_id],
    }
    point = {
        "claim_id": claim_id(page_ref, "key_point", point, index=0),
        **point,
    }
    concept = {
        "concept_id": concept_id(page_ref, label, definition, [point]),
        "page_ref": page_ref,
        "label": label,
        "definition": definition,
        "key_points": [point],
        "processing": "succeeded",
        "quality": "needs_review",
        "decision": "review",
        "reason_codes": ["CONTENT_REVIEW_REQUIRED"],
    }
    document = {
        "schema": "study-material-output/v4",
        "run_id": "study-test-run",
        "produced_at": "2026-08-21T10:00:00+08:00",
        "material_ref": "material:sha256:" + source_sha256,
        "source_binding": {
            "source_sha256": source_sha256,
            "page_count": 1,
            "producer_output_id": "concept-evidence-output:sha256:" + "4" * 64,
            "runtime_binding_sha256": "5" * 64,
        },
        "pages": [
            {
                "page_ref": page_ref,
                "page_number": 1,
                "page_evidence_id": "page-evidence:sha256:" + "6" * 64,
                "native_evidence_ref": "native-evidence:sha256:" + "7" * 64,
                "processing": "succeeded",
                "quality": "needs_review",
                "decision": "review",
                "reason_codes": ["CONTENT_REVIEW_REQUIRED"],
            }
        ],
        "excluded_pages": [],
        "concepts": [concept],
        "evidence_index": [
            {
                "evidence_id": evidence_id,
                "page_ref": page_ref,
                "page_number": 1,
                "kind": "paragraph",
                "region": {
                    "coordinate_space": "unrotated_pdf_points",
                    "bbox": [20.0, 30.0, 300.0, 60.0],
                },
            }
        ],
        "images": [],
        "processing": "succeeded",
        "quality": "needs_review",
        "decision": "review",
        "reason_codes": ["CONTENT_REVIEW_REQUIRED"],
    }
    document["output_id"] = "study-material-output:sha256:" + canonical_sha256(
        document
    )
    return document


def _normalized_label(label):
    normalized = unicodedata.normalize("NFKC", label).casefold()
    return " ".join(
        "".join(
            " " if unicodedata.category(character).startswith("P") else character
            for character in normalized
        ).split()
    )


def test_non_subject_specific_fixture_builds_deterministic_closed_library():
    first_sha = "a" * 64
    second_sha = "b" * 64
    sources = [_source(first_sha), _source(second_sha, "Astronomy Notes")]
    entries = [
        _entry(first_sha),
        _entry(second_sha, left=60.0),
        _entry(second_sha, "Orbital Resonance", page_number=3, left=70.0),
    ]

    library = build_resource_library(sources, entries)
    repeated = build_resource_library(deepcopy(sources), deepcopy(entries))

    assert canonical_bytes(library) == canonical_bytes(repeated)
    assert validate_resource_library(library) is None
    assert len(library["sources"]) == 2
    assert len(library["evidence"]) == 3
    assert len(library["concepts"]) == 3
    assert library["library_revision"].startswith("resource-library:sha256:")
    assert library["sources"] == sorted(
        library["sources"], key=lambda source: source["resource_id"]
    )
    assert library["concepts"] == sorted(
        library["concepts"],
        key=lambda concept: (concept["page_ref"], concept["concept_id"]),
    )


def test_builder_preserves_exact_evidence_lineage_and_rejects_bad_input():
    source_sha256 = "a" * 64
    entry = _entry(source_sha256)
    library = build_resource_library([_source(source_sha256)], [entry])
    evidence = library["evidence"][0]
    concept = library["concepts"][0]
    source = library["sources"][0]

    assert evidence["resource_id"] == source["resource_id"]
    assert evidence["page_number"] == entry["page_number"]
    assert evidence["page_ref"] == concept["page_ref"]
    assert concept["evidence_ids"] == [evidence["evidence_id"]]
    assert evidence["quote"] == entry["evidence"][0]["quote"]
    assert (evidence["processing"], evidence["quality"], evidence["decision"]) == (
        "succeeded",
        "accepted",
        "retain",
    )

    invalid_entry = deepcopy(entry)
    invalid_entry["evidence"][0]["region"]["bbox"] = [10.0, 20.0, 5.0, 30.0]
    with pytest.raises(ValueError, match="RESOURCE_LIBRARY_INPUT_INVALID"):
        build_resource_library([_source(source_sha256)], [invalid_entry])


def test_builder_keeps_all_reviewed_evidence_for_one_concept():
    source_sha256 = "a" * 64
    entry = _entry(source_sha256)
    entry["evidence"].append({
        "quote": "A second reviewed passage supports the same concept.",
        "region": {
            "coordinate_space": "unrotated_pdf_points",
            "bbox": [40.0, 120.0, 280.0, 144.0],
        },
    })

    library = build_resource_library([_source(source_sha256)], [entry])

    assert len(library["evidence"]) == 2
    assert len(library["concepts"][0]["evidence_ids"]) == 2
    assert set(library["concepts"][0]["evidence_ids"]) == {
        evidence["evidence_id"] for evidence in library["evidence"]
    }

    broken = deepcopy(library)
    broken["concepts"][0]["evidence_ids"] = ["resource-evidence:sha256:" + "0" * 64]
    assert validate_resource_library(broken) == "RESOURCE_LIBRARY_INVALID"

    malformed = deepcopy(library)
    malformed["concepts"][0]["evidence_ids"] = 3
    assert validate_resource_library(malformed) == "RESOURCE_LIBRARY_INVALID"

    malformed_entry = deepcopy(entry)
    malformed_entry["source_sha256"] = []
    with pytest.raises(ValueError, match="RESOURCE_LIBRARY_INPUT_INVALID"):
        build_resource_library([_source(source_sha256)], [malformed_entry])


def test_bundled_library_is_self_contained_and_matches_initial_data_gate():
    library = load_bundled_resource_library()
    library_path = (
        Path(map_resources.__file__).with_name("data") / "resource_library_v1.json"
    )

    assert validate_resource_library(library) is None
    assert canonical_bytes(library) == library_path.read_bytes()
    assert len(library["sources"]) == 3
    assert len(library["evidence"]) == 289
    assert len(library["concepts"]) == 289
    assert len({_normalized_label(concept["label"]) for concept in library["concepts"]}) == 266
    assert all(
        concept["evidence_ids"]
        and all(
            evidence_id in {item["evidence_id"] for item in library["evidence"]}
            for evidence_id in concept["evidence_ids"]
        )
        for concept in library["concepts"]
    )


def test_production_module_contains_no_initial_dataset_or_private_path_branch():
    source = Path(map_resources.__file__).read_text(encoding="utf-8")
    forbidden = {
        "data_structures",
        "Open Data Structures",
        "Think Data Structures",
        "An Open Guide to Data Structures",
        "3fbbe5febd0e84f79c509cca28a0be93deef0ab045999b5efc78af9e913088f4",
        "289",
        "266",
        "Binary Search Tree",
        "Doubly-Linked List",
        "Radix Sort",
        "/mnt/",
        "docs_local",
    }
    assert not any(value in source for value in forbidden)
    assert "pymupdf" not in source.casefold()
    assert "httpx" not in source.casefold()


def test_loader_rejects_corrupt_and_duplicate_key_artifacts(tmp_path, monkeypatch):
    fake_module = tmp_path / "map_resources.py"
    data_directory = tmp_path / "data"
    data_directory.mkdir()
    bundled_path = data_directory / "resource_library_v1.json"
    monkeypatch.setattr(map_resources, "__file__", str(fake_module))

    bundled_path.write_text("not-json", encoding="utf-8")
    with pytest.raises(ValueError, match="RESOURCE_LIBRARY_LOAD_FAILED"):
        load_bundled_resource_library()

    bundled_path.write_text('{"schema":"first","schema":"second"}', encoding="utf-8")
    with pytest.raises(ValueError, match="RESOURCE_LIBRARY_LOAD_FAILED"):
        load_bundled_resource_library()


def test_nonempty_context_keeps_all_source_bound_matches_under_review():
    first_sha = "a" * 64
    second_sha = "b" * 64
    library = build_resource_library(
        [_source(first_sha), _source(second_sha)],
        [_entry(first_sha), _entry(second_sha, left=60.0)],
    )
    study_output = _study_output("quantum—field theory")

    context = build_map_resource_context(study_output, library)
    repeated = build_map_resource_context(deepcopy(study_output), deepcopy(library))

    assert context == repeated
    assert validate_map_resource_context(context, study_output, library) is None
    assert len(context["matches"]) == 2
    assert (context["processing"], context["quality"], context["decision"]) == (
        "partial",
        "needs_review",
        "review",
    )
    assert context["reason_codes"] == [
        "RESOURCE_MATCH_REQUIRES_P05_QUALIFICATION"
    ]
    assert all(
        match["reason_codes"]
        == ["RESOURCE_MATCH_REQUIRES_P05_QUALIFICATION"]
        and match["match_reason"] == "EXACT_NORMALIZED_LABEL"
        for match in context["matches"]
    )
    assert context["matches"] == sorted(
        context["matches"],
        key=lambda match: (
            match["study_concept_id"],
            match["resource_concept_id"],
            match["match_id"],
        ),
    )


@pytest.mark.parametrize(
    ("study_label", "same_source", "reason_code"),
    [
        ("Unrelated Concept", False, "RESOURCE_NO_EXACT_LABEL_MATCH"),
        ("Quantum Field Theory", True, "RESOURCE_NO_DISTINCT_SOURCE_MATCH"),
    ],
)
def test_approved_empty_contexts_are_truthful_successes(
    study_label, same_source, reason_code
):
    source_sha256 = "a" * 64
    library = build_resource_library(
        [_source(source_sha256)], [_entry(source_sha256)]
    )
    study_output = _study_output(
        study_label, source_sha256=source_sha256 if same_source else "f" * 64
    )

    context = build_map_resource_context(study_output, library)

    assert context["matches"] == []
    assert (context["processing"], context["quality"], context["decision"]) == (
        "succeeded",
        "accepted",
        "retain",
    )
    assert context["reason_codes"] == [reason_code]
    assert validate_map_resource_context(context, study_output, library) is None


def test_context_rejects_invalid_inputs_missing_matches_and_false_success():
    source_sha256 = "a" * 64
    library = build_resource_library(
        [_source(source_sha256)], [_entry(source_sha256)]
    )
    study_output = _study_output("Quantum Field Theory")
    context = build_map_resource_context(study_output, library)

    invalid_study = deepcopy(study_output)
    invalid_study["output_id"] = "study-material-output:sha256:" + "0" * 64
    with pytest.raises(ValueError, match="MAP_RESOURCE_CONTEXT_INPUT_INVALID"):
        build_map_resource_context(invalid_study, library)

    malformed_study = deepcopy(study_output)
    malformed_study["concepts"][0]["concept_id"] = []
    with pytest.raises(ValueError, match="MAP_RESOURCE_CONTEXT_INPUT_INVALID"):
        build_map_resource_context(malformed_study, library)

    missing_match = deepcopy(context)
    missing_match["matches"] = []
    missing_match["processing"] = "succeeded"
    missing_match["quality"] = "accepted"
    missing_match["decision"] = "retain"
    missing_match["reason_codes"] = ["RESOURCE_NO_EXACT_LABEL_MATCH"]
    assert (
        validate_map_resource_context(missing_match, study_output, library)
        == "MAP_RESOURCE_CONTEXT_INVALID"
    )

    false_success = deepcopy(context)
    false_success["processing"] = "succeeded"
    false_success["quality"] = "accepted"
    false_success["decision"] = "retain"
    assert (
        validate_map_resource_context(false_success, study_output, library)
        == "MAP_RESOURCE_CONTEXT_INVALID"
    )

    malformed = deepcopy(context)
    malformed["matches"][0]["match_id"] = []
    assert (
        validate_map_resource_context(malformed, study_output, library)
        == "MAP_RESOURCE_CONTEXT_INVALID"
    )


def test_context_does_not_use_substring_matching():
    source_sha256 = "a" * 64
    library = build_resource_library(
        [_source(source_sha256)], [_entry(source_sha256)]
    )
    study_output = _study_output("Quantum Field")

    context = build_map_resource_context(study_output, library)

    assert context["matches"] == []
    assert context["reason_codes"] == ["RESOURCE_NO_EXACT_LABEL_MATCH"]
