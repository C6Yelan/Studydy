from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from pdf_evidence.ocr_page_evidence import canonical_sha256
from runtime.learner_session import TrustedLearner
from runtime.storage.tables import StudySession, database_session

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


def _concept(context, concept_id: str | None) -> ConceptContext | None:
    return next((concept for concept in context.concepts if concept.concept_id == concept_id), None)


def _first_unmastered_claim(concept: ConceptContext, states: dict[str, ConceptLearningState]) -> str | None:
    state = states[concept.concept_id]
    weak = set(state.weak_claim_ids)
    uncovered = [claim.claim_id for claim in concept.claims if claim.claim_id not in state.covered_claim_ids]
    return next((claim.claim_id for claim in concept.claims if claim.claim_id in weak), None) or (uncovered[0] if uncovered else concept.claims[0].claim_id if concept.claims else None)


def _next_action(context, session: StoredStudySession, states: list[ConceptLearningState]) -> NextAction:
    by_id = {state.concept_id: state for state in states}
    current = _concept(context, session.current_concept_id)
    deferred = set(session.deferred_concept_ids)
    no_safe = set(session.no_safe_claim_ids)
    if current is None:
        target = next((item for item in context.initial_learning_path if by_id[item].status != "mastered"), None)
        return NextAction(action="advance" if target else "complete", target_concept_id=target, target_claim_id=None, prerequisite_concept_ids=[], reason="initial_path" if target else "all_mastered")
    state = by_id[current.concept_id]
    target_claim = _first_unmastered_claim(current, by_id)
    if target_claim in no_safe:
        target = next((item for item in context.initial_learning_path if item != current.concept_id and item not in deferred and by_id[item].status != "mastered"), None)
        return NextAction(action="defer" if target else "no_safe", target_concept_id=target, target_claim_id=target_claim, prerequisite_concept_ids=[], reason="no_safe_assessment")
    if state.status != "mastered":
        unmet = [concept_id for concept_id in current.prerequisite_ids if by_id[concept_id].status != "mastered"]
        if unmet:
            target = unmet[0]
            return NextAction(action="review_prerequisite", target_concept_id=target, target_claim_id=_first_unmastered_claim(_concept(context, target), by_id), prerequisite_concept_ids=unmet, reason="canonical_prerequisite_gap")
        return NextAction(action="assess", target_concept_id=current.concept_id, target_claim_id=target_claim, prerequisite_concept_ids=[], reason="current_concept")
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
) -> LearnerProgressSnapshot:
    weaknesses = [
        WeaknessFinding(concept_id=state.concept_id, claim_ids=state.weak_claim_ids, reason="latest_answer_incorrect")
        for state in states if state.weak_claim_ids
    ]
    action = _next_action(context, session, list(states))
    identity = {
        "study_session_id": str(session.study_session_id),
        "knowledge_structure_revision": session.knowledge_structure_revision,
        "event_watermark": session.last_event_number,
        "current_concept_id": session.current_concept_id,
        "deferred_concept_ids": list(session.deferred_concept_ids),
        "no_safe_claim_ids": list(session.no_safe_claim_ids),
        "concept_states": [state.model_dump() for state in states],
        "next_action": action.model_dump(),
    }
    return LearnerProgressSnapshot(
        schema_="learner-progress/v2",
        study_session_id=session.study_session_id,
        knowledge_structure_revision=session.knowledge_structure_revision,
        event_watermark=session.last_event_number,
        current_concept_id=session.current_concept_id,
        deferred_concept_ids=list(session.deferred_concept_ids),
        concept_states=list(states),
        weaknesses=weaknesses,
        next_action=action,
        guidance_revision="learner-guidance:sha256:" + canonical_sha256(identity),
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
        events = read_answer_events(learner, study_session_id, dsn=dsn)
        if len(events) != session.last_event_number:
            raise LearnerProgressError("LEARNER_PROGRESS_STALE")
        return _snapshot(session, context, derive_learning_states(context, events))
    except LearnerProgressError:
        raise
    except Exception:
        raise LearnerProgressError("LEARNER_PROGRESS_UNAVAILABLE") from None


def _guidance_was_applied(
    learner: TrustedLearner,
    study_session_id: UUID,
    guidance_revision: str,
    *,
    dsn: str | None,
) -> bool:
    try:
        with database_session(dsn) as session:
            row = session.execute(
                select(StudySession.last_applied_guidance_revision).where(
                    StudySession.learner_id == learner.learner_id,
                    StudySession.study_session_id == study_session_id,
                )
            ).one_or_none()
        if row is None:
            raise LearnerProgressError("LEARNER_PROGRESS_UNAVAILABLE")
        return row[0] == guidance_revision
    except LearnerProgressError:
        raise
    except Exception:
        raise LearnerProgressError("LEARNER_PROGRESS_UNAVAILABLE") from None


def apply_guidance(
    learner: TrustedLearner,
    study_session_id: UUID,
    guidance_revision: str,
    *,
    dsn: str | None = None,
) -> LearnerProgressSnapshot:
    if _guidance_was_applied(
        learner, study_session_id, guidance_revision, dsn=dsn
    ):
        return derive_learner_progress(learner, study_session_id, dsn=dsn)
    before = derive_learner_progress(learner, study_session_id, dsn=dsn)
    if guidance_revision != before.guidance_revision:
        if _guidance_was_applied(
            learner, study_session_id, guidance_revision, dsn=dsn
        ):
            return derive_learner_progress(learner, study_session_id, dsn=dsn)
        raise LearnerProgressError("LEARNER_GUIDANCE_STALE")
    state_sha = canonical_sha256({
        "event_watermark": before.event_watermark,
        "current_concept_id": before.current_concept_id,
        "deferred_concept_ids": before.deferred_concept_ids,
    })
    try:
        with database_session(dsn) as session:
            stored = session.scalar(
                select(StudySession).where(
                    StudySession.learner_id == learner.learner_id,
                    StudySession.study_session_id == study_session_id,
                ).with_for_update()
            )
            if stored is None:
                raise LearnerProgressError("LEARNER_PROGRESS_UNAVAILABLE")
            if stored.last_applied_guidance_revision == guidance_revision:
                pass
            elif stored.last_event_number != before.event_watermark or stored.current_concept_id != before.current_concept_id or list(stored.deferred_concept_ids) != before.deferred_concept_ids:
                raise LearnerProgressError("LEARNER_GUIDANCE_STALE")
            else:
                action = before.next_action
                if action.action == "defer":
                    if stored.current_concept_id not in stored.deferred_concept_ids:
                        stored.deferred_concept_ids = [*stored.deferred_concept_ids, stored.current_concept_id]
                    stored.current_concept_id = action.target_concept_id
                    stored.status = "active"
                elif action.action in {"advance", "review_prerequisite", "resume"}:
                    stored.current_concept_id = action.target_concept_id
                    stored.status = "active"
                    if action.action == "resume":
                        stored.deferred_concept_ids = [
                            concept_id for concept_id in stored.deferred_concept_ids
                            if concept_id != action.target_concept_id
                        ]
                elif action.action == "complete":
                    stored.status = "completed"
                    from datetime import UTC, datetime
                    stored.completed_at = datetime.now(UTC)
                stored.last_applied_guidance_revision = guidance_revision
                stored.last_applied_progress_sha256 = state_sha
    except LearnerProgressError:
        raise
    except Exception:
        raise LearnerProgressError("LEARNER_PROGRESS_UNAVAILABLE") from None
    return derive_learner_progress(learner, study_session_id, dsn=dsn)
