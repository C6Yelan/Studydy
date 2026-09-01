from copy import deepcopy
import json

import pytest

from pdf_evidence.concept_generation import (
    SemanticOutputError,
    build_semantic_request,
    combine_semantic_batches,
    validate_concepts,
)
from pdf_evidence.document_context import build_document_contexts


PAGE_REF = "page:sha256:" + "1" * 64


def _page(blocks):
    return {
        "schema": "page-evidence/v3",
        "material_id": "material-alpha",
        "material_revision": "revision-one",
        "section_id": "section-public",
        "page_ref": PAGE_REF,
        "page_number": 1,
        "page_evidence_id": "page-evidence:sha256:" + "2" * 64,
        "evidence_blocks": [
            {
                "evidence_id": f"evidence-{index}",
                "block_id": f"block-{index}",
                "kind": kind,
                "text": text,
                "reading_order": index,
            }
            for index, (kind, text) in enumerate(blocks)
        ],
    }


def _request(blocks):
    page = _page(blocks)
    request, aliases = build_semantic_request(
        page, build_document_contexts([page])[0]
    )
    return request, aliases


def _validate(concepts, blocks=None):
    request, aliases = _request(
        blocks or [("paragraph", "Photosynthesis converts light energy into chemical energy.")]
    )
    return validate_concepts(
        json.dumps({"concepts": concepts}),
        semantic_request=request,
        evidence_aliases=aliases,
        page_ref=PAGE_REF,
        input_binding={"evidence_allowlist": list(aliases.values())},
        attempt=1,
    )


def test_request_uses_short_aliases_without_mutating_page():
    page = _page([("paragraph", "Price\x01equals P\\0 when x>=2.")])
    unchanged = deepcopy(page)
    request, aliases = build_semantic_request(
        page, build_document_contexts([page])[0]
    )
    assert request["evidence"] == [
        {"id": "e1", "text": "Price equals P\\0 when x>=2."}
    ]
    assert aliases == {"e1": "evidence-0"}
    assert page == unchanged


def test_grounded_single_claim_concept_survives():
    artifact = _validate([
        {
            "label": "Photosynthesis",
            "claims": [{
                "text": "Photosynthesis converts light energy into chemical energy.",
                "evidence_ids": ["e1"],
            }],
        }
    ])
    concept = artifact["concepts"][0]
    assert concept["processing"] == "succeeded"
    assert [claim["text"] for claim in concept["claims"]] == [
        "Photosynthesis converts light energy into chemical energy."
    ]


@pytest.mark.parametrize(
    "text",
    [
        "const answer = 42;",
        "E = mc^2",
        "Figure 1: Request flow",
        "function calculate(",
        "Result:",
    ],
)
def test_grounded_code_formula_caption_and_partial_declaration_survive(text):
    artifact = _validate(
        [{"label": "Technical example", "claims": [{"text": text, "evidence_ids": ["e1"]}]}],
        [("code", f"{text} is shown in the material.")],
    )
    assert artifact["concepts"][0]["claims"][0]["text"] == text


def test_objective_junk_is_removed_without_erasing_valid_sibling_claim():
    artifact = _validate(
        [{
            "label": "Flow",
            "claims": [
                {"text": "and", "evidence_ids": ["e1"]},
                {"text": "The flow reaches the target.", "evidence_ids": ["e1"]},
            ],
        }],
        [("paragraph", "and The flow reaches the target.")],
    )
    concept = artifact["concepts"][0]
    assert [claim["text"] for claim in concept["claims"]] == [
        "The flow reaches the target."
    ]
    assert concept["processing"] == "partial"
    assert "CLAIM_ISOLATED_CONNECTOR" in concept["reason_codes"]


def test_unkeyed_assessment_cannot_publish_a_fact():
    blocks = [
        ("paragraph", "Which statement is correct?"),
        ("paragraph", "(A) Rivers always flow uphill."),
        ("paragraph", "(B) Rivers never carry sediment."),
        ("paragraph", "(C) Rivers always have equal depth."),
    ]
    artifact = _validate(
        [{
            "label": "River answer",
            "claims": [{
                "text": "Rivers always flow uphill.",
                "evidence_ids": ["e2"],
            }],
        }],
        blocks,
    )
    assert artifact["concepts"] == []
    assert artifact["rejected_candidates"][0]["reason_codes"] == [
        "CLAIM_UNKEYED_ASSESSMENT_OPTION"
    ]


def test_unknown_evidence_rejects_only_that_candidate():
    artifact = _validate([
        {
            "label": "Valid",
            "claims": [{
                "text": "Photosynthesis converts light energy into chemical energy.",
                "evidence_ids": ["e1"],
            }],
        },
        {
            "label": "Invalid",
            "claims": [{"text": "Unknown", "evidence_ids": ["e9"]}],
        },
    ])
    assert [concept["label"] for concept in artifact["concepts"]] == ["Valid"]
    assert artifact["concepts"][0]["processing"] == "succeeded"
    assert artifact["processing"] == "partial"
    assert artifact["rejected_candidates"][0]["reason_codes"] == [
        "UNKNOWN_EVIDENCE_ID"
    ]


def test_exact_technical_token_fabrication_remains_blocked():
    artifact = _validate(
        [{
            "label": "Escape marker",
            "claims": [{
                "text": "Readers must preserve the escape 0 exactly.",
                "evidence_ids": ["e1"],
            }],
        }],
        [("code", "Readers must preserve the escape \\0 exactly.")],
    )
    assert artifact["concepts"] == []
    assert artifact["rejected_candidates"][0]["reason_codes"] == [
        "CLAIM_EVIDENCE_UNSUPPORTED"
    ]


def test_batch_rejection_does_not_downgrade_valid_concept():
    valid = _validate([
        {
            "label": "Valid",
            "claims": [{
                "text": "Photosynthesis converts light energy into chemical energy.",
                "evidence_ids": ["e1"],
            }],
        }
    ])
    rejected = _validate([
        {"label": "Invalid", "claims": [{"text": "Unknown", "evidence_ids": ["e9"]}]}
    ])
    combined = combine_semantic_batches(
        [valid, rejected],
        page_ref=PAGE_REF,
        input_binding={"batch_bindings": []},
    )
    assert len(combined["concepts"]) == 1
    assert combined["concepts"][0]["processing"] == "succeeded"
    assert combined["processing"] == "partial"


def test_malformed_model_document_fails_without_raw_content():
    request, aliases = _request([("paragraph", "Grounded text")])
    with pytest.raises(SemanticOutputError, match="MODEL_OUTPUT_INVALID_JSON"):
        validate_concepts(
            "not-json",
            semantic_request=request,
            evidence_aliases=aliases,
            page_ref=PAGE_REF,
            input_binding={"evidence_allowlist": list(aliases.values())},
            attempt=1,
        )
