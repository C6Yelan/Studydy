from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import unicodedata
from typing import Any
from uuid import UUID

import httpx

from pdf_evidence.concept_api import (
    ConceptAPIError,
    start_concept_server,
)
from pdf_evidence.local_ai_process import (
    LocalAIError,
    LocalAIProcess,
)
from pdf_evidence.ocr_page_evidence import canonical_sha256
from pdf_evidence.text_first_run import material_analysis_lock
from runtime.learner_session import TrustedLearner

from .assessment_items import (
    GENERATION_PROVENANCE_SCHEMA,
    AssessmentError,
    AssessmentDocuments,
    StoredAssessment,
    build_single_choice_assessment,
    store_assessment,
    used_question_ids,
    validate_assessment_generation_provenance,
)
from .assessment_model_api import request_assessment_text
from .assessment_runtime import (
    AssessmentRuntimeError,
    assessment_runtime_preflight,
    load_assessment_runtime_lock,
)
from .assessment_verifier import start_assessment_process
from .map_context import (
    ClaimContext,
    FormalConceptContext,
    MapContextError,
    read_map_context,
)
from .study_sessions import StudySessionError, read_study_session


_CLAIM_ID = re.compile(r"^claim:sha256:[0-9a-f]{64}$")
_LABEL_ONLY = re.compile(r"[A-Da-d][.)]?")
_ANSWER_CUE = re.compile(
    r"正確答案|答案是|correct answer|all of the above|none of the above|以上皆是|以上皆非",
    re.IGNORECASE,
)
_ALIAS_LEAKAGE = re.compile(
    r"(?<![0-9A-Za-z])e[1-9][0-9]*(?![0-9A-Za-z])|evidence\s+aliases?",
    re.IGNORECASE,
)
_MATERIAL_BOUND = re.compile(
    r"(?:根據|依據).*(?:教材|材料|內容|資訊)|according\s+to.*(?:material|text|information)",
    re.IGNORECASE,
)
_CANDIDATE_COUNT = 3
_PROPOSAL_DISTRACTOR_COUNT = 3
_REPAIR_PROPOSAL_COUNT = 5
_FINAL_OPTION_COUNT = 4


class AssessmentGenerationError(RuntimeError):
    """Assessment generation失敗，只攜帶固定reason code。"""


@dataclass(frozen=True)
class _Grounding:
    concept: FormalConceptContext
    claim: ClaimContext
    aliases: dict[str, tuple[str, str]]


@dataclass(frozen=True)
class _Candidate:
    stage: str
    index: int
    support_aliases: tuple[str, ...]
    prompt: str
    correct_option: str
    distractors: tuple[str, str, str]


@dataclass(frozen=True)
class _ScoredCandidate:
    candidate: _Candidate
    probabilities: tuple[float, float, float, float]
    selected_evidence_probabilities: tuple[float, float, float, float]
    margin: float
    selected_evidence_margin: float
    maximum_distractor: float


def _error(reason: str) -> AssessmentGenerationError:
    return AssessmentGenerationError(reason)


def _normalized(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def _escape_tokens(value: str) -> set[str]:
    return set(re.findall(r"\\[0-9A-Za-z]", value))


def _json_document(model_text: str) -> dict[str, Any]:
    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError
            value[key] = item
        return value

    def reject_constant(_: str) -> None:
        raise ValueError

    try:
        document = json.loads(
            model_text,
            object_pairs_hook=no_duplicates,
            parse_constant=reject_constant,
        )
    except (RecursionError, ValueError):
        raise _error("ASSESSMENT_MODEL_OUTPUT_INVALID") from None
    if not isinstance(document, dict):
        raise _error("ASSESSMENT_MODEL_OUTPUT_INVALID")
    return document


def _response_format(aliases: list[str], *, repair: bool) -> dict[str, Any]:
    distractor: dict[str, Any]
    if repair:
        distractor = {
            "type": "object",
            "additionalProperties": False,
            "required": ["text", "support_id", "changed_from", "changed_to"],
            "properties": {
                "text": {"type": "string", "minLength": 1, "maxLength": 300},
                "support_id": {"enum": aliases},
                "changed_from": {"type": "string", "minLength": 1, "maxLength": 100},
                "changed_to": {"type": "string", "minLength": 1, "maxLength": 100},
            },
        }
    else:
        distractor = {"type": "string", "minLength": 1, "maxLength": 300}
    candidate = {
        "type": "object",
        "additionalProperties": False,
        "required": ["support_ids", "prompt", "correct_option", "distractors"],
        "properties": {
            "support_ids": {
                "type": "array",
                "minItems": 1,
                "maxItems": len(aliases),
                "items": {"enum": aliases},
            },
            "prompt": {"type": "string", "minLength": 1, "maxLength": 400},
            "correct_option": {"type": "string", "minLength": 1, "maxLength": 300},
            "distractors": {
                "type": "array",
                "minItems": (
                    _REPAIR_PROPOSAL_COUNT
                    if repair
                    else _PROPOSAL_DISTRACTOR_COUNT
                ),
                "maxItems": (
                    _REPAIR_PROPOSAL_COUNT
                    if repair
                    else _PROPOSAL_DISTRACTOR_COUNT
                ),
                "items": distractor,
            },
        },
    }
    return {
        "type": "json_schema",
        "json_schema": {
            "name": (
                "assessment_grounded_multi_v2"
                if repair
                else "assessment_grounded_multi"
            ),
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["candidates"],
                "properties": {
                    "candidates": {
                        "type": "array",
                        "minItems": _CANDIDATE_COUNT,
                        "maxItems": _CANDIDATE_COUNT,
                        "items": candidate,
                    }
                },
            },
        },
    }


def _base_candidate(
    candidate: Any,
    aliases: set[str],
) -> tuple[tuple[str, ...], str, str] | None:
    if (
        not isinstance(candidate, dict)
        or set(candidate) != {"support_ids", "prompt", "correct_option", "distractors"}
        or not isinstance(candidate["support_ids"], list)
        or not candidate["support_ids"]
        or len(candidate["support_ids"]) != len(set(candidate["support_ids"]))
        or not set(candidate["support_ids"]) <= aliases
        or not isinstance(candidate["prompt"], str)
        or not candidate["prompt"].strip()
        or len(candidate["prompt"]) > 400
        or not isinstance(candidate["correct_option"], str)
        or not candidate["correct_option"].strip()
        or len(candidate["correct_option"]) > 300
        or _LABEL_ONLY.fullmatch(candidate["correct_option"].strip()) is not None
        or _ANSWER_CUE.search(candidate["prompt"])
        or _ANSWER_CUE.search(candidate["correct_option"])
        or _ALIAS_LEAKAGE.search(candidate["prompt"])
        or _ALIAS_LEAKAGE.search(candidate["correct_option"])
    ):
        return None
    return (
        tuple(candidate["support_ids"]),
        candidate["prompt"],
        candidate["correct_option"],
    )


def _proposal_candidates(
    model_text: str,
    grounding: _Grounding,
) -> list[_Candidate]:
    document = _json_document(model_text)
    raw = document.get("candidates")
    if (
        not isinstance(raw, list)
        or len(raw) != _CANDIDATE_COUNT
        or set(document) != {"candidates"}
    ):
        raise _error("ASSESSMENT_MODEL_OUTPUT_INVALID")
    candidates = []
    aliases = set(grounding.aliases)
    for index, value in enumerate(raw):
        base = _base_candidate(value, aliases)
        distractors = value.get("distractors") if isinstance(value, dict) else None
        if (
            base is None
            or not isinstance(distractors, list)
            or len(distractors) != _PROPOSAL_DISTRACTOR_COUNT
            or any(
                not isinstance(distractor, str)
                or not distractor.strip()
                or len(distractor) > 300
                or _ANSWER_CUE.search(distractor)
                or _ALIAS_LEAKAGE.search(distractor)
                for distractor in distractors
            )
        ):
            continue
        support_ids, prompt, correct = base
        options = [correct, *distractors]
        if len({_normalized(option) for option in options}) != _FINAL_OPTION_COUNT:
            continue
        candidates.append(
            _Candidate(
                stage="proposal",
                index=index,
                support_aliases=support_ids,
                prompt=prompt,
                correct_option=correct,
                distractors=tuple(distractors),
            )
        )
    return candidates


def _proof_is_valid(
    candidate: dict[str, Any],
    distractor: dict[str, Any],
    grounding: _Grounding,
) -> bool:
    support_id = distractor.get("support_id")
    changed_from = distractor.get("changed_from")
    changed_to = distractor.get("changed_to")
    text = distractor.get("text")
    if (
        not isinstance(support_id, str)
        or support_id not in candidate["support_ids"]
        or support_id not in grounding.aliases
        or not all(isinstance(value, str) and value for value in (changed_from, changed_to, text))
    ):
        return False
    source = _normalized(changed_from)
    replacement = _normalized(changed_to)
    correct = _normalized(candidate["correct_option"])
    distractor_text = _normalized(text)
    all_evidence = _normalized(
        "\n".join(item[1] for item in grounding.aliases.values())
    )
    support_text = _normalized(grounding.aliases[support_id][1])
    return (
        bool(source)
        and bool(replacement)
        and source != replacement
        and source in correct
        and source in support_text
        and replacement in distractor_text
        and replacement not in correct
        and replacement not in all_evidence
        and not _ANSWER_CUE.search(text)
        and not _ALIAS_LEAKAGE.search(text)
    )


def _repair_candidates(
    model_text: str,
    grounding: _Grounding,
) -> list[_Candidate]:
    document = _json_document(model_text)
    raw = document.get("candidates")
    if (
        not isinstance(raw, list)
        or len(raw) != _CANDIDATE_COUNT
        or set(document) != {"candidates"}
    ):
        raise _error("ASSESSMENT_MODEL_OUTPUT_INVALID")
    candidates = []
    aliases = set(grounding.aliases)
    for index, value in enumerate(raw):
        base = _base_candidate(value, aliases)
        distractors = value.get("distractors") if isinstance(value, dict) else None
        if (
            base is None
            or not _MATERIAL_BOUND.search(value["prompt"])
            or not isinstance(distractors, list)
            or len(distractors) != _REPAIR_PROPOSAL_COUNT
        ):
            continue
        valid = [
            distractor["text"]
            for distractor in distractors
            if isinstance(distractor, dict)
            and set(distractor) == {"text", "support_id", "changed_from", "changed_to"}
            and _proof_is_valid(value, distractor, grounding)
        ]
        support_ids, prompt, correct = base
        distinct = []
        seen = {_normalized(correct)}
        for distractor in valid:
            key = _normalized(distractor)
            if key and key not in seen:
                seen.add(key)
                distinct.append(distractor)
            if len(distinct) == _PROPOSAL_DISTRACTOR_COUNT:
                break
        if len(distinct) != _PROPOSAL_DISTRACTOR_COUNT:
            continue
        candidates.append(
            _Candidate(
                stage="repair",
                index=index,
                support_aliases=support_ids,
                prompt=prompt,
                correct_option=correct,
                distractors=tuple(distinct),
            )
        )
    return candidates


def _request_stage(
    client: httpx.Client,
    settings: dict[str, Any],
    stage: dict[str, Any],
    request: dict[str, Any],
    response_format: dict[str, Any],
) -> str:
    retry = stage["retry"]
    for attempt in range(1, retry["max_attempts"] + 1):
        try:
            return request_assessment_text(
                client,
                base_url=settings["concept_api_base_url"],
                model=settings["concept_model"],
                prompt_template=stage["prompt"],
                request_document=request,
                response_format=response_format,
                max_model_len=settings["concept_max_model_len"],
                max_tokens=stage["generation"]["max_tokens"],
                timeout_seconds=stage["timeout_seconds"],
            )
        except ConceptAPIError as error:
            if (
                attempt == retry["max_attempts"]
                or error.reason_code not in retry["retryable_reasons"]
            ):
                raise
    raise ConceptAPIError("CONCEPT_API_UNAVAILABLE")


def _verifier_probabilities(
    process: LocalAIProcess,
    premise: str,
    options: list[str],
    request_id: str,
    timeout_seconds: float,
) -> tuple[float, float, float, float]:
    response = process.request(
        {
            "schema": "local-assessment-verifier-request/v1",
            "request_id": request_id,
            "premise": premise,
            "options": options,
        },
        timeout_seconds,
    )
    rejected = {
        "schema": "local-assessment-verifier-response/v2",
        "request_id": request_id,
        "status": "rejected",
        "reason_code": "ASSESSMENT_VERIFIER_INPUT_TOO_LARGE",
    }
    if response == rejected:
        raise _error("ASSESSMENT_VERIFIER_INPUT_TOO_LARGE")
    probabilities = (
        response.get("entailment_probabilities")
        if isinstance(response, dict)
        else None
    )
    if (
        not isinstance(response, dict)
        or set(response)
        != {"schema", "request_id", "status", "entailment_probabilities"}
        or response.get("schema") != "local-assessment-verifier-response/v2"
        or response.get("request_id") != request_id
        or response.get("status") != "scored"
        or not isinstance(probabilities, list)
        or len(probabilities) != _FINAL_OPTION_COUNT
        or any(
            type(probability) not in {int, float}
            or probability < 0
            or probability > 1
            for probability in probabilities
        )
    ):
        raise _error("ASSESSMENT_VERIFIER_RESPONSE_INVALID")
    return tuple(float(probability) for probability in probabilities)


def _score_candidate(
    process: LocalAIProcess,
    candidate: _Candidate,
    grounding: _Grounding,
    timeout_seconds: float,
) -> _ScoredCandidate:
    options = [candidate.correct_option, *candidate.distractors]
    identity = {
        "claim_id": grounding.claim.claim_id,
        "stage": candidate.stage,
        "candidate_index": candidate.index,
        "options": options,
    }
    selected_premise = "\n".join(
        grounding.aliases[alias][1]
        for alias in candidate.support_aliases
    )
    full_premise = "\n".join(
        evidence.text for evidence in grounding.claim.evidence
    )
    selected_values = _verifier_probabilities(
        process,
        selected_premise,
        options,
        canonical_sha256({**identity, "evidence_scope": "selected"}),
        timeout_seconds,
    )
    values = (
        selected_values
        if selected_premise == full_premise
        else _verifier_probabilities(
            process,
            full_premise,
            options,
            canonical_sha256({**identity, "evidence_scope": "full_claim"}),
            timeout_seconds,
        )
    )
    maximum_distractor = max(values[1:])
    selected_maximum_distractor = max(selected_values[1:])
    return _ScoredCandidate(
        candidate=candidate,
        probabilities=values,
        selected_evidence_probabilities=selected_values,
        margin=values[0] - maximum_distractor,
        selected_evidence_margin=(
            selected_values[0] - selected_maximum_distractor
        ),
        maximum_distractor=maximum_distractor,
    )


def _rank_candidates(
    candidates: list[_Candidate],
    process: LocalAIProcess,
    grounding: _Grounding,
    policy: dict[str, Any],
) -> list[_ScoredCandidate]:
    scored = [
        _score_candidate(
            process,
            candidate,
            grounding,
            policy["verifier"]["request_timeout_seconds"],
        )
        for candidate in candidates
    ]
    passing = [
        candidate
        for candidate in scored
        if candidate.margin >= policy["verifier"]["entailment_margin_threshold"]
        and candidate.selected_evidence_margin
        >= policy["verifier"]["entailment_margin_threshold"]
    ]
    return sorted(
        passing,
        key=lambda item: (-item.margin, item.candidate.index),
    )


def _grounding(
    context_concept: FormalConceptContext,
    target_claim_id: str,
    policy: dict[str, Any],
) -> _Grounding:
    claim = next(
        (claim for claim in context_concept.claims if claim.claim_id == target_claim_id),
        None,
    )
    if claim is None or not claim.evidence:
        raise _error("ASSESSMENT_GROUNDING_UNAVAILABLE")
    aliases = {
        f"e{index}": (evidence.evidence_id, evidence.text)
        for index, evidence in enumerate(claim.evidence, start=1)
    }
    evidence_text = "\n".join(evidence.text for evidence in claim.evidence)
    if (
        len(evidence_text) > policy["limits"]["maximum_evidence_characters"]
        or _escape_tokens(claim.text) - _escape_tokens(evidence_text)
    ):
        raise _error("ASSESSMENT_INPUT_UNSAFE")
    return _Grounding(concept=context_concept, claim=claim, aliases=aliases)


def _request_document(
    grounding: _Grounding, *, include_output_language: bool
) -> dict[str, Any]:
    evidence_text = "\n".join(text for _, text in grounding.aliases.values())
    document = {
        "formal_concept": grounding.concept.label,
        "target_claim": grounding.claim.text,
        "evidence": [
            {"id": alias, "text": text}
            for alias, (_, text) in grounding.aliases.items()
        ],
    }
    if include_output_language:
        document = {
            "formal_concept": document["formal_concept"],
            "target_claim": document["target_claim"],
            "output_language": (
                "English"
                if sum(character.isascii() for character in evidence_text)
                / max(1, len(evidence_text))
                > 0.9
                else "Traditional Chinese"
            ),
            "evidence": document["evidence"],
        }
    return document


def _ordered_options(
    selected: _ScoredCandidate,
    target_claim_id: str,
    policy_revision: str,
) -> tuple[list[str], int, list[float], list[float]]:
    values = [
        (
            selected.candidate.correct_option,
            selected.probabilities[0],
            selected.selected_evidence_probabilities[0],
            True,
        ),
        *[
            (
                text,
                selected.probabilities[index],
                selected.selected_evidence_probabilities[index],
                False,
            )
            for index, text in enumerate(selected.candidate.distractors, start=1)
        ],
    ]
    values.sort(
        key=lambda value: canonical_sha256(
            {
                "target_claim_id": target_claim_id,
                "policy_revision": policy_revision,
                "option": value[0],
            }
        )
    )
    correct_index = next(index for index, value in enumerate(values) if value[3])
    return (
        [value[0] for value in values],
        correct_index,
        [value[1] for value in values],
        [value[2] for value in values],
    )


def _provenance(
    documents: AssessmentDocuments,
    selected: _ScoredCandidate,
    ordered_probabilities: list[float],
    ordered_selected_evidence_probabilities: list[float],
    correct_index: int,
    evidence_ids: list[str],
    runtime_binding_sha256: str,
    assessment_lock: dict[str, Any],
    risk_trigger: float,
) -> dict[str, Any]:
    shared_models = assessment_lock["shared_models"]
    verifier = assessment_lock["verifier"]
    value = {
        "schema": GENERATION_PROVENANCE_SCHEMA,
        "assessment_revision": documents.public_document.assessment_revision,
        "question_id": documents.public_document.question_id,
        "generation_policy_revision": assessment_lock["policy_revision"],
        "runtime_binding_sha256": runtime_binding_sha256,
        "model_id": shared_models["semantic_model_id"],
        "model_revision": shared_models["semantic_revision"],
        "proposal_prompt_sha256": assessment_lock["proposal"]["prompt_sha256"],
        "repair_prompt_sha256": assessment_lock["repair"]["prompt_sha256"],
        "verifier_model_id": shared_models["verifier_model_id"],
        "verifier_revision": shared_models["verifier_revision"],
        "selected_stage": selected.candidate.stage,
        "selected_candidate_index": selected.candidate.index,
        "selected_evidence_ids": evidence_ids,
        "option_entailment_probabilities": ordered_probabilities,
        "selected_evidence_option_entailment_probabilities": (
            ordered_selected_evidence_probabilities
        ),
        "correct_option_index": correct_index,
        "entailment_margin_threshold": verifier["entailment_margin_threshold"],
        "multiple_support_risk_threshold": verifier[
            "multiple_support_risk_threshold"
        ],
        "entailment_margin": selected.margin,
        "selected_evidence_entailment_margin": (
            selected.selected_evidence_margin
        ),
        "maximum_distractor_entailment": selected.maximum_distractor,
        "risk_trigger_distractor_entailment": risk_trigger,
        "multiple_support_risk": (
            risk_trigger >= verifier["multiple_support_risk_threshold"]
        ),
        "provenance_sha256": "0" * 64,
    }
    identity = dict(value)
    identity.pop("provenance_sha256")
    value["provenance_sha256"] = canonical_sha256(identity)
    validate_assessment_generation_provenance(value, documents)
    return value


def _generate_documents(
    study_session_id: UUID,
    knowledge_map_revision: str,
    grounding: _Grounding,
    settings: dict[str, Any],
    runtime_binding_sha256: str,
    used_questions: frozenset[str],
) -> tuple[AssessmentDocuments, dict[str, Any]]:
    assessment_lock = settings["assessment_runtime_lock"]
    proposal_request = _request_document(
        grounding, include_output_language=False
    )
    server = verifier = None
    try:
        server = start_concept_server(settings)
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            proposal_text = _request_stage(
                client,
                settings,
                assessment_lock["proposal"],
                proposal_request,
                _response_format(list(grounding.aliases), repair=False),
            )
            proposals = _proposal_candidates(proposal_text, grounding)
            verifier = start_assessment_process(
                settings,
                assessment_lock["verifier"]["startup_timeout_seconds"],
            )
            ranked = _rank_candidates(
                proposals, verifier, grounding, assessment_lock
            )
            choice = None
            repair_attempted = False
            for proposal in ranked:
                proposal_choice = _first_unused_documents(
                    [proposal],
                    study_session_id,
                    knowledge_map_revision,
                    grounding,
                    settings,
                    runtime_binding_sha256,
                    used_questions,
                )
                if proposal_choice is None:
                    continue
                risk_trigger = proposal_choice[3]
                if (
                    risk_trigger
                    < assessment_lock["verifier"][
                        "multiple_support_risk_threshold"
                    ]
                ):
                    choice = proposal_choice
                    break
                if repair_attempted:
                    continue
                repair_attempted = True
                repair_text = _request_stage(
                    client,
                    settings,
                    assessment_lock["repair"],
                    _request_document(
                        grounding, include_output_language=True
                    ),
                    _response_format(list(grounding.aliases), repair=True),
                )
                repairs = _repair_candidates(repair_text, grounding)
                ranked_repairs = _rank_candidates(
                    repairs, verifier, grounding, assessment_lock
                )
                choice = _first_unused_documents(
                    ranked_repairs,
                    study_session_id,
                    knowledge_map_revision,
                    grounding,
                    settings,
                    runtime_binding_sha256,
                    used_questions,
                    risk_trigger=risk_trigger,
                )
                if choice is not None:
                    break
            if choice is None and not repair_attempted:
                repair_text = _request_stage(
                    client,
                    settings,
                    assessment_lock["repair"],
                    _request_document(grounding, include_output_language=True),
                    _response_format(list(grounding.aliases), repair=True),
                )
                ranked_repairs = _rank_candidates(
                    _repair_candidates(repair_text, grounding),
                    verifier,
                    grounding,
                    assessment_lock,
                )
                choice = _first_unused_documents(
                    ranked_repairs,
                    study_session_id,
                    knowledge_map_revision,
                    grounding,
                    settings,
                    runtime_binding_sha256,
                    used_questions,
                )
            if choice is None:
                raise _error("ASSESSMENT_NO_NEW_SAFE_ITEM")
            selected, documents, provenance, _ = choice
            if provenance is None:
                raise _error("ASSESSMENT_NO_SAFE_CANDIDATE")
            verifier.close()
            verifier = None
        server.close()
        server = None
    except AssessmentGenerationError:
        raise
    except ConceptAPIError as error:
        if error.reason_code in {
            "CONCEPT_API_TIMEOUT",
            "CONCEPT_API_UNAVAILABLE",
        }:
            raise _error("ASSESSMENT_MODEL_UNAVAILABLE") from None
        raise _error("ASSESSMENT_MODEL_OUTPUT_INVALID") from None
    except LocalAIError:
        raise _error("ASSESSMENT_VERIFIER_UNAVAILABLE") from None
    finally:
        if verifier is not None:
            verifier.abort()
        if server is not None:
            server.close()

    return documents, provenance


def _build_documents(
    study_session_id: UUID,
    knowledge_map_revision: str,
    grounding: _Grounding,
    selected: _ScoredCandidate,
    settings: dict[str, Any],
    runtime_binding_sha256: str,
    risk_trigger: float,
) -> tuple[AssessmentDocuments, dict[str, Any]]:
    """將已通過semantic gates的candidate deterministic轉成P06-02文件。"""

    (
        documents,
        ordered_probabilities,
        ordered_selected_evidence_probabilities,
        correct_index,
        evidence_ids,
    ) = _candidate_documents(
        study_session_id,
        knowledge_map_revision,
        grounding,
        selected,
        settings,
    )
    assessment_lock = settings["assessment_runtime_lock"]
    provenance = _provenance(
        documents,
        selected,
        ordered_probabilities,
        ordered_selected_evidence_probabilities,
        correct_index,
        evidence_ids,
        runtime_binding_sha256,
        assessment_lock,
        risk_trigger,
    )
    return documents, provenance


def _candidate_documents(
    study_session_id: UUID,
    knowledge_map_revision: str,
    grounding: _Grounding,
    selected: _ScoredCandidate,
    settings: dict[str, Any],
) -> tuple[
    AssessmentDocuments,
    list[float],
    list[float],
    int,
    list[str],
]:
    assessment_lock = settings["assessment_runtime_lock"]
    evidence_ids = [
        grounding.aliases[alias][0]
        for alias in selected.candidate.support_aliases
    ]
    (
        option_texts,
        correct_index,
        ordered_probabilities,
        ordered_selected_evidence_probabilities,
    ) = _ordered_options(
        selected,
        grounding.claim.claim_id,
        assessment_lock["policy_revision"],
    )
    support_text = " / ".join(
        grounding.aliases[alias][1]
        for alias in selected.candidate.support_aliases
    )
    rationale = (
        f"The selected Evidence states: {support_text}"
        if sum(character.isascii() for character in support_text)
        / max(1, len(support_text))
        > 0.9
        else f"選定 Evidence 明確記載：{support_text}"
    )
    documents = build_single_choice_assessment(
        study_session_id=study_session_id,
        knowledge_map_revision=knowledge_map_revision,
        target_formal_concept_id=grounding.concept.formal_concept_id,
        target_claim_id=grounding.claim.claim_id,
        source_evidence_ids=evidence_ids,
        prompt=selected.candidate.prompt,
        option_texts=option_texts,
        correct_option_index=correct_index,
        rationale=rationale,
    )
    return (
        documents,
        ordered_probabilities,
        ordered_selected_evidence_probabilities,
        correct_index,
        evidence_ids,
    )


def _first_unused_documents(
    ranked: list[_ScoredCandidate],
    study_session_id: UUID,
    knowledge_map_revision: str,
    grounding: _Grounding,
    settings: dict[str, Any],
    runtime_binding_sha256: str,
    used_questions: frozenset[str],
    *,
    risk_trigger: float | None = None,
) -> tuple[
    _ScoredCandidate,
    AssessmentDocuments,
    dict[str, Any] | None,
    float,
] | None:
    for selected in ranked:
        trigger = (
            selected.maximum_distractor
            if risk_trigger is None
            else risk_trigger
        )
        (
            documents,
            ordered_probabilities,
            ordered_selected_evidence_probabilities,
            correct_index,
            evidence_ids,
        ) = _candidate_documents(
            study_session_id,
            knowledge_map_revision,
            grounding,
            selected,
            settings,
        )
        if documents.public_document.question_id not in used_questions:
            assessment_lock = settings["assessment_runtime_lock"]
            risky_proposal = (
                selected.candidate.stage == "proposal"
                and trigger
                >= assessment_lock["verifier"][
                    "multiple_support_risk_threshold"
                ]
            )
            provenance = (
                None
                if risky_proposal
                else _provenance(
                    documents,
                    selected,
                    ordered_probabilities,
                    ordered_selected_evidence_probabilities,
                    correct_index,
                    evidence_ids,
                    runtime_binding_sha256,
                    assessment_lock,
                    trigger,
                )
            )
            return selected, documents, provenance, trigger
    return None


def generate_and_store_assessment(
    learner: TrustedLearner,
    study_session_id: UUID,
    target_claim_id: str,
    local_config: dict[str, Any],
    *,
    dsn: str | None = None,
) -> StoredAssessment:
    """使用canonical Evidence生成、驗證並immutable儲存單選Assessment。"""

    if (
        not isinstance(study_session_id, UUID)
        or not isinstance(target_claim_id, str)
        or _CLAIM_ID.fullmatch(target_claim_id) is None
        or not isinstance(local_config, dict)
    ):
        raise _error("ASSESSMENT_GENERATION_REQUEST_INVALID")
    try:
        study_session = read_study_session(
            learner, study_session_id, dsn=dsn
        )
        if (
            study_session.status != "active"
            or study_session.current_formal_concept_id is None
        ):
            raise _error("ASSESSMENT_GROUNDING_UNAVAILABLE")
        context = read_map_context(
            study_session.learner_id,
            study_session.material_id,
            study_session.knowledge_map_revision,
            dsn=dsn,
        )
        concept = next(
            (
                concept
                for concept in context.formal_concepts
                if concept.formal_concept_id
                == study_session.current_formal_concept_id
            ),
            None,
        )
        if concept is None:
            raise _error("ASSESSMENT_GROUNDING_UNAVAILABLE")
        assessment_lock = load_assessment_runtime_lock()
        settings = {**local_config, "assessment_runtime_lock": assessment_lock}
        grounding = _grounding(concept, target_claim_id, assessment_lock)
        used_questions = used_question_ids(
            learner,
            study_session.study_session_id,
            target_claim_id,
            dsn=dsn,
        )
    except AssessmentGenerationError:
        raise
    except (AssessmentError, MapContextError, StudySessionError):
        raise _error("ASSESSMENT_GROUNDING_UNAVAILABLE") from None
    except (KeyError, TypeError):
        raise _error("ASSESSMENT_CONFIGURATION_INVALID") from None

    try:
        runtime_binding = assessment_runtime_preflight(
            local_config, assessment_lock
        )
        with material_analysis_lock(
            Path(local_config["private_runtime_root"])
        ):
            documents, provenance = _generate_documents(
                study_session.study_session_id,
                study_session.knowledge_map_revision,
                grounding,
                settings,
                runtime_binding["runtime_binding_sha256"],
                used_questions,
            )
    except AssessmentGenerationError:
        raise
    except AssessmentRuntimeError:
        raise _error("ASSESSMENT_CONFIGURATION_INVALID") from None
    except (KeyError, TypeError):
        raise _error("ASSESSMENT_CONFIGURATION_INVALID") from None
    except ValueError as error:
        reason = (
            "ASSESSMENT_RUNTIME_BUSY"
            if str(error) == "RUNTIME_BUSY"
            else "ASSESSMENT_CONFIGURATION_INVALID"
        )
        raise _error(reason) from None

    try:
        return store_assessment(
            learner,
            documents.public_document.model_dump(mode="json", by_alias=True),
            documents.private_answer_document.model_dump(
                mode="json", by_alias=True
            ),
            generation_provenance=provenance,
            require_new=True,
            dsn=dsn,
        )
    except AssessmentError as error:
        if str(error) == "ASSESSMENT_NO_NEW_ITEM":
            raise _error("ASSESSMENT_NO_NEW_SAFE_ITEM") from None
        raise _error("ASSESSMENT_STORAGE_FAILED") from None
