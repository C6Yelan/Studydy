import json
from copy import deepcopy
from pathlib import Path
import unicodedata

import pytest

from pdf_evidence.concept_generation import (
    SemanticOutputError,
    build_semantic_request,
    combine_semantic_batches,
    fitted_semantic_request_matches_source,
    split_semantic_request,
    validate_concepts,
    validate_semantic_request,
)
from pdf_evidence.document_context import build_document_contexts
from pdf_evidence.ocr_page_evidence import canonical_sha256


FIXTURES = Path(__file__).parents[2] / "local_ai" / "tests" / "fixtures"


def _request():
    return json.loads((FIXTURES / "semantic_request.json").read_text(encoding="utf-8"))


def _output():
    return json.loads((FIXTURES / "semantic_model_output.json").read_text(encoding="utf-8"))


def _request_with_evidence(evidence):
    request = _request()
    request["evidence"] = [
        {"id": item["id"], "text": item["text"]} for item in evidence
    ]
    request["document_context"]["current_blocks"] = [
        {
            "evidence_id": item["id"],
            "kind": item.get("kind", "paragraph"),
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
    context_identity = {
        key: value
        for key, value in request["document_context"].items()
        if key != "document_context_id"
    }
    request["document_context"]["document_context_id"] = (
        "concept-context:sha256:" + canonical_sha256(context_identity)
    )
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


def _page_with_blocks(blocks):
    return {
        "schema": "page-evidence/v3",
        "material_id": "material-alpha",
        "material_revision": "revision-one",
        "section_id": "section-public",
        "page_ref": "page:sha256:" + "1" * 64,
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


def _unkeyed_question_page(include_normal_detail=False):
    blocks = [
        ("paragraph", "Which statement is correct? (Choose one)"),
        ("paragraph", "(K) Rivers always flow uphill."),
        ("paragraph", "(L) Rivers never carry sediment."),
        ("paragraph", "(M) Rivers always have equal depth."),
        ("paragraph", "(N) Rivers can transport sediment."),
    ]
    if include_normal_detail:
        blocks.append(("paragraph", "River flow transports sediment downstream."))
    return _page_with_blocks(blocks)


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


def test_semantic_evidence_normalizes_controls_without_changing_page():
    source_text = "Price\x01equals P\\0\twhen x>=2.\nNext\x1fline.\x07"
    page = _page_with_blocks([("paragraph", source_text)])
    unchanged_page = deepcopy(page)
    context = build_document_contexts([page])[0]

    request, aliases = build_semantic_request(page, context)

    assert request["evidence"] == [{
        "id": "e1",
        "text": "Price equals P\\0\twhen x>=2.\nNext line.",
    }]
    assert aliases == {"e1": "evidence-0"}
    assert request["document_context"]["source_context_id"] == context["context_id"]
    assert page == unchanged_page
    assert page["evidence_blocks"][0]["text"] == source_text
    assert page["page_evidence_id"] == unchanged_page["page_evidence_id"]


def test_every_disallowed_c0_control_becomes_a_word_separator():
    controls = "".join(
        chr(codepoint)
        for codepoint in range(32)
        if chr(codepoint) not in "\n\t"
    )
    page = _page_with_blocks([("paragraph", f"left{controls}right")])

    request, _ = build_semantic_request(
        page, build_document_contexts([page])[0]
    )

    assert request["evidence"][0]["text"] == (
        "left" + " " * len(controls) + "right"
    )


def test_control_only_evidence_and_direct_control_in_request_fail_closed():
    page = _page_with_blocks([("paragraph", "\x01\x02")])
    with pytest.raises(SemanticOutputError, match="INVALID_TEXT_FIELD"):
        build_semantic_request(page, build_document_contexts([page])[0])

    request = _request_with_evidence([
        {"id": "e1", "text": "Unsafe\x01request"}
    ])
    with pytest.raises(SemanticOutputError, match="INVALID_TEXT_FIELD"):
        validate_semantic_request(request)


def test_control_normalization_reaches_grouping_and_keeps_normal_claims():
    page = _page_with_blocks([
        ("paragraph", "Which statement is correct? (Choose one)"),
        ("paragraph", "(K) Rivers always flow uphill."),
        ("paragraph", "(L) Rivers never carry sediment."),
        ("paragraph", "(M) Rivers always have equal depth."),
        ("paragraph", "(N) Rivers can transport sediment."),
        ("paragraph", "Marginal\x01revenue equals marginal cost."),
        ("paragraph", "Profit\x02is maximized at that quantity."),
    ])
    unchanged_page = deepcopy(page)
    request, aliases = build_semantic_request(
        page, build_document_contexts([page])[0]
    )

    assert request["assessment_groups"][0]["question_evidence_id"] == "e1"
    assert request["assessment_groups"][0]["option_evidence_ids"] == [
        "e2", "e3", "e4", "e5"
    ]
    assert request["assessment_groups"][0]["has_reliable_answer"] is False
    assert request["evidence"][5:] == [
        {"id": "e6", "text": "Marginal revenue equals marginal cost."},
        {"id": "e7", "text": "Profit is maximized at that quantity."},
    ]
    assert page == unchanged_page

    normal_artifact = validate_concepts(
        json.dumps({
            "concepts": [{
                "label": "Profit maximization",
                "definition": {
                    "text": "Marginal revenue equals marginal cost.",
                    "evidence_ids": ["e6"],
                },
                "key_points": [{
                    "text": "Profit is maximized at that quantity.",
                    "evidence_ids": ["e7"],
                }],
            }]
        }),
        semantic_request=request,
        evidence_aliases=aliases,
        page_ref=page["page_ref"],
        input_binding={"evidence_allowlist": list(aliases.values())},
        attempt=1,
    )
    assert len(normal_artifact["concepts"]) == 1
    assert normal_artifact["concepts"][0]["processing"] == "succeeded"


def test_unkeyed_question_keeps_grouping_and_rejects_option_claims():
    page = _unkeyed_question_page()
    context = build_document_contexts([page])[0]
    request, aliases = build_semantic_request(page, context)

    assert request["schema"] == "concept-generation-input/v7"
    assert request["document_context"]["source_context_id"] == context["context_id"]
    assert request["assessment_groups"] == [{
        "assessment_id": request["assessment_groups"][0]["assessment_id"],
        "question_evidence_id": "e1",
        "option_evidence_ids": ["e2", "e3", "e4", "e5"],
        "has_reliable_answer": False,
    }]
    assert request["assessment_groups"][0]["assessment_id"].startswith(
        "assessment-context:sha256:"
    )
    assert aliases == {
        f"e{index}": f"evidence-{index - 1}" for index in range(1, 6)
    }
    assert len(request["evidence"]) == 5

    model_output = {
        "concepts": [{
            "label": "River choices",
            "definition": {
                "text": "Rivers can transport sediment.",
                "evidence_ids": ["e5"],
            },
            "key_points": [
                {
                    "text": "Rivers always flow uphill.",
                    "evidence_ids": ["e2"],
                },
                {
                    "text": "Rivers never carry sediment.",
                    "evidence_ids": ["e3"],
                },
                {
                    "text": "Rivers always have equal depth.",
                    "evidence_ids": ["e4"],
                },
            ],
        }]
    }
    artifact = validate_concepts(
        json.dumps(model_output),
        semantic_request=request,
        evidence_aliases=aliases,
        page_ref=page["page_ref"],
        input_binding={"evidence_allowlist": list(aliases.values())},
        attempt=1,
    )

    assert artifact["concepts"] == []
    assert artifact["rejected_candidates"][0]["reason_codes"] == [
        "CLAIM_UNKEYED_ASSESSMENT_OPTION"
    ]
    assert artifact["processing"] == "partial"


@pytest.mark.parametrize(
    ("claim_text", "evidence_ids"),
    [
        ("Which statement is correct?", ["e1"]),
        (
            "Which statement is correct? Rivers can transport sediment.",
            ["e1", "e5"],
        ),
    ],
    ids=["stem-only", "stem-and-option"],
)
def test_unkeyed_assessment_stem_cannot_bypass_claim_rejection(
    claim_text, evidence_ids
):
    page = _unkeyed_question_page()
    request, aliases = build_semantic_request(
        page, build_document_contexts([page])[0]
    )

    artifact = validate_concepts(
        json.dumps({
            "concepts": [{
                "label": "River question",
                "definition": {
                    "text": claim_text,
                    "evidence_ids": evidence_ids,
                },
                "key_points": [{
                    "text": claim_text,
                    "evidence_ids": evidence_ids,
                }],
            }]
        }),
        semantic_request=request,
        evidence_aliases=aliases,
        page_ref=page["page_ref"],
        input_binding={"evidence_allowlist": list(aliases.values())},
        attempt=1,
    )

    assert artifact["concepts"] == []
    assert artifact["rejected_candidates"][0]["reason_codes"] == [
        "CLAIM_UNKEYED_ASSESSMENT_OPTION"
    ]
    combined = combine_semantic_batches(
        [artifact],
        page_ref=page["page_ref"],
        input_binding={"evidence_allowlist": list(aliases.values())},
    )
    assert combined["concepts"] == []


def test_missing_or_fabricated_assessment_context_is_invalid():
    page = _unkeyed_question_page()
    request, _ = build_semantic_request(
        page, build_document_contexts([page])[0]
    )

    missing = deepcopy(request)
    missing["assessment_groups"] = []
    with pytest.raises(SemanticOutputError, match="INPUT_SCHEMA_INVALID"):
        validate_semantic_request(missing)

    fabricated = deepcopy(request)
    fabricated["assessment_groups"][0]["option_evidence_ids"][-1] = "e99"
    with pytest.raises(SemanticOutputError, match="INPUT_SCHEMA_INVALID"):
        validate_semantic_request(fabricated)


def test_normal_heading_definition_examples_and_bullets_remain_factual():
    page = _page_with_blocks([
        ("heading", "River transport"),
        ("paragraph", "River transport moves sediment downstream."),
        ("list", "Definition: Sediment is material carried by water."),
        ("list", "(K) An example shows rivers carrying sand."),
        ("list", "(L) Another example shows rivers carrying silt."),
        ("list", "(M) A final example shows rivers carrying clay."),
    ])
    request, aliases = build_semantic_request(
        page, build_document_contexts([page])[0]
    )

    assert request["assessment_groups"] == []
    assert [block["kind"] for block in request["document_context"]["current_blocks"]] == [
        "heading",
        "paragraph",
        "list",
        "list",
        "list",
        "list",
    ]
    artifact = validate_concepts(
        json.dumps({
            "concepts": [{
                "label": "River transport",
                "definition": {
                    "text": "River transport moves sediment downstream.",
                    "evidence_ids": ["e2"],
                },
                "key_points": [
                    {
                        "text": "Sediment is material carried by water.",
                        "evidence_ids": ["e3"],
                    },
                    {
                        "text": "An example shows rivers carrying sand.",
                        "evidence_ids": ["e4"],
                    },
                ],
            }]
        }),
        semantic_request=request,
        evidence_aliases=aliases,
        page_ref=page["page_ref"],
        input_binding={"evidence_allowlist": list(aliases.values())},
        attempt=1,
    )

    assert len(artifact["concepts"]) == 1
    assert artifact["concepts"][0]["processing"] == "succeeded"


def test_request_split_keeps_question_and_options_together():
    page = _unkeyed_question_page(include_normal_detail=True)
    request, _ = build_semantic_request(
        page, build_document_contexts([page])[0]
    )

    question_batch, normal_batch = split_semantic_request(request)

    assert [item["id"] for item in question_batch["evidence"]] == [
        "e1", "e2", "e3", "e4", "e5"
    ]
    assert len(question_batch["assessment_groups"]) == 1
    assert normal_batch["evidence"] == [{
        "id": "e6",
        "text": "River flow transports sediment downstream.",
    }]
    assert normal_batch["assessment_groups"] == []
    assert fitted_semantic_request_matches_source(question_batch, request)
    assert fitted_semantic_request_matches_source(normal_batch, request)

    question_only = _unkeyed_question_page()
    question_request, _ = build_semantic_request(
        question_only, build_document_contexts([question_only])[0]
    )
    with pytest.raises(SemanticOutputError, match="MODEL_INPUT_TOO_LARGE"):
        split_semantic_request(question_request)


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


@pytest.mark.parametrize(
    "claim_text",
    [
        "However, the value changes.",
        "因此，數值會改變。",
        "Result:",
        "Polymorphism",
    ],
)
def test_complete_connector_colon_and_short_legal_claims_remain_eligible(
    claim_text,
):
    evidence_text = (
        "A complete rule explains the value. "
        f"{claim_text} A second complete point is grounded."
    )
    artifact = validate_concepts(
        json.dumps({
            "concepts": [{
                "label": "Complete rule",
                "definition": {
                    "text": "A complete rule explains the value.",
                    "evidence_ids": ["e1"],
                },
                "key_points": [
                    {"text": claim_text, "evidence_ids": ["e1"]},
                    {
                        "text": "A second complete point is grounded.",
                        "evidence_ids": ["e1"],
                    },
                ],
            }]
        }),
        semantic_request=_request_with_evidence(
            [{"id": "e1", "text": evidence_text}]
        ),
        evidence_aliases={"e1": "evidence-one"},
        page_ref="page:sha256:" + "1" * 64,
        input_binding={"evidence_allowlist": ["evidence-one"]},
        attempt=1,
    )

    normalized_claim = " ".join(unicodedata.normalize("NFKC", claim_text).split())
    assert normalized_claim in [
        point["text"] for point in artifact["concepts"][0]["key_points"]
    ]
    assert artifact["concepts"][0]["processing"] == "succeeded"


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

    request = _request()
    request["document_context"]["current_blocks"][0].pop("kind")
    with pytest.raises(SemanticOutputError, match="INPUT_SCHEMA_INVALID"):
        validate_concepts(
            json.dumps(_output()),
            semantic_request=request,
            evidence_aliases={"e1": "evidence-one"},
            page_ref="page:sha256:" + "1" * 64,
            input_binding={"evidence_allowlist": ["evidence-one"]},
            attempt=1,
        )


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
    assert first["document_context"]["current_blocks"][0]["kind"] == "paragraph"
    assert second["document_context"]["current_blocks"][0]["kind"] == "paragraph"

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
    source = _request_with_evidence(
        [{"id": "e1", "text": "first half   second half"}]
    )
    first, second = split_semantic_request(source)
    assert first["evidence"] == [{"id": "e1", "text": "first half"}]
    assert second["evidence"] == [{"id": "e1", "text": "second half"}]
    assert fitted_semantic_request_matches_source(first, source)
    assert fitted_semantic_request_matches_source(second, source)
    first_quarter, second_quarter = split_semantic_request(first)
    assert fitted_semantic_request_matches_source(first_quarter, source)
    assert fitted_semantic_request_matches_source(second_quarter, source)

    tampered = json.loads(json.dumps(first_quarter))
    tampered["evidence"][0]["text"] = "not a deterministic slice"
    assert not fitted_semantic_request_matches_source(tampered, source)


def test_multi_source_second_evidence_recursive_quarter_matches_exact_alias():
    source = _request_with_evidence(
        [
            {"id": "e1", "text": "first source"},
            {"id": "e2", "text": "second source has four deterministic parts"},
        ]
    )
    _, second = split_semantic_request(source)
    _, second_right = split_semantic_request(second)
    quarter, _ = split_semantic_request(second_right)

    assert quarter["evidence"][0]["id"] == "e2"
    assert fitted_semantic_request_matches_source(quarter, source)

    tampered = json.loads(json.dumps(quarter))
    tampered["evidence"][0]["text"] = "nearby but not derivable"
    assert not fitted_semantic_request_matches_source(tampered, source)
