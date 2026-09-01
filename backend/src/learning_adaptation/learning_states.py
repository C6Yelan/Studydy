from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from .answer_events import StoredAnswerEvent
from .map_context import FormalConceptContext, MapContext


class LearningStateError(RuntimeError):
    """StudySession 的可信事件無法形成安全 Learning State。"""


class ConceptLearningState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    formal_concept_id: str = Field(
        pattern=r"^formal-concept:sha256:[0-9a-f]{64}$"
    )
    status: Literal["not_started", "learning", "needs_review", "mastered"]
    mastery_band: Literal["no_evidence", "developing", "demonstrated"]
    confidence: Literal["none", "limited", "supported"]
    needs_more_data: bool
    required_claim_ids: list[str]
    attempted_claim_ids: list[str]
    latest_correct_claim_ids: list[str]
    claim_coverage_complete: bool
    required_evidence_ids: list[str]
    observed_evidence_ids: list[str]
    evidence_coverage_complete: bool
    valid_attempts: int = Field(ge=0)
    correct_attempts: int = Field(ge=0)
    qualified_distinct_correct_items: int = Field(ge=0)
    recent_result: Literal["correct", "incorrect"] | None
    repeated_error: bool
    post_error_improvement: bool
    source_answer_event_ids: list[UUID]
    source_event_numbers: list[int]
    reason_code: Literal[
        "NO_ASSESSMENT_EVIDENCE",
        "MORE_ATTEMPTS_REQUIRED",
        "CLAIM_COVERAGE_INCOMPLETE",
        "LATEST_CLAIM_RESULT_INCORRECT",
        "DISTINCT_ITEM_EVIDENCE_REQUIRED",
        "MASTERY_DEMONSTRATED",
    ]
    explanation: str = Field(min_length=1)


def _error(reason: str) -> LearningStateError:
    return LearningStateError(reason)


def _mastery_reason(
    attempts: list[StoredAnswerEvent],
    required_claim_ids: list[str],
    attempted_claim_ids: set[str],
    latest_correct_claim_ids: set[str],
) -> tuple[str, str]:
    if not attempts:
        return (
            "NO_ASSESSMENT_EVIDENCE",
            "尚未有可信作答紀錄，需要先完成評量。",
        )
    if len(attempts) < 2:
        return (
            "MORE_ATTEMPTS_REQUIRED",
            "目前只有一次可信作答，還需要新的題目確認理解是否穩定。",
        )
    if set(required_claim_ids) - attempted_claim_ids:
        return (
            "CLAIM_COVERAGE_INCOMPLETE",
            "尚有必要概念主張未被評量，先補足題目涵蓋範圍。",
        )
    if set(required_claim_ids) - latest_correct_claim_ids:
        return (
            "LATEST_CLAIM_RESULT_INCORRECT",
            "至少一項必要概念主張的最近一次作答不正確，需要複習。",
        )
    if len(required_claim_ids) == 1 and len(
        {
            event.semantic_identity
            for event in attempts
            if event.is_correct
            and event.counts_as_distinct_mastery_evidence
        }
    ) < 2:
        return (
            "DISTINCT_ITEM_EVIDENCE_REQUIRED",
            "單一概念主張需要至少兩道不同題目的正確作答。",
        )
    return (
        "MASTERY_DEMONSTRATED",
        "必要概念主張的涵蓋與最近作答均符合掌握條件。",
    )


def _concept_state(
    concept: FormalConceptContext,
    events: tuple[StoredAnswerEvent, ...],
) -> ConceptLearningState:
    attempts = [
        event
        for event in events
        if event.target_formal_concept_id == concept.formal_concept_id
    ]
    required_claim_ids = [claim.claim_id for claim in concept.claims]
    required_claim_id_set = set(required_claim_ids)
    if any(
        event.target_claim_id not in required_claim_id_set
        for event in attempts
    ):
        raise _error("LEARNING_STATE_EVENT_BINDING_INVALID")
    attempted_claim_ids = {event.target_claim_id for event in attempts}
    latest_by_claim: dict[str, StoredAnswerEvent] = {}
    for event in attempts:
        latest_by_claim[event.target_claim_id] = event
    latest_correct_claim_ids = {
        claim_id
        for claim_id, event in latest_by_claim.items()
        if event.is_correct
    }
    required_evidence_ids = {
        evidence.evidence_id
        for claim in concept.claims
        for evidence in claim.evidence
    }
    observed_evidence_ids = {
        evidence_id
        for event in attempts
        for evidence_id in event.source_evidence_ids
    }
    reason_code, explanation = _mastery_reason(
        attempts,
        required_claim_ids,
        attempted_claim_ids,
        latest_correct_claim_ids,
    )
    is_mastered = reason_code == "MASTERY_DEMONSTRATED"
    wrong_attempts = sum(not event.is_correct for event in attempts)
    post_error_improvement = bool(
        attempts
        and attempts[-1].is_correct
        and any(not event.is_correct for event in attempts[:-1])
    )
    repeated_error = wrong_attempts >= 2
    if is_mastered:
        status = "mastered"
    elif not attempts:
        status = "not_started"
    elif not attempts[-1].is_correct or repeated_error:
        status = "needs_review"
    else:
        status = "learning"
    claim_coverage_complete = not (
        required_claim_id_set - attempted_claim_ids
    )
    needs_more_data = reason_code in {
        "NO_ASSESSMENT_EVIDENCE",
        "MORE_ATTEMPTS_REQUIRED",
        "CLAIM_COVERAGE_INCOMPLETE",
        "DISTINCT_ITEM_EVIDENCE_REQUIRED",
    }
    if not attempts:
        confidence = "none"
    elif needs_more_data:
        confidence = "limited"
    else:
        confidence = "supported"
    return ConceptLearningState(
        formal_concept_id=concept.formal_concept_id,
        status=status,
        mastery_band="demonstrated" if is_mastered else (
            "developing" if attempts else "no_evidence"
        ),
        confidence=confidence,
        needs_more_data=needs_more_data,
        required_claim_ids=required_claim_ids,
        attempted_claim_ids=sorted(attempted_claim_ids),
        latest_correct_claim_ids=sorted(latest_correct_claim_ids),
        claim_coverage_complete=claim_coverage_complete,
        required_evidence_ids=sorted(required_evidence_ids),
        observed_evidence_ids=sorted(observed_evidence_ids),
        evidence_coverage_complete=required_evidence_ids <= observed_evidence_ids,
        valid_attempts=len(attempts),
        correct_attempts=sum(event.is_correct for event in attempts),
        qualified_distinct_correct_items=len(
            {
                event.semantic_identity
                for event in attempts
                if event.is_correct
                and event.counts_as_distinct_mastery_evidence
            }
        ),
        recent_result=(
            None
            if not attempts
            else "correct" if attempts[-1].is_correct else "incorrect"
        ),
        repeated_error=repeated_error,
        post_error_improvement=post_error_improvement,
        source_answer_event_ids=[event.answer_event_id for event in attempts],
        source_event_numbers=[event.event_number for event in attempts],
        reason_code=reason_code,
        explanation=explanation,
    )


def _concept_states(
    context: MapContext,
    events: tuple[StoredAnswerEvent, ...],
) -> list[ConceptLearningState]:
    """依 Map 順序從同一批可信事件直接推導 Concept states。"""

    return [
        _concept_state(concept, events) for concept in context.formal_concepts
    ]
