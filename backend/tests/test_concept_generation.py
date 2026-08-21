import json
from pathlib import Path

import pytest

from pdf_evidence.concept_generation import (
    SemanticOutputError,
    build_semantic_request,
    combine_semantic_batches,
    split_semantic_request,
    validate_concepts,
)


FIXTURES = Path(__file__).parents[2] / "local_ai" / "tests" / "fixtures"


def _request():
    return json.loads((FIXTURES / "semantic_request.json").read_text(encoding="utf-8"))


def _output():
    return json.loads((FIXTURES / "semantic_model_output.json").read_text(encoding="utf-8"))


def _validate(model_text):
    return validate_concepts(
        model_text,
        semantic_request=_request(),
        evidence_aliases={"e1": "evidence-one"},
        page_ref="page:sha256:" + "1" * 64,
        input_binding={"evidence_allowlist": ["evidence-one"]},
        attempt=1,
    )


def test_model_request_contains_only_short_alias_and_text():
    page = {
        "material_id": "material-alpha",
        "material_revision": "revision-one",
        "section_id": "section-light",
        "evidence_blocks": [
            {
                "evidence_id": "evidence-one",
                "text": "Photosynthesis converts light energy into chemical energy in plants.",
                "locator": {"page": 1, "block_id": "block-one", "region": [10, 20, 90, 60]},
            }
        ],
    }
    request, aliases = build_semantic_request(page)
    assert request == _request()
    assert aliases == {"e1": "evidence-one"}
    assert "material-alpha" not in json.dumps(request)
    assert "block-one" not in json.dumps(request)


@pytest.mark.parametrize(
    "model_text",
    [
        '```json\n{"concepts":[]}\n```',
        'prefix{"concepts":[]}',
        '{"concepts":[]}"',
        '{"concepts":[]}}',
        '{"concepts":[]}""',
        '{"concepts":',
        '{"concepts":[],"concepts":[]}',
        '{"concepts":NaN}',
    ],
)
def test_fence_prefix_suffix_truncation_duplicate_and_nan_fail(model_text):
    with pytest.raises(SemanticOutputError):
        _validate(model_text)


def test_cross_concept_evidence_reuse_and_no_lexical_decision():
    output = {
        "concepts": [
            {
                "label": "Unrelated label",
                "definition": "Structurally valid unrelated definition",
                "key_points": ["No lexical matching decision"],
                "evidence_ids": ["e1"],
            },
            {
                "label": "Second concept",
                "definition": "Second definition",
                "key_points": ["Second point"],
                "evidence_ids": ["e1"],
            },
        ]
    }
    artifact = _validate(json.dumps(output, separators=(",", ":")))
    assert len(artifact["concepts"]) == 2
    assert all(concept["decision"] == "review" for concept in artifact["concepts"])
    assert all(concept["processing"] == "succeeded" for concept in artifact["concepts"])
    assert artifact["processing"] == "succeeded"


def test_per_concept_invalid_candidate_is_rejected_without_losing_valid_candidate():
    output = _output()
    output["concepts"].append(
        {
            "label": "Invalid",
            "definition": "Invalid evidence",
            "key_points": ["Invalid"],
            "evidence_ids": ["unknown"],
        }
    )
    artifact = _validate(json.dumps(output, separators=(",", ":")))
    assert len(artifact["concepts"]) == 1
    assert artifact["rejected_candidates"][0]["reason_codes"] == ["UNKNOWN_EVIDENCE_ID"]
    assert artifact["processing"] == "partial"


def test_model_status_or_locator_fields_are_not_trusted():
    output = _output()
    output["concepts"][0]["status"] = "accepted"
    with pytest.raises(SemanticOutputError, match="NO_USABLE_CONCEPT"):
        _validate(json.dumps(output, separators=(",", ":")))


def test_candidate_text_is_normalized_before_validation():
    output = _output()
    output["concepts"][0].update(
        {
            "label": "  Ｌight   energy  ",
            "definition": "Plants\n convert\t light into chemical energy.",
            "key_points": ["  Full-width： light   becomes energy  "],
        }
    )
    artifact = _validate(json.dumps(output, ensure_ascii=False, separators=(",", ":")))
    concept = artifact["concepts"][0]
    assert concept["label"] == "Light energy"
    assert concept["definition"] == "Plants convert light into chemical energy."
    assert concept["key_points"] == ["Full-width: light becomes energy"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("label", "  \t\n  "),
        ("definition", "safe\x00unsafe"),
    ],
)
def test_candidate_text_still_rejects_empty_long_or_control_values(field, value):
    output = _output()
    output["concepts"][0][field] = value
    with pytest.raises(SemanticOutputError, match="NO_USABLE_CONCEPT"):
        _validate(json.dumps(output, ensure_ascii=False, separators=(",", ":")))


def test_semantic_request_aliases_and_evidence_references_remain_exact():
    request = _request()
    request["material_id"] = "material-alpha"
    with pytest.raises(SemanticOutputError, match="INPUT_SCHEMA_INVALID"):
        validate_concepts(
            json.dumps(_output(), separators=(",", ":")),
            semantic_request=request,
            evidence_aliases={"e1": "evidence-one"},
            page_ref="page:sha256:" + "1" * 64,
            input_binding={"evidence_allowlist": ["evidence-one"]},
            attempt=1,
        )

    request = _request()
    request["evidence"].append(request["evidence"][0])
    with pytest.raises(SemanticOutputError, match="DUPLICATE_EVIDENCE_ID"):
        validate_concepts(
            json.dumps(_output(), separators=(",", ":")),
            semantic_request=request,
            evidence_aliases={"e1": "evidence-one"},
            page_ref="page:sha256:" + "1" * 64,
            input_binding={"evidence_allowlist": ["evidence-one"]},
            attempt=1,
        )

    output = _output()
    output["concepts"][0]["evidence_ids"] = ["ｅ1"]
    with pytest.raises(SemanticOutputError, match="NO_USABLE_CONCEPT"):
        _validate(json.dumps(output, ensure_ascii=False, separators=(",", ":")))

    output = _output()
    output["concepts"].append(dict(output["concepts"][0]))
    output["concepts"][0]["evidence_ids"] = ["e1", "e1"]
    artifact = _validate(json.dumps(output, separators=(",", ":")))
    assert artifact["rejected_candidates"][0]["reason_codes"] == [
        "DUPLICATE_EVIDENCE_REFERENCE"
    ]
    assert len(artifact["concepts"]) == 1


def test_large_page_request_splits_without_losing_formal_evidence_ids():
    request = {
        "schema": "concept-generation-input/v2",
        "evidence": [
            {"id": "e1", "text": "first concept"},
            {"id": "e2", "text": "second concept"},
        ],
    }
    first, second = split_semantic_request(request)
    assert first["evidence"] == [{"id": "e1", "text": "first concept"}]
    assert second["evidence"] == [{"id": "e2", "text": "second concept"}]

    page_ref = "page:sha256:" + "1" * 64
    binding = {"evidence_allowlist": ["formal-one", "formal-two"]}
    artifacts = []
    for batch, aliases, label in (
        (first, {"e1": "formal-one"}, "First"),
        (second, {"e2": "formal-two"}, "Second"),
    ):
        artifacts.append(
            validate_concepts(
                json.dumps({
                    "concepts": [{
                        "label": label,
                        "definition": f"{label} definition",
                        "key_points": [f"{label} point"],
                        "evidence_ids": list(aliases),
                    }]
                }),
                semantic_request=batch,
                evidence_aliases=aliases,
                page_ref=page_ref,
                input_binding=binding,
                attempt=1,
            )
        )
    combined = combine_semantic_batches(
        artifacts,
        page_ref=page_ref,
        input_binding=binding,
    )
    assert len(combined["concepts"]) == 2
    assert {
        evidence_id
        for concept in combined["concepts"]
        for evidence_id in concept["evidence_ids"]
    } == {"formal-one", "formal-two"}


def test_single_large_evidence_split_removes_only_boundary_whitespace():
    first, second = split_semantic_request({
        "schema": "concept-generation-input/v2",
        "evidence": [{"id": "e1", "text": "first half   second half"}],
    })
    assert first["evidence"] == [{"id": "e1", "text": "first half"}]
    assert second["evidence"] == [{"id": "e1", "text": "second half"}]
