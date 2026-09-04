from __future__ import annotations

import json
from uuid import uuid4

import pytest

import learning_adaptation.assessment_generation as generation
from learning_adaptation.assessment_generation import (
    AssessmentGenerationError,
    _Candidate,
    _Grounding,
    _generate_documents,
    _grounding,
    _proposal_candidates,
    _rank_candidates,
    _repair_candidates,
    _request_document,
)
from learning_adaptation.assessment_items import project_public_assessment
from learning_adaptation.map_context import (
    ClaimContext,
    EvidenceLocator,
    FormalConceptContext,
)
from pdf_evidence.local_ai_process import LocalAIError


def _identifier(kind: str, digit: str) -> str:
    return f"{kind}:sha256:{digit * 64}"


def _concept() -> FormalConceptContext:
    evidence = EvidenceLocator(
        evidence_id=_identifier("evidence", "1"),
        page_ref="page:sha256:" + "2" * 64,
        page_number=1,
        coordinate_space="pdf_points_top_left",
        bbox=(0, 0, 20, 20),
        text="A stack stores its first element at stack[0].",
    )
    claim = ClaimContext(
        claim_id=_identifier("claim", "3"),
        text="The first stack element is stored at stack[0].",
        evidence=(evidence,),
    )
    return FormalConceptContext(
        formal_concept_id=_identifier("formal-concept", "4"),
        label="Stack storage",
        source_page_numbers=(1,),
        claims=(claim,),
        supplementary_resources=(),
    )


def _policy() -> dict:
    return {
        "policy_revision": "assessment-generation-policy/v1",
        "shared_models": {
            "semantic_model_id": "Qwen/Qwen3.8-27B-FP8",
            "semantic_revision": "content-sha256:" + "5" * 64,
            "verifier_model_id": "MoritzLaurer/mDeBERTa-v3-base-mnli-xnli",
            "verifier_revision": "8adb042d524ecd5c26d3e3ba0e3fbcf7e2d0864c",
        },
        "proposal": {
            "prompt": "proposal",
            "prompt_sha256": "6" * 64,
            "generation": {"max_tokens": 2800},
            "retry": {
                "max_attempts": 2,
                "retryable_reasons": [
                    "CONCEPT_API_TIMEOUT",
                    "CONCEPT_API_UNAVAILABLE",
                ],
            },
        },
        "repair": {
            "prompt": "repair",
            "prompt_sha256": "7" * 64,
            "generation": {"max_tokens": 3400},
            "retry": {
                "max_attempts": 2,
                "retryable_reasons": [
                    "CONCEPT_API_TIMEOUT",
                    "CONCEPT_API_UNAVAILABLE",
                ],
            },
        },
        "verifier": {
            "startup_timeout_seconds": 120,
            "entailment_margin_threshold": 0.1,
            "multiple_support_risk_threshold": 0.4,
        },
        "novelty": {
            "decision_rule": (
                "publication-independent-mastery-qualification/v1"
            ),
            "novel_requirement": (
                "each-prior-no-entailment-and-directional-contradiction/v1"
            ),
            "qualification_outcomes": {
                "no_prior": "distinct-mastery-evidence:no-prior/v1",
                "verified_distinct": (
                    "distinct-mastery-evidence:verified-distinct/v1"
                ),
                "neutral": "distinct-mastery-evidence:neutral/v1",
                "timeout": "distinct-mastery-evidence:timeout/v1",
                "invalid_response": (
                    "distinct-mastery-evidence:invalid-response/v1"
                ),
                "unavailable": "distinct-mastery-evidence:unavailable/v1",
                "unsupported": "distinct-mastery-evidence:unsupported/v1",
                "over_limit": "distinct-mastery-evidence:over-limit/v1",
            },
            "maximum_prior_items": 32,
        },
        "limits": {"maximum_evidence_characters": 32768},
    }


def _settings() -> dict:
    return {
        "assessment_runtime_lock": _policy(),
        "concept_api_base_url": "http://127.0.0.1:8000",
        "concept_model": "Qwen/Qwen3.8-27B-FP8",
        "concept_max_model_len": 32768,
    }


def _grounded() -> _Grounding:
    concept = _concept()
    return _grounding(concept, concept.claims[0].claim_id, _policy())


def _proposal_document() -> str:
    return json.dumps(
        {
            "candidates": [
                {
                    "support_ids": ["e1"],
                    "prompt": f"Which statement matches candidate {index}?",
                    "correct_option": f"Supported answer {index}",
                    "distractors": [
                        f"Wrong {index}-1",
                        f"Wrong {index}-2",
                        f"Wrong {index}-3",
                    ],
                }
                for index in range(3)
            ]
        }
    )


def _repair_document(*, valid: bool = True) -> str:
    return json.dumps(
        {
            "candidates": [
                {
                    "support_ids": ["e1"],
                    "prompt": (
                        "According to the provided material, which statement "
                        f"matches repaired candidate {index}?"
                    ),
                    "correct_option": "The first element is stored at stack[0].",
                    "distractors": [
                        {
                            "text": f"The first element is stored at stack[{offset}].",
                            "support_id": "e1",
                            "changed_from": "stack[0]",
                            "changed_to": (
                                f"stack[{offset}]" if valid else "stack[0]"
                            ),
                        }
                        for offset in range(1, 6)
                    ],
                }
                for index in range(3)
            ]
        }
    )


class _Verifier:
    def __init__(
        self,
        scores: dict[str, list[float]],
        *,
        novelty_probabilities: tuple[float, float] = (0.1, 0.1),
        novelty_entailments: tuple[bool, bool] = (False, False),
        novelty_contradictions: tuple[bool, bool] = (True, False),
    ) -> None:
        self.scores = scores
        self.novelty_probabilities = novelty_probabilities
        self.novelty_entailments = novelty_entailments
        self.novelty_contradictions = novelty_contradictions
        self.novelty_requests = 0
        self.closed = False
        self.aborted = False

    def request(self, request, _timeout):
        assert _timeout is None
        if request["schema"] == "local-assessment-novelty-request/v1":
            self.novelty_requests += 1
            return {
                "schema": "local-assessment-novelty-response/v1",
                "request_id": request["request_id"],
                "status": "scored",
                "comparisons": [
                    {
                        "candidate_to_prior": self.novelty_probabilities[0],
                        "prior_to_candidate": self.novelty_probabilities[1],
                        "candidate_entails_prior": self.novelty_entailments[0],
                        "prior_entails_candidate": self.novelty_entailments[1],
                        "candidate_contradicts_prior": (
                            self.novelty_contradictions[0]
                        ),
                        "prior_contradicts_candidate": (
                            self.novelty_contradictions[1]
                        ),
                    }
                    for _ in request["prior_focuses"]
                ],
            }
        return {
            "schema": "local-assessment-verifier-response/v2",
            "request_id": request["request_id"],
            "status": "scored",
            "entailment_probabilities": self.scores[request["options"][0]],
        }

    def close(self) -> None:
        self.closed = True

    def abort(self) -> None:
        self.aborted = True


def test_proposal_and_mutation_proof_gates_fail_closed():
    grounding = _grounded()
    assert "output_language" not in _request_document(
        grounding, include_output_language=False
    )
    assert _request_document(
        grounding, include_output_language=True
    )["output_language"] == "English"
    proposals = _proposal_candidates(_proposal_document(), grounding)
    repairs = _repair_candidates(_repair_document(), grounding)

    assert len(proposals) == 3
    assert len(repairs) == 3
    assert repairs[0].distractors == (
        "The first element is stored at stack[1].",
        "The first element is stored at stack[2].",
        "The first element is stored at stack[3].",
    )
    assert _repair_candidates(_repair_document(valid=False), grounding) == []

    invalid = json.loads(_proposal_document())
    invalid["candidates"][0]["prompt"] = "The correct answer is shown below."
    invalid["candidates"][1]["distractors"][0] = "Supported answer 1"
    invalid["candidates"][2]["correct_option"] = "A"
    assert _proposal_candidates(json.dumps(invalid), grounding) == []

    symbolic = json.loads(_proposal_document())
    symbolic["candidates"][0]["correct_option"] = "parent(i) = i + 1"
    symbolic["candidates"][0]["distractors"] = [
        "parent(i) = i - 1",
        "parent(i) = i * 1",
        "parent(i) = i / 1",
    ]
    assert _proposal_candidates(json.dumps(symbolic), grounding)[0].index == 0


def test_safe_proposal_builds_contract_and_private_provenance(monkeypatch):
    verifier = _Verifier(
        {
            "Supported answer 0": [0.55, 0.2, 0.1, 0.1],
            "Supported answer 1": [0.9, 0.1, 0.1, 0.1],
            "Supported answer 2": [0.6, 0.3, 0.2, 0.1],
        }
    )
    monkeypatch.setattr(
        generation, "start_assessment_process", lambda *_: verifier
    )
    monkeypatch.setattr(
        generation, "_request_stage", lambda *args, **kwargs: _proposal_document()
    )

    documents, provenance, novelty = _generate_documents(
        uuid4(),
        _identifier("knowledge-map", "8"),
        _grounded(),
        _settings(),
        "9" * 64,
        frozenset(),
    )

    assert verifier.closed and not verifier.aborted
    assert provenance["selected_stage"] == "proposal"
    assert provenance["selected_candidate_index"] == 1
    assert provenance["multiple_support_risk"] is False
    assert novelty.compared_semantic_identities == []
    assert documents.public_document.source_evidence_ids == [
        _identifier("evidence", "1")
    ]
    public = project_public_assessment(documents)
    assert "correct_option_id" not in json.dumps(public)
    assert documents.private_answer_document.correct_option_id in {
        option.option_id for option in documents.public_document.options
    }
    assert documents.private_answer_document.rationale == (
        "教材依據明確記載：A stack stores its first element at stack[0]."
    )


def test_empty_validated_proposals_use_existing_repair_stage(monkeypatch):
    invalid_proposal = json.loads(_proposal_document())
    for candidate in invalid_proposal["candidates"]:
        candidate["prompt"] = "The correct answer is shown below."
    responses = iter([json.dumps(invalid_proposal), _repair_document()])
    verifier = _Verifier({
        "The first element is stored at stack[0].": [
            0.99, 0.01, 0.01, 0.01,
        ],
    })
    monkeypatch.setattr(
        generation, "start_assessment_process", lambda *_: verifier
    )
    monkeypatch.setattr(
        generation, "_request_stage", lambda *args, **kwargs: next(responses)
    )

    _, provenance, _ = _generate_documents(
        uuid4(),
        _identifier("knowledge-map", "8"),
        _grounded(),
        _settings(),
        "9" * 64,
        frozenset(),
    )

    assert provenance["selected_stage"] == "repair"
    assert provenance["multiple_support_risk"] is False
    assert verifier.closed and not verifier.aborted


def test_repeated_generation_selects_unused_safe_candidate_then_fails_closed(
    monkeypatch,
):
    verifier = _Verifier(
        {
            "Supported answer 0": [0.55, 0.2, 0.1, 0.1],
            "Supported answer 1": [0.9, 0.1, 0.1, 0.1],
            "Supported answer 2": [0.6, 0.3, 0.2, 0.1],
        }
    )
    monkeypatch.setattr(
        generation, "start_assessment_process", lambda *_: verifier
    )
    monkeypatch.setattr(
        generation, "_request_stage", lambda *args, **kwargs: _proposal_document()
    )
    session_id = uuid4()
    used = set()

    for expected_candidate_index in (1, 0, 2):
        documents, provenance, novelty = _generate_documents(
            session_id,
            _identifier("knowledge-map", "8"),
            _grounded(),
            _settings(),
            "9" * 64,
            frozenset(used),
        )
        assert provenance["selected_candidate_index"] == expected_candidate_index
        assert novelty.semantic_identity not in used
        used.add(novelty.semantic_identity)

    with pytest.raises(
        AssessmentGenerationError, match="^ASSESSMENT_NO_NEW_SAFE_ITEM$"
    ):
        _generate_documents(
            session_id,
            _identifier("knowledge-map", "8"),
            _grounded(),
            _settings(),
            "9" * 64,
            frozenset(used),
        )


def test_exhausted_risky_repairs_do_not_block_lower_safe_proposals(monkeypatch):
    proposal = json.loads(_proposal_document())
    proposal["candidates"][0]["correct_option"] = "Risky supported answer"
    proposal["candidates"][1]["correct_option"] = "Safe lower answer 1"
    proposal["candidates"][2]["correct_option"] = "Safe lower answer 2"
    verifier = _Verifier(
        {
            "Risky supported answer": [0.99, 0.45, 0.1, 0.1],
            "Safe lower answer 1": [0.8, 0.3, 0.1, 0.1],
            "Safe lower answer 2": [0.75, 0.3, 0.1, 0.1],
            "The first element is stored at stack[0].": [
                0.99,
                0.01,
                0.01,
                0.01,
            ],
        }
    )
    monkeypatch.setattr(
        generation, "start_assessment_process", lambda *_: verifier
    )
    monkeypatch.setattr(
        generation,
        "_request_stage",
        lambda _client, _settings, stage, *_: (
            _repair_document()
            if stage["prompt"] == "repair"
            else json.dumps(proposal)
        ),
    )
    session_id = uuid4()
    used = set()
    selected = []

    for _ in range(5):
        documents, provenance, novelty = _generate_documents(
            session_id,
            _identifier("knowledge-map", "8"),
            _grounded(),
            _settings(),
            "9" * 64,
            frozenset(used),
        )
        assert novelty.semantic_identity not in used
        used.add(novelty.semantic_identity)
        selected.append(
            (
                provenance["selected_stage"],
                provenance["selected_candidate_index"],
            )
        )

    assert selected == [
        ("repair", 0),
        ("repair", 1),
        ("repair", 2),
        ("proposal", 1),
        ("proposal", 2),
    ]
    assert len(used) == 5
    with pytest.raises(
        AssessmentGenerationError, match="^ASSESSMENT_NO_NEW_SAFE_ITEM$"
    ):
        _generate_documents(
            session_id,
            _identifier("knowledge-map", "8"),
            _grounded(),
            _settings(),
            "9" * 64,
            frozenset(used),
        )


def test_verifier_over_token_boundary_rejects_before_selection(monkeypatch):
    class RejectingVerifier(_Verifier):
        def request(self, request, _timeout):
            return {
                "schema": "local-assessment-verifier-response/v2",
                "request_id": request["request_id"],
                "status": "rejected",
                "reason_code": "ASSESSMENT_VERIFIER_INPUT_TOO_LARGE",
            }

    verifier = RejectingVerifier({})
    monkeypatch.setattr(
        generation, "start_assessment_process", lambda *_: verifier
    )
    monkeypatch.setattr(
        generation, "_request_stage", lambda *args, **kwargs: _proposal_document()
    )

    with pytest.raises(
        AssessmentGenerationError,
        match="^ASSESSMENT_VERIFIER_INPUT_TOO_LARGE$",
    ):
        _generate_documents(
            uuid4(),
            _identifier("knowledge-map", "8"),
            _grounded(),
            _settings(),
            "9" * 64,
            frozenset(),
        )


def test_directional_entailment_publishes_without_mastery_qualification(
    monkeypatch,
):
    scores = {
        "Supported answer 0": [0.55, 0.2, 0.1, 0.1],
        "Supported answer 1": [0.9, 0.1, 0.1, 0.1],
        "Supported answer 2": [0.6, 0.3, 0.2, 0.1],
        "The first element is stored at stack[0].": [
            0.99,
            0.01,
            0.01,
            0.01,
        ],
    }
    first_verifier = _Verifier(scores)
    monkeypatch.setattr(
        generation, "start_assessment_process", lambda *_: first_verifier
    )
    monkeypatch.setattr(
        generation, "_request_stage", lambda *args, **kwargs: _proposal_document()
    )
    session_id = uuid4()
    _, _, first_novelty = _generate_documents(
        session_id,
        _identifier("knowledge-map", "8"),
        _grounded(),
        _settings(),
        "9" * 64,
        frozenset(),
    )

    duplicate_verifier = _Verifier(
        scores,
        novelty_probabilities=(0.554235, 0.133799),
        novelty_entailments=(True, False),
        novelty_contradictions=(False, True),
    )
    monkeypatch.setattr(
        generation, "start_assessment_process", lambda *_: duplicate_verifier
    )
    monkeypatch.setattr(
        generation,
        "_request_stage",
        lambda _client, _settings, stage, *_: (
            _repair_document()
            if stage["prompt"] == "repair"
            else _proposal_document()
        ),
    )
    _, _, novelty = _generate_documents(
        session_id,
        _identifier("knowledge-map", "8"),
        _grounded(),
        _settings(),
        "9" * 64,
        frozenset({first_novelty.semantic_identity}),
        (first_novelty,),
    )

    assert duplicate_verifier.novelty_requests <= 6
    assert novelty.counts_as_distinct_mastery_evidence is False
    assert novelty.comparison_policy_revision == (
        "distinct-mastery-evidence:neutral/v1"
    )


def test_semantically_distinct_candidate_remains_selectable(monkeypatch):
    verifier = _Verifier(
        {
            "Supported answer 0": [0.55, 0.2, 0.1, 0.1],
            "Supported answer 1": [0.9, 0.1, 0.1, 0.1],
            "Supported answer 2": [0.6, 0.3, 0.2, 0.1],
        },
        novelty_probabilities=(0.021863, 0.003729),
        novelty_contradictions=(True, False),
    )
    monkeypatch.setattr(
        generation, "start_assessment_process", lambda *_: verifier
    )
    monkeypatch.setattr(
        generation, "_request_stage", lambda *args, **kwargs: _proposal_document()
    )
    session_id = uuid4()
    first_documents, _, first_novelty = _generate_documents(
        session_id,
        _identifier("knowledge-map", "8"),
        _grounded(),
        _settings(),
        "9" * 64,
        frozenset(),
    )
    documents, _, novelty = _generate_documents(
        session_id,
        _identifier("knowledge-map", "8"),
        _grounded(),
        _settings(),
        "9" * 64,
        frozenset({first_novelty.semantic_identity}),
        (first_novelty,),
    )

    assert (
        documents.public_document.question_id
        != first_documents.public_document.question_id
    )
    assert novelty.maximum_equivalence_score == 0.003729
    assert novelty.counts_as_distinct_mastery_evidence is True


def test_neutral_pair_publishes_without_mastery_qualification(monkeypatch):
    scores = {
        "Supported answer 0": [0.55, 0.2, 0.1, 0.1],
        "Supported answer 1": [0.9, 0.1, 0.1, 0.1],
        "Supported answer 2": [0.6, 0.3, 0.2, 0.1],
        "The first element is stored at stack[0].": [
            0.99,
            0.01,
            0.01,
            0.01,
        ],
    }
    first_verifier = _Verifier(scores)
    monkeypatch.setattr(
        generation, "start_assessment_process", lambda *_: first_verifier
    )
    monkeypatch.setattr(
        generation, "_request_stage", lambda *args, **kwargs: _proposal_document()
    )
    session_id = uuid4()
    _, _, first_novelty = _generate_documents(
        session_id,
        _identifier("knowledge-map", "8"),
        _grounded(),
        _settings(),
        "9" * 64,
        frozenset(),
    )

    ambiguous_verifier = _Verifier(
        scores,
        novelty_probabilities=(0.018247, 0.012273),
        novelty_entailments=(False, False),
        novelty_contradictions=(False, False),
    )
    monkeypatch.setattr(
        generation, "start_assessment_process", lambda *_: ambiguous_verifier
    )
    monkeypatch.setattr(
        generation,
        "_request_stage",
        lambda _client, _settings, stage, *_: (
            _repair_document()
            if stage["prompt"] == "repair"
            else _proposal_document()
        ),
    )

    _, _, novelty = _generate_documents(
        session_id,
        _identifier("knowledge-map", "8"),
        _grounded(),
        _settings(),
        "9" * 64,
        frozenset({first_novelty.semantic_identity}),
        (first_novelty,),
    )
    assert ambiguous_verifier.novelty_requests <= 6
    assert novelty.counts_as_distinct_mastery_evidence is False
    assert novelty.comparison_policy_revision == (
        "distinct-mastery-evidence:neutral/v1"
    )


@pytest.mark.parametrize(
    ("novelty_failure", "expected_policy"),
    [
        (
            LocalAIError("ASSESSMENT_VERIFIER_TIMEOUT"),
            "distinct-mastery-evidence:timeout/v1",
        ),
        (
            LocalAIError("ASSESSMENT_VERIFIER_UNAVAILABLE"),
            "distinct-mastery-evidence:unavailable/v1",
        ),
        (
            LocalAIError("ASSESSMENT_VERIFIER_RESPONSE_INVALID"),
            "distinct-mastery-evidence:invalid-response/v1",
        ),
        (
            LocalAIError("ASSESSMENT_VERIFIER_DEPENDENCY_MISSING"),
            "distinct-mastery-evidence:unsupported/v1",
        ),
    ],
)
def test_novelty_runtime_failure_publishes_without_mastery_qualification(
    monkeypatch, novelty_failure, expected_policy
):
    scores = {
        "Supported answer 0": [0.55, 0.2, 0.1, 0.1],
        "Supported answer 1": [0.9, 0.1, 0.1, 0.1],
        "Supported answer 2": [0.6, 0.3, 0.2, 0.1],
    }
    monkeypatch.setattr(
        generation, "_request_stage", lambda *args, **kwargs: _proposal_document()
    )
    first_verifier = _Verifier(scores)
    monkeypatch.setattr(
        generation, "start_assessment_process", lambda *_: first_verifier
    )
    session_id = uuid4()
    _, _, first_novelty = _generate_documents(
        session_id,
        _identifier("knowledge-map", "8"),
        _grounded(),
        _settings(),
        "9" * 64,
        frozenset(),
    )

    class UncertainVerifier(_Verifier):
        def request(self, request, timeout):
            if request["schema"] == "local-assessment-novelty-request/v1":
                raise novelty_failure
            return super().request(request, timeout)

    uncertain_verifier = UncertainVerifier(scores)
    monkeypatch.setattr(
        generation,
        "start_assessment_process",
        lambda *_: uncertain_verifier,
    )
    _, _, novelty = _generate_documents(
        session_id,
        _identifier("knowledge-map", "8"),
        _grounded(),
        _settings(),
        "9" * 64,
        frozenset({first_novelty.semantic_identity}),
        (first_novelty,),
    )

    assert novelty.counts_as_distinct_mastery_evidence is False
    assert novelty.comparison_policy_revision == expected_policy


def test_novelty_over_limit_publishes_without_comparison(monkeypatch):
    verifier = _Verifier({
        "Supported answer 0": [0.55, 0.2, 0.1, 0.1],
        "Supported answer 1": [0.9, 0.1, 0.1, 0.1],
        "Supported answer 2": [0.6, 0.3, 0.2, 0.1],
    })
    monkeypatch.setattr(
        generation, "start_assessment_process", lambda *_: verifier
    )
    monkeypatch.setattr(
        generation, "_request_stage", lambda *args, **kwargs: _proposal_document()
    )
    session_id = uuid4()
    _, _, first_novelty = _generate_documents(
        session_id,
        _identifier("knowledge-map", "8"),
        _grounded(),
        _settings(),
        "9" * 64,
        frozenset(),
    )
    _, _, novelty = _generate_documents(
        session_id,
        _identifier("knowledge-map", "8"),
        _grounded(),
        _settings(),
        "9" * 64,
        frozenset({first_novelty.semantic_identity}),
        (first_novelty,) * 33,
    )

    assert verifier.novelty_requests == 0
    assert novelty.counts_as_distinct_mastery_evidence is False
    assert novelty.comparison_policy_revision == (
        "distinct-mastery-evidence:over-limit/v1"
    )


def test_selected_evidence_must_independently_support_correct_option():
    first = EvidenceLocator(
        evidence_id=_identifier("evidence", "1"),
        page_ref="page:sha256:" + "1" * 64,
        page_number=1,
        coordinate_space="unrotated_pdf_points",
        bbox=(0, 0, 10, 10),
        text="The unrelated value is alpha.",
    )
    second = EvidenceLocator(
        evidence_id=_identifier("evidence", "2"),
        page_ref="page:sha256:" + "2" * 64,
        page_number=2,
        coordinate_space="unrotated_pdf_points",
        bbox=(0, 0, 10, 10),
        text="The supported value is beta.",
    )
    claim = ClaimContext(
        claim_id=_identifier("claim", "3"),
        text="The supported value is beta.",
        evidence=(first, second),
    )
    grounding = _Grounding(
        concept=FormalConceptContext(
            formal_concept_id=_identifier("formal-concept", "4"),
            label="Two evidence claim",
            source_page_numbers=(1, 2),
            claims=(claim,),
            supplementary_resources=(),
        ),
        claim=claim,
        aliases={
            "e1": (first.evidence_id, first.text),
            "e2": (second.evidence_id, second.text),
        },
    )

    class ScopeVerifier:
        def request(self, request, _timeout):
            scores = (
                [0.1, 0.2, 0.1, 0.1]
                if request["premise"] == first.text
                else [0.9, 0.1, 0.1, 0.1]
            )
            return {
                "schema": "local-assessment-verifier-response/v2",
                "request_id": request["request_id"],
                "status": "scored",
                "entailment_probabilities": scores,
            }

    values = {
        "stage": "proposal",
        "index": 0,
        "prompt": "Which value is supported?",
        "correct_option": "The supported value is beta.",
        "distractors": ("gamma", "delta", "epsilon"),
    }
    mismatched = _Candidate(support_aliases=("e1",), **values)
    truthful = _Candidate(support_aliases=("e2",), **values)

    assert _rank_candidates(
        [mismatched], ScopeVerifier(), grounding, _policy()
    ) == []
    ranked = _rank_candidates(
        [truthful], ScopeVerifier(), grounding, _policy()
    )
    assert len(ranked) == 1
    assert ranked[0].selected_evidence_margin == pytest.approx(0.8)
    assert ranked[0].margin == pytest.approx(0.8)


def test_multiple_supported_risk_requires_passing_repair(monkeypatch):
    responses = iter([_proposal_document(), _repair_document()])
    verifier = _Verifier(
        {
            "Supported answer 0": [0.55, 0.2, 0.1, 0.1],
            "Supported answer 1": [0.9, 0.45, 0.1, 0.1],
            "Supported answer 2": [0.6, 0.3, 0.2, 0.1],
            "The first element is stored at stack[0].": [
                0.99,
                0.01,
                0.01,
                0.01,
            ],
        }
    )
    monkeypatch.setattr(
        generation, "start_assessment_process", lambda *_: verifier
    )
    monkeypatch.setattr(
        generation, "_request_stage", lambda *args, **kwargs: next(responses)
    )

    _, provenance, _ = _generate_documents(
        uuid4(),
        _identifier("knowledge-map", "8"),
        _grounded(),
        _settings(),
        "9" * 64,
        frozenset(),
    )

    assert provenance["selected_stage"] == "repair"
    assert provenance["multiple_support_risk"] is True
    assert provenance["risk_trigger_distractor_entailment"] == 0.45
    assert provenance["maximum_distractor_entailment"] == 0.01


def test_failed_repair_never_promotes_risky_proposal(monkeypatch):
    responses = iter([_proposal_document(), _repair_document(valid=False)])
    verifier = _Verifier(
        {
            "Supported answer 0": [0.55, 0.2, 0.1, 0.1],
            "Supported answer 1": [0.9, 0.45, 0.1, 0.1],
            "Supported answer 2": [0.6, 0.3, 0.2, 0.1],
        }
    )
    monkeypatch.setattr(
        generation, "start_assessment_process", lambda *_: verifier
    )
    monkeypatch.setattr(
        generation, "_request_stage", lambda *args, **kwargs: next(responses)
    )

    _, provenance, _ = _generate_documents(
        uuid4(),
        _identifier("knowledge-map", "8"),
        _grounded(),
        _settings(),
        "9" * 64,
        frozenset(),
    )
    assert provenance["selected_stage"] == "proposal"
    assert provenance["selected_candidate_index"] == 0


def test_failed_repair_rejects_when_no_lower_safe_proposal(monkeypatch):
    responses = iter([_proposal_document(), _repair_document(valid=False)])
    verifier = _Verifier(
        {
            "Supported answer 0": [0.2, 0.15, 0.1, 0.1],
            "Supported answer 1": [0.9, 0.45, 0.1, 0.1],
            "Supported answer 2": [0.2, 0.15, 0.1, 0.1],
        }
    )
    monkeypatch.setattr(
        generation, "start_assessment_process", lambda *_: verifier
    )
    monkeypatch.setattr(
        generation, "_request_stage", lambda *args, **kwargs: next(responses)
    )

    with pytest.raises(
        AssessmentGenerationError, match="^ASSESSMENT_NO_NEW_SAFE_ITEM$"
    ):
        _generate_documents(
            uuid4(),
            _identifier("knowledge-map", "8"),
            _grounded(),
            _settings(),
            "9" * 64,
            frozenset(),
        )
    assert verifier.aborted


def test_grounding_rejects_missing_claim_and_invented_escape():
    concept = _concept()
    policy = _policy()
    with pytest.raises(
        AssessmentGenerationError, match="^ASSESSMENT_GROUNDING_UNAVAILABLE$"
    ):
        _grounding(concept, _identifier("claim", "f"), policy)

    unsafe_claim = ClaimContext(
        claim_id=concept.claims[0].claim_id,
        text=r"The material invents \\gamma.",
        evidence=concept.claims[0].evidence,
    )
    unsafe_concept = FormalConceptContext(
        **{**concept.__dict__, "claims": (unsafe_claim,)}
    )
    with pytest.raises(
        AssessmentGenerationError, match="^ASSESSMENT_INPUT_UNSAFE$"
    ):
        _grounding(unsafe_concept, unsafe_claim.claim_id, policy)
