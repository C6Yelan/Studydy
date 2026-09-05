from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from .answer_events import StoredAnswerEvent
from .map_context import MapContext


class ConceptLearningState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    concept_id: str
    label: str
    status: str
    attempts: int
    correct_answers: int
    qualified_correct_items: int
    covered_claim_ids: list[str]
    weak_claim_ids: list[str]
    latest_is_correct: bool | None


def derive_learning_states(
    context: MapContext,
    events: tuple[StoredAnswerEvent, ...],
) -> tuple[ConceptLearningState, ...]:
    """Mastery 只由每個 Claim 的兩筆 distinct qualified correct answers 建立。"""

    states = []
    for concept in context.concepts:
        concept_events = [event for event in events if event.target_concept_id == concept.concept_id]
        by_claim = {
            claim.claim_id: [event for event in concept_events if event.target_claim_id == claim.claim_id]
            for claim in concept.claims
        }
        mastered_claims = {
            claim_id
            for claim_id, claim_events in by_claim.items()
            if len({event.semantic_identity for event in claim_events if event.is_correct and event.mastery_qualified}) >= 2
            and claim_events[-1].is_correct
        }
        covered = [claim_id for claim_id, claim_events in by_claim.items() if claim_events]
        weak = [claim_id for claim_id, claim_events in by_claim.items() if claim_events and not claim_events[-1].is_correct]
        latest = concept_events[-1].is_correct if concept_events else None
        if by_claim and mastered_claims == set(by_claim):
            status = "mastered"
        elif weak:
            status = "needs_review"
        elif concept_events:
            status = "learning"
        else:
            status = "not_started"
        states.append(ConceptLearningState(
            concept_id=concept.concept_id,
            label=concept.label,
            status=status,
            attempts=len(concept_events),
            correct_answers=sum(event.is_correct for event in concept_events),
            qualified_correct_items=len({event.semantic_identity for event in concept_events if event.is_correct and event.mastery_qualified}),
            covered_claim_ids=covered,
            weak_claim_ids=weak,
            latest_is_correct=latest,
        ))
    return tuple(states)
