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
from pdf_evidence.document_context import build_document_contexts


FIXTURES = Path(__file__).parents[2] / "local_ai" / "tests" / "fixtures"


def _request():
    return json.loads((FIXTURES / "semantic_request.json").read_text(encoding="utf-8"))


def _output():
    return json.loads((FIXTURES / "semantic_model_output.json").read_text(encoding="utf-8"))


def _request_with_evidence(evidence):
    request = _request()
    request["evidence"] = evidence
    request["document_context"]["current_blocks"] = [
        {
            "evidence_id": item["id"],
            "heading_ancestry_ids": [],
            "previous_evidence_id": (
                evidence[index - 1]["id"] if index > 0 else None
            ),
            "next_evidence_id": (
                evidence[index + 1]["id"]
                if index + 1 < len(evidence)
                else None
            ),
            "continuation_ids": [],
        }
        for index, item in enumerate(evidence)
    ]
    return request


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
        "schema": "page-evidence/v3",
        "material_id": "material-alpha",
        "material_revision": "revision-one",
        "section_id": "section-light",
        "page_ref": "page:sha256:" + "1" * 64,
        "page_number": 1,
        "page_evidence_id": "page-evidence:sha256:" + "2" * 64,
        "evidence_blocks": [
            {
                "evidence_id": "evidence-one",
                "block_id": "block-one",
                "kind": "paragraph",
                "text": "Photosynthesis converts light energy into chemical energy in plants.",
                "reading_order": 0,
                "locator": {"page": 1, "block_id": "block-one", "region": [10, 20, 90, 60]},
            }
        ],
    }
    request, aliases = build_semantic_request(
        page, build_document_contexts([page])[0]
    )
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


def test_zero_concepts_is_a_valid_page_result():
    artifact = _validate('{"concepts":[]}')
    assert artifact["concepts"] == []
    assert artifact["processing"] == "succeeded"
    assert artifact["decision"] == "review"


def test_cross_concept_evidence_reuse_requires_each_claim_grounding():
    output = {
        "concepts": [
            {
                "label": "Light conversion",
                "definition": {"text": "Photosynthesis converts light energy into chemical energy.", "evidence_ids": ["e1"]},
                "key_points": [{"text": "Plants convert light energy.", "evidence_ids": ["e1"]}],
            },
            {
                "label": "Chemical energy",
                "definition": {"text": "Chemical energy is produced from light energy in plants.", "evidence_ids": ["e1"]},
                "key_points": [{"text": "Plants contain chemical energy.", "evidence_ids": ["e1"]}],
            },
            {
                "label": "Unsupported",
                "definition": {"text": "Database indexes accelerate unrelated queries.", "evidence_ids": ["e1"]},
                "key_points": [{"text": "Indexes store row locations.", "evidence_ids": ["e1"]}],
            },
        ]
    }
    artifact = _validate(json.dumps(output, separators=(",", ":")))
    assert len(artifact["concepts"]) == 2
    assert artifact["rejected_candidates"][0]["reason_codes"] == [
        "CLAIM_EVIDENCE_UNSUPPORTED"
    ]
    assert all(concept["decision"] == "review" for concept in artifact["concepts"])
    assert all(concept["processing"] == "succeeded" for concept in artifact["concepts"])
    assert artifact["processing"] == "partial"


def test_per_concept_invalid_candidate_is_rejected_without_losing_valid_candidate():
    output = _output()
    output["concepts"].append(
        {
            "label": "Invalid",
            "definition": {"text": "Invalid evidence", "evidence_ids": ["unknown"]},
            "key_points": [{"text": "Invalid", "evidence_ids": ["e1"]}],
        }
    )
    artifact = _validate(json.dumps(output, separators=(",", ":")))
    assert len(artifact["concepts"]) == 1
    assert artifact["rejected_candidates"][0]["reason_codes"] == ["UNKNOWN_EVIDENCE_ID"]
    assert artifact["processing"] == "partial"


def test_model_status_or_locator_fields_are_not_trusted():
    output = _output()
    output["concepts"][0]["status"] = "accepted"
    artifact = _validate(json.dumps(output, separators=(",", ":")))
    assert artifact["concepts"] == []
    assert artifact["rejected_candidates"][0]["reason_codes"] == ["CANDIDATE_SCHEMA_INVALID"]


def test_candidate_text_is_normalized_before_validation():
    output = _output()
    output["concepts"][0].update(
        {
            "label": "  Ｌight   energy  ",
            "definition": {"text": "Plants\n convert\t light into chemical energy.", "evidence_ids": ["e1"]},
            "key_points": [{"text": "  Light   becomes chemical energy.  ", "evidence_ids": ["e1"]}],
        }
    )
    artifact = _validate(json.dumps(output, ensure_ascii=False, separators=(",", ":")))
    concept = artifact["concepts"][0]
    assert concept["label"] == "Light energy"
    assert concept["definition"]["text"] == "Plants convert light into chemical energy."
    assert concept["key_points"][0]["text"] == "Light becomes chemical energy."


def test_title_only_evidence_cannot_support_an_expanded_definition():
    request = _request_with_evidence(
        [{"id": "e1", "text": "Data Dictionary (8 of 8)"}]
    )
    artifact = validate_concepts(
        json.dumps({
            "concepts": [{
                "label": "Data Dictionary",
                "definition": {
                    "text": "A data dictionary stores metadata about definitions and relationships.",
                    "evidence_ids": ["e1"],
                },
                "key_points": [{
                    "text": "A data dictionary provides reports.",
                    "evidence_ids": ["e1"],
                }],
            }],
        }),
        semantic_request=request,
        evidence_aliases={"e1": "evidence-one"},
        page_ref="page:sha256:" + "1" * 64,
        input_binding={"evidence_allowlist": ["evidence-one"]},
        attempt=1,
    )

    assert artifact["concepts"] == []
    assert artifact["rejected_candidates"][0]["reason_codes"] == [
        "CLAIM_EVIDENCE_UNSUPPORTED"
    ]
    assert artifact["processing"] == "partial"


def test_invalid_definition_is_replaced_by_grounded_explanation():
    request = _request_with_evidence(
        [
            {"id": "e1", "text": "Process Tools (1 of 8)"},
            {
                "id": "e2",
                "text": (
                    "A process description documents business logic. "
                    "Decision tables describe combinations of conditions."
                ),
            },
        ]
    )
    artifact = validate_concepts(
        json.dumps({
            "concepts": [{
                "label": "Process Tools",
                "definition": {
                    "text": "Process Tools are a complete modeling framework.",
                    "evidence_ids": ["e1"],
                },
                "key_points": [
                    {
                        "text": "A process description documents business logic.",
                        "evidence_ids": ["e2"],
                    },
                    {
                        "text": "Decision tables describe combinations of conditions.",
                        "evidence_ids": ["e2"],
                    },
                ],
            }],
        }),
        semantic_request=request,
        evidence_aliases={"e1": "evidence-one", "e2": "evidence-two"},
        page_ref="page:sha256:" + "1" * 64,
        input_binding={"evidence_allowlist": ["evidence-one", "evidence-two"]},
        attempt=1,
    )

    concept = artifact["concepts"][0]
    assert concept["definition"]["text"] == (
        "A process description documents business logic."
    )
    assert [point["text"] for point in concept["key_points"]] == [
        "Decision tables describe combinations of conditions."
    ]
    assert concept["processing"] == "partial"


def test_visual_index_and_scalar_fragments_are_removed_but_short_claim_survives():
    evidence_text = (
        "A circular queue stores elements in a fixed-size ring. "
        "front = 5 rear = 4 [0] [1] (a) full circular queue. "
        "Queue is full. The rear pointer advances after insertion. "
        "Figure 5-20 Sequence Structure. "
        "Figure 5-24 illustrates a complete decision table. "
        "Circular queue (1 of 8)."
    )
    request = _request_with_evidence(
        [{"id": "e1", "text": evidence_text}]
    )
    artifact = validate_concepts(
        json.dumps({
            "concepts": [{
                "label": "Circular queue",
                "definition": {
                    "text": "A circular queue stores elements in a fixed-size ring.",
                    "evidence_ids": ["e1"],
                },
                "key_points": [
                    {"text": "front = 5", "evidence_ids": ["e1"]},
                    {"text": "[0] [1]", "evidence_ids": ["e1"]},
                    {"text": "(a) full circular queue", "evidence_ids": ["e1"]},
                    {
                        "text": "Figure 5-20 Sequence Structure",
                        "evidence_ids": ["e1"],
                    },
                    {
                        "text": "Figure 5-24 illustrates a complete decision table.",
                        "evidence_ids": ["e1"],
                    },
                    {
                        "text": "Circular queue (1 of 8)",
                        "evidence_ids": ["e1"],
                    },
                    {"text": "Queue is full.", "evidence_ids": ["e1"]},
                    {
                        "text": "The rear pointer advances after insertion.",
                        "evidence_ids": ["e1"],
                    },
                ],
            }],
        }),
        semantic_request=request,
        evidence_aliases={"e1": "evidence-one"},
        page_ref="page:sha256:" + "1" * 64,
        input_binding={"evidence_allowlist": ["evidence-one"]},
        attempt=1,
    )

    assert [point["text"] for point in artifact["concepts"][0]["key_points"]] == [
        "Figure 5-24 illustrates a complete decision table.",
        "Queue is full.",
        "The rear pointer advances after insertion.",
    ]
    assert artifact["concepts"][0]["processing"] == "partial"
    assert artifact["processing"] == "partial"


@pytest.mark.parametrize(
    ("fragment", "reason_code"),
    [
        ("and", "CLAIM_ISOLATED_CONNECTOR"),
        (") ;", "CLAIM_SYNTAX_TAIL"),
        ("because the value changes", "CLAIM_HALF_CLAUSE"),
        ("value =", "CLAIM_HALF_CLAUSE"),
        ("function calculate(", "CLAIM_INCOMPLETE_DECLARATION"),
    ],
)
def test_incomplete_claims_are_demoted_with_deterministic_reasons(
    fragment, reason_code
):
    evidence_text = (
        "A complete rule explains the value. A second complete point is grounded. "
        + fragment
    )
    request = _request_with_evidence([{"id": "e1", "text": evidence_text}])
    artifact = validate_concepts(
        json.dumps({
            "concepts": [{
                "label": "Complete rule",
                "definition": {
                    "text": "A complete rule explains the value.",
                    "evidence_ids": ["e1"],
                },
                "key_points": [
                    {"text": fragment, "evidence_ids": ["e1"]},
                    {
                        "text": "A second complete point is grounded.",
                        "evidence_ids": ["e1"],
                    },
                ],
            }]
        }),
        semantic_request=request,
        evidence_aliases={"e1": "evidence-one"},
        page_ref="page:sha256:" + "1" * 64,
        input_binding={"evidence_allowlist": ["evidence-one"]},
        attempt=1,
    )

    assert [point["text"] for point in artifact["concepts"][0]["key_points"]] == [
        "A second complete point is grounded."
    ]
    assert reason_code in artifact["concepts"][0]["reason_codes"]
    assert artifact["concepts"][0]["processing"] == "partial"


def test_backslash_zero_is_preserved_and_plain_zero_cannot_replace_it():
    evidence_text = (
        "The escape \\0 marks the end of this public sequence. "
        "Readers must preserve the escape \\0 exactly."
    )
    request = _request_with_evidence([{"id": "e1", "text": evidence_text}])
    valid_output = {
        "concepts": [{
            "label": "Escape marker",
            "definition": {
                "text": "The escape \\0 marks the end of this public sequence.",
                "evidence_ids": ["e1"],
            },
            "key_points": [{
                "text": "Readers must preserve the escape \\0 exactly.",
                "evidence_ids": ["e1"],
            }],
        }]
    }

    artifact = validate_concepts(
        json.dumps(valid_output),
        semantic_request=request,
        evidence_aliases={"e1": "evidence-one"},
        page_ref="page:sha256:" + "1" * 64,
        input_binding={"evidence_allowlist": ["evidence-one"]},
        attempt=1,
    )
    assert artifact["concepts"][0]["definition"]["text"] == (
        "The escape \\0 marks the end of this public sequence."
    )

    changed = json.loads(json.dumps(valid_output))
    changed["concepts"][0]["definition"]["text"] = (
        "The escape 0 marks the end of this public sequence."
    )
    rejected = validate_concepts(
        json.dumps(changed),
        semantic_request=request,
        evidence_aliases={"e1": "evidence-one"},
        page_ref="page:sha256:" + "1" * 64,
        input_binding={"evidence_allowlist": ["evidence-one"]},
        attempt=1,
    )
    assert rejected["concepts"] == []
    assert rejected["rejected_candidates"][0]["reason_codes"] == [
        "CLAIM_EVIDENCE_UNSUPPORTED"
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("label", "  \t\n  "),
        ("definition", {"text": "safe\x00unsafe", "evidence_ids": ["e1"]}),
    ],
)
def test_candidate_text_still_rejects_empty_long_or_control_values(field, value):
    output = _output()
    output["concepts"][0][field] = value
    artifact = _validate(json.dumps(output, ensure_ascii=False, separators=(",", ":")))
    assert artifact["concepts"] == []


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
    output["concepts"][0]["definition"]["evidence_ids"] = ["ｅ1"]
    assert _validate(json.dumps(output, ensure_ascii=False, separators=(",", ":")))["concepts"] == []

    output = _output()
    output["concepts"].append(json.loads(json.dumps(output["concepts"][0])))
    output["concepts"][0]["definition"]["evidence_ids"] = ["e1", "e1"]
    artifact = _validate(json.dumps(output, separators=(",", ":")))
    assert artifact["rejected_candidates"][0]["reason_codes"] == [
        "DUPLICATE_EVIDENCE_REFERENCE"
    ]
    assert len(artifact["concepts"]) == 1


def test_large_page_request_splits_without_losing_formal_evidence_ids():
    request = _request_with_evidence(
        [
            {"id": "e1", "text": "first concept"},
            {"id": "e2", "text": "second concept"},
        ]
    )
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
                        "definition": {"text": f"{label} definition", "evidence_ids": list(aliases)},
                        "key_points": [{"text": f"{label} point", "evidence_ids": list(aliases)}],
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
        for claim in [concept["definition"], *concept["key_points"]]
        for evidence_id in claim["evidence_ids"]
    } == {"formal-one", "formal-two"}


def test_single_large_evidence_split_removes_only_boundary_whitespace():
    first, second = split_semantic_request(
        _request_with_evidence(
            [{"id": "e1", "text": "first half   second half"}]
        )
    )
    assert first["evidence"] == [{"id": "e1", "text": "first half"}]
    assert second["evidence"] == [{"id": "e1", "text": "second half"}]
