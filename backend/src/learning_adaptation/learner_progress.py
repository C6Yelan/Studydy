from __future__ import annotations

from uuid import UUID
from contextlib import contextmanager

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, text

from pdf_evidence.ocr_page_evidence import canonical_sha256
from runtime.learner_session import TrustedLearner
from runtime.storage.tables import Material, database_session

from .answer_events import _read_events
from .learning_states import ConceptLearningState, derive_learning_states
from .map_context import ConceptContext, _context_from_validated_document
from .study_sessions import StoredStudySession, _learner, _row, _stored, _validate_context


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
    uncovered = [
        claim.claim_id for claim in concept.claims
        if claim.claim_id not in state.covered_claim_ids
    ]
    remaining = [
        claim.claim_id for claim in concept.claims
        if claim.claim_id not in state.mastered_claim_ids
    ]
    return next(
        (claim.claim_id for claim in concept.claims if claim.claim_id in weak), None
    ) or (uncovered[0] if uncovered else remaining[0] if remaining else None)


def _next_action(
    context, session: StoredStudySession, states: list[ConceptLearningState], cycles
) -> NextAction:
    by_id = {state.concept_id: state for state in states}
    current = _concept(context, session.current_concept_id)
    deferred = set(session.deferred_concept_ids)
    no_safe = set(session.no_safe_claim_ids)
    if current is None:
        target = next(
            (item for item in context.initial_learning_path if by_id[item].status != "mastered"),
            None,
        )
        return NextAction(
            action="advance" if target else "complete",
            target_concept_id=target,
            target_claim_id=None,
            prerequisite_concept_ids=[],
            reason="initial_path" if target else "all_mastered",
        )
    cycle = next((item for item in cycles if item['concept_id'] == current.concept_id), None)
    if cycle is not None:
        if cycle['active_set_id']:
            return NextAction(
                action='continue_set', target_concept_id=current.concept_id,
                target_claim_id=None, prerequisite_concept_ids=[], reason='active_assessment_set',
            )
        if cycle['outcome'] == 'needs_review':
            return NextAction(
                action='remediate', target_concept_id=current.concept_id,
                target_claim_id=None, prerequisite_concept_ids=[], reason='diagnostic_wrong_points',
            )
        if cycle['outcome'] in ('passed', 'incomplete'):
            finished = {
                item['concept_id'] for item in cycles
                if item['outcome'] in ('passed', 'incomplete') and not item['active_set_id']
            }
            target = next(
                (identity for identity in context.initial_learning_path if identity not in finished),
                None,
            )
            return NextAction(
                action='advance' if target else 'complete', target_concept_id=target,
                target_claim_id=None, prerequisite_concept_ids=[], reason='round_finished',
            )
    state = by_id[current.concept_id]
    target_claim = _first_unmastered_claim(current, by_id)
    if target_claim in no_safe:
        target = next(
            (
                item for item in context.initial_learning_path
                if item != current.concept_id and item not in deferred
                and by_id[item].status != "mastered"
            ),
            None,
        )
        return NextAction(
            action="defer" if target else "no_safe", target_concept_id=target,
            target_claim_id=target_claim, prerequisite_concept_ids=[], reason="no_safe_assessment",
        )
    if state.status != "mastered":
        unmet = [
            concept_id for concept_id in current.prerequisite_ids
            if by_id[concept_id].status != "mastered"
        ]
        return NextAction(
            action="assess", target_concept_id=current.concept_id,
            target_claim_id=target_claim, prerequisite_concept_ids=unmet,
            reason="canonical_prerequisite_gap" if unmet else "current_concept",
        )
    target = next(
        (
            item for item in context.initial_learning_path
            if by_id[item].status != "mastered" and item not in deferred
        ),
        None,
    )
    if target:
        return NextAction(
            action="advance", target_concept_id=target, target_claim_id=None,
            prerequisite_concept_ids=[], reason="initial_path",
        )
    resumed = next(
        (
            item for item in context.initial_learning_path
            if item in deferred and by_id[item].status != "mastered"
        ),
        None,
    )
    if resumed:
        return NextAction(
            action="resume", target_concept_id=resumed, target_claim_id=None,
            prerequisite_concept_ids=[], reason="resume_deferred",
        )
    return NextAction(
        action="complete", target_concept_id=None, target_claim_id=None,
        prerequisite_concept_ids=[], reason="all_mastered",
    )


def _snapshot(
    session: StoredStudySession,
    context,
    states: tuple[ConceptLearningState, ...],
    cycles,
) -> LearnerProgressSnapshot:
    weaknesses = [
        WeaknessFinding(
            concept_id=state.concept_id,
            claim_ids=state.weak_claim_ids,
            reason="latest_answer_incorrect",
        )
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
        schema_="learner-progress/v1",
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


@contextmanager
def progress_snapshot(learner: TrustedLearner, study_session_id: UUID, *, dsn=None):
    """同次讀取共用已驗證教材與一致 DB snapshot，不以重讀全部資料來偵測競態。"""
    from runtime.storage.knowledge_structures import _read_verified_document
    from .assessment_sets import _read_cycles
    from .inherited_progress import inherited_answers
    with database_session(dsn) as db:
        try:
            db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
            row = _row(db, _learner(learner), study_session_id)
            material = db.scalar(select(Material.material_id).where(
                Material.learner_id == learner.learner_id,
                Material.material_id == row.material_id,
                Material.discard_requested_at.is_(None),
            ))
            if material is None:
                raise LearnerProgressError('LEARNER_PROGRESS_UNAVAILABLE')
            document = _read_verified_document(
                db, learner.learner_id, row.material_id,
                revision=row.knowledge_structure_revision,
            )
            context = _context_from_validated_document(row.material_id, document)
            _validate_context(row, context)
            study = _stored(row)
            events = _read_events(db, row)
            if len(events) != study.last_event_number:
                raise LearnerProgressError('LEARNER_PROGRESS_STALE')
            cycles = _read_cycles(db, row)
            inherited = inherited_answers(db, learner, study, document)
            evidence = (
                tuple(sorted(
                    (*inherited, *events),
                    key=lambda event: (event.created_at, str(event.answer_event_id)),
                ))
                if inherited else events
            )
            snapshot = _snapshot(study, context, derive_learning_states(context, evidence), cycles)
        except LearnerProgressError:
            raise
        except Exception:
            raise LearnerProgressError('LEARNER_PROGRESS_UNAVAILABLE') from None
        yield db, study, document, snapshot


def derive_learner_progress(
    learner: TrustedLearner, study_session_id: UUID, *, dsn: str | None = None
) -> LearnerProgressSnapshot:
    with progress_snapshot(learner, study_session_id, dsn=dsn) as (_, _, _, progress):
        return progress


def apply_guidance(
    learner: TrustedLearner, study_session_id: UUID, guidance_revision: str,
    *, dsn: str | None = None,
) -> LearnerProgressSnapshot:
    """只套用目前權威投影的 advance/complete；同 revision 重播不會多前進一步。"""
    from datetime import UTC, datetime
    from .assessment_sets import _scope, has_active_set
    try:
        with database_session(dsn) as session:
            # 與題組建立／提交共用 Material → Study 鎖，阻止核對與套用之間插入作答。
            stored, _, _ = _scope(session, learner, study_session_id, lock=True)
            if stored.last_applied_guidance_revision != guidance_revision:
                before = derive_learner_progress(learner, study_session_id, dsn=dsn)
                action = before.next_action
                if (
                    stored.status not in ('active', 'no_safe')
                    or before.guidance_revision != guidance_revision
                    or action.action not in ('advance', 'complete')
                ):
                    raise LearnerProgressError('LEARNER_GUIDANCE_STALE')
                active_concept = (
                    stored.current_concept_id if action.action == 'advance' else None
                )
                if has_active_set(session, study_session_id, active_concept):
                    raise LearnerProgressError('ASSESSMENT_SET_ACTIVE')
                if action.action == 'advance':
                    if action.target_concept_id is None:
                        raise LearnerProgressError('LEARNER_GUIDANCE_STALE')
                    stored.current_concept_id = action.target_concept_id
                    stored.status = 'active'
                else:
                    stored.status = 'completed'
                    stored.completed_at = datetime.now(UTC)
                stored.last_applied_guidance_revision = guidance_revision
                stored.last_applied_progress_sha256 = canonical_sha256(before.model_dump(mode='json'))
    except LearnerProgressError:
        raise
    except Exception:
        raise LearnerProgressError('LEARNER_PROGRESS_UNAVAILABLE') from None
    return derive_learner_progress(learner, study_session_id, dsn=dsn)
