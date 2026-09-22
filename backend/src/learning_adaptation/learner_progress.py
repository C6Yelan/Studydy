from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from pdf_evidence.ocr_page_evidence import canonical_sha256
from runtime.learner_session import TrustedLearner
from runtime.storage.tables import StudySession, Material, database_session

from .answer_events import read_answer_events
from .learning_states import ConceptLearningState, derive_learning_states
from .map_context import ConceptContext, read_map_context
from .study_sessions import StoredStudySession, read_study_session


class LearnerProgressError(RuntimeError):
    pass


class WeaknessFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    concept_id: str
    claim_ids: list[str]
    reason: str


class NextAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    action: str
    target_concept_id: str | None
    target_claim_id: str | None
    prerequisite_concept_ids: list[str]
    reason: str


class LearnerProgressSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_: str
    study_session_id: UUID
    knowledge_structure_revision: str
    event_watermark: int
    current_concept_id: str | None
    deferred_concept_ids: list[str]
    concept_states: list[ConceptLearningState]
    weaknesses: list[WeaknessFinding]
    next_action: NextAction
    guidance_revision: str
    assessment_cycles: list[dict]


def _concept(context, concept_id: str | None) -> ConceptContext | None:
    return next((concept for concept in context.concepts if concept.concept_id == concept_id), None)


def _first_unmastered_claim(concept: ConceptContext, states: dict[str, ConceptLearningState]) -> str | None:
    state = states[concept.concept_id]
    weak = set(state.weak_claim_ids)
    uncovered = [claim.claim_id for claim in concept.claims if claim.claim_id not in state.covered_claim_ids]
    remaining = [claim.claim_id for claim in concept.claims if claim.claim_id not in state.mastered_claim_ids]
    return next((claim.claim_id for claim in concept.claims if claim.claim_id in weak), None) or (uncovered[0] if uncovered else remaining[0] if remaining else None)


def _next_action(context, session: StoredStudySession, states: list[ConceptLearningState], cycles) -> NextAction:
    by_id = {state.concept_id: state for state in states}
    current = _concept(context, session.current_concept_id)
    deferred = set(session.deferred_concept_ids)
    no_safe = set(session.no_safe_claim_ids)
    if current is None:
        target = next((item for item in context.initial_learning_path if by_id[item].status != "mastered"), None)
        return NextAction(action="advance" if target else "complete", target_concept_id=target, target_claim_id=None, prerequisite_concept_ids=[], reason="initial_path" if target else "all_mastered")
    cycle = next((item for item in cycles if item['concept_id'] == current.concept_id), None)
    if cycle is not None:
        if cycle['active_set_id']:
            return NextAction(action='continue_set', target_concept_id=current.concept_id,
                target_claim_id=None, prerequisite_concept_ids=[], reason='active_assessment_set')
        if cycle['closed_at'] is None and cycle['pending_count']:
            return NextAction(action='remediate' if cycle['outcome'] == 'ready_for_remediation' else 'review',
                target_concept_id=current.concept_id, target_claim_id=None, prerequisite_concept_ids=[], reason='diagnostic_wrong_points')
        if cycle['closed_at'] is not None:
            finished = {item['concept_id'] for item in cycles if item['closed_at'] is not None}
            target = next((identity for identity in context.initial_learning_path if identity not in finished), None)
            return NextAction(action='advance' if target else 'complete', target_concept_id=target,
                target_claim_id=None, prerequisite_concept_ids=[], reason='round_finished')
    state = by_id[current.concept_id]
    target_claim = _first_unmastered_claim(current, by_id)
    if target_claim in no_safe:
        target = next((item for item in context.initial_learning_path if item != current.concept_id and item not in deferred and by_id[item].status != "mastered"), None)
        return NextAction(action="defer" if target else "no_safe", target_concept_id=target, target_claim_id=target_claim, prerequisite_concept_ids=[], reason="no_safe_assessment")
    if state.status != "mastered":
        unmet = [concept_id for concept_id in current.prerequisite_ids if by_id[concept_id].status != "mastered"]
        return NextAction(action="assess", target_concept_id=current.concept_id, target_claim_id=target_claim,
                          prerequisite_concept_ids=unmet, reason="canonical_prerequisite_gap" if unmet else "current_concept")
    target = next((item for item in context.initial_learning_path if by_id[item].status != "mastered" and item not in deferred), None)
    if target:
        return NextAction(action="advance", target_concept_id=target, target_claim_id=None, prerequisite_concept_ids=[], reason="initial_path")
    resumed = next((item for item in context.initial_learning_path if item in deferred and by_id[item].status != "mastered"), None)
    if resumed:
        return NextAction(action="resume", target_concept_id=resumed, target_claim_id=None, prerequisite_concept_ids=[], reason="resume_deferred")
    return NextAction(action="complete", target_concept_id=None, target_claim_id=None, prerequisite_concept_ids=[], reason="all_mastered")


def _snapshot(
    session: StoredStudySession,
    context,
    states: tuple[ConceptLearningState, ...],
    cycles,
) -> LearnerProgressSnapshot:
    weaknesses = [
        WeaknessFinding(concept_id=state.concept_id, claim_ids=state.weak_claim_ids, reason="latest_answer_incorrect")
        for state in states if state.weak_claim_ids
    ]
    action = _next_action(context, session, list(states), cycles)
    identity = {
        "study_session_id": str(session.study_session_id),
        "knowledge_structure_revision": session.knowledge_structure_revision,
        "event_watermark": session.last_event_number,
        "current_concept_id": session.current_concept_id,
        "deferred_concept_ids": list(session.deferred_concept_ids),
        "no_safe_claim_ids": list(session.no_safe_claim_ids),
        "concept_states": [state.model_dump() for state in states],
        "next_action": action.model_dump(),
        "assessment_cycles": cycles,
        "policy": "diagnostic-remediation/v1",
    }
    return LearnerProgressSnapshot(
        schema_="learner-progress/v3",
        study_session_id=session.study_session_id,
        knowledge_structure_revision=session.knowledge_structure_revision,
        event_watermark=session.last_event_number,
        current_concept_id=session.current_concept_id,
        deferred_concept_ids=list(session.deferred_concept_ids),
        concept_states=list(states),
        weaknesses=weaknesses,
        next_action=action,
        guidance_revision="learner-guidance:sha256:" + canonical_sha256(identity),
        assessment_cycles=cycles,
    )


def derive_learner_progress(
    learner: TrustedLearner,
    study_session_id: UUID,
    *,
    dsn: str | None = None,
) -> LearnerProgressSnapshot:
    try:
        session = read_study_session(learner, study_session_id, dsn=dsn)
        context = read_map_context(
            learner.learner_id,
            session.material_id,
            session.knowledge_structure_revision,
            dsn=dsn,
        )
        from .assessment_sets import read_cycles
        cycles = read_cycles(learner, study_session_id, dsn=dsn)
        events = read_answer_events(learner, study_session_id, dsn=dsn)
        if len(events) != session.last_event_number:
            raise LearnerProgressError("LEARNER_PROGRESS_STALE")
        from .inherited_progress import inherited_answers
        inherited = inherited_answers(learner, session, dsn=dsn)
        # 未跨版本時保留原有 event_number 順序；只有跨 session 的證據需要合併時間序。
        evidence = tuple(sorted((*inherited, *events), key=lambda event: (event.created_at, str(event.answer_event_id)))) if inherited else events
        if cycles != read_cycles(learner, study_session_id, dsn=dsn):
            raise LearnerProgressError('LEARNER_PROGRESS_STALE')
        # cycle timestamps must be canonical JSON for the guidance digest.
        cycles = [{key: value.isoformat() if hasattr(value, 'isoformat') else value for key, value in item.items()} for item in cycles]
        return _snapshot(session, context, derive_learning_states(context, evidence), cycles)
    except LearnerProgressError:
        raise
    except Exception:
        raise LearnerProgressError("LEARNER_PROGRESS_UNAVAILABLE") from None
