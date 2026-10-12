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
    completed_claim_ids: list[str]
    weak_claim_ids: list[str]
    unavailable_claim_ids: list[str] = []
    assessable_claim_ids: list[str] = []
    latest_is_correct: bool | None


def claim_completion(events) -> tuple[bool, int]:
    """初答正確即完成；答錯後須連續答對兩道不同題，完成後不撤銷。"""
    correct_questions = set()
    for index, event in enumerate(events):
        if not event.is_correct:
            correct_questions.clear()
        else:
            correct_questions.add(event.semantic_identity)
            if index == 0 or len(correct_questions) >= 2:
                return True, len(correct_questions)
    return False, len(correct_questions)


def derive_learning_states(
    context: MapContext,
    events: tuple[StoredAnswerEvent, ...],
    *,
    unavailable_claim_ids: set[str] | None = None,
    completed_concept_ids: set[str] | None = None,
) -> tuple[ConceptLearningState, ...]:
    """主要狀態僅由完成規則投影；系統無法出題不算使用者答錯。"""
    from .assessment_sets import plan_concept

    unavailable = unavailable_claim_ids or set()
    states = []
    for concept in context.concepts:
        concept_events = [event for event in events if event.target_concept_id == concept.concept_id]
        plan = plan_concept(context, concept.concept_id)
        completed, covered, weak, assessable = set(), set(), set(), set()
        for target in plan['targets']:
            ids = set(target['covered_claim_ids'])
            history = [event for event in concept_events if event.target_claim_id in ids]
            done, _ = claim_completion(history)
            if history:
                covered.update(ids)
            if done:
                completed.update(ids)
            if not ids.intersection(unavailable):
                assessable.update(ids)
                if history and not done:
                    weak.update(ids)
        # 曾完成的重點保留完成事實；目前無題的重點不形成補強 gate。
        if concept.concept_id in (completed_concept_ids or set()) or (assessable and assessable <= completed):
            status = "completed"
            weak.clear()
        elif weak:
            status = "needs_review"
        elif concept_events:
            status = "in_progress"
        else:
            status = "not_started"
        claims = [claim.claim_id for claim in concept.claims]
        states.append(ConceptLearningState(
            concept_id=concept.concept_id,
            label=concept.label,
            status=status,
            attempts=len(concept_events),
            correct_answers=sum(event.is_correct for event in concept_events),
            qualified_correct_items=len({event.semantic_identity for event in concept_events
                                         if event.is_correct and event.mastery_qualified}),
            covered_claim_ids=[claim for claim in claims if claim in covered],
            completed_claim_ids=[claim for claim in claims if claim in completed],
            weak_claim_ids=[claim for claim in claims if claim in weak],
            assessable_claim_ids=[claim for claim in claims if claim in assessable],
            unavailable_claim_ids=[claim for claim in claims if claim not in assessable],
            latest_is_correct=concept_events[-1].is_correct if concept_events else None,
        ))
    return tuple(states)
