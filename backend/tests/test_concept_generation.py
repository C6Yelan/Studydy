import json
from pathlib import Path

import pytest

from pdf_evidence.concept_generation import (
    SemanticOutputError,
    build_semantic_request,
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
        page_ref="page:sha256:" + "1" * 64,
        input_binding={"evidence_allowlist": ["evidence-one"]},
        attempt=1,
    )


def test_exact_request_preserves_identity_text_and_locator():
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
    assert build_semantic_request(page) == _request()


def test_single_trailing_ascii_quote_is_only_sanitation():
    model_text = json.dumps(_output(), ensure_ascii=False, separators=(",", ":")) + '"'
    artifact = _validate(model_text)
    assert len(artifact["concepts"]) == 1
    assert artifact["concepts"][0]["decision"] == "review"
    assert "TRAILING_QUOTE_REMOVED" in artifact["reason_codes"]


@pytest.mark.parametrize(
    "model_text",
    [
        '```json\n{"concepts":[]}\n```',
        'prefix{"concepts":[]}',
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
                "evidence_ids": ["evidence-one"],
            },
            {
                "label": "Second concept",
                "definition": "Second definition",
                "key_points": ["Second point"],
                "evidence_ids": ["evidence-one"],
            },
        ]
    }
    artifact = _validate(json.dumps(output, separators=(",", ":")))
    assert len(artifact["concepts"]) == 2
    assert all(concept["decision"] == "review" for concept in artifact["concepts"])


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
        ("definition", "ﬃ" * 334),
        ("definition", "safe\x00unsafe"),
    ],
)
def test_candidate_text_still_rejects_empty_long_or_control_values(field, value):
    output = _output()
    output["concepts"][0][field] = value
    with pytest.raises(SemanticOutputError, match="NO_USABLE_CONCEPT"):
        _validate(json.dumps(output, ensure_ascii=False, separators=(",", ":")))


def test_semantic_request_fields_and_evidence_references_remain_exact():
    request = _request()
    request["section_id"] = "section　light"
    with pytest.raises(SemanticOutputError, match="INVALID_TEXT_FIELD"):
        validate_concepts(
            json.dumps(_output(), separators=(",", ":")),
            semantic_request=request,
            page_ref="page:sha256:" + "1" * 64,
            input_binding={"evidence_allowlist": ["evidence-one"]},
            attempt=1,
        )

    output = _output()
    output["concepts"][0]["evidence_ids"] = ["ｅvidence-one"]
    with pytest.raises(SemanticOutputError, match="NO_USABLE_CONCEPT"):
        _validate(json.dumps(output, ensure_ascii=False, separators=(",", ":")))
