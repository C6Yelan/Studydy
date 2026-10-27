from __future__ import annotations

from uuid import UUID
from contextlib import contextmanager

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, text

from content_identity import canonical_sha256
from runtime.learner_session import TrustedLearner
from runtime.storage.tables import Material
from runtime.storage.database import database_session

from .answer_events import _read_events
from .learning_states import ConceptLearningState, derive_learning_states
from .study_sessions import StoredStudySession, _learner, _row, _stored, _validate_plan


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
    learning_path: list[str] = []


def _concept(context, concept_id: str | None):
    return next(
        (
            concept
            for concept in context["concepts"]
            if concept["concept_id"] == concept_id
        ),
        None,
    )


def _first_incomplete_claim(
    concept: dict, states: dict[str, ConceptLearningState]
) -> str | None:
    state = states[concept["concept_id"]]
    weak = set(state.weak_claim_ids)
    uncovered = [
        claim
        for claim in concept["claim_ids"]
        if claim in state.assessable_claim_ids and claim not in state.covered_claim_ids
    ]
    remaining = [
        claim
        for claim in concept["claim_ids"]
        if claim in state.assessable_claim_ids
        and claim not in state.completed_claim_ids
    ]
    return next((claim for claim in concept["claim_ids"] if claim in weak), None) or (
        uncovered[0] if uncovered else remaining[0] if remaining else None
    )


def _next_action(
    context, session: StoredStudySession, states: list[ConceptLearningState], cycles, *, learning_path=None
) -> NextAction:
    by_id = {state.concept_id: state for state in states}
    current = _concept(context, session.current_concept_id)
    by_cycle = {cycle["concept_id"]: cycle for cycle in cycles}
    path = context["initial_learning_path"] if learning_path is None else learning_path

    def remaining(identity):
        state = by_id[identity]
        return state.status != "completed" and bool(state.assessable_claim_ids)

    if current is not None:
        state = by_id[current["concept_id"]]
        cycle = by_cycle.get(current["concept_id"])
        if cycle and cycle["active_set_id"]:
            return NextAction(
                action="continue_set",
                target_concept_id=current["concept_id"],
                target_claim_id=None,
                prerequisite_concept_ids=[],
                reason="active_assessment_set",
            )
        if remaining(current["concept_id"]):
            unmet = [
                identity
                for identity in current["prerequisite_ids"]
                if by_id[identity].status != "completed"
            ]
            remediation = bool(cycle and cycle["outcome"] == "needs_review")
            return NextAction(
                action="remediate" if remediation else "assess",
                target_concept_id=current["concept_id"],
                target_claim_id=_first_incomplete_claim(current, by_id),
                prerequisite_concept_ids=[] if remediation else unmet,
                reason="incomplete_claims"
                if remediation
                else "canonical_prerequisite_gap"
                if unmet
                else "current_concept",
            )
    target = next(
        (
            identity
            for identity in path
            if remaining(identity)
        ),
        None,
    )
    return NextAction(
        action="advance" if target else "complete",
        target_concept_id=target,
        target_claim_id=None,
        prerequisite_concept_ids=[],
        reason="initial_path" if target else "assessable_concepts_finished",
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
            reason="remediation_incomplete"
            if state.latest_is_correct
            else "latest_answer_incorrect",
        )
        for state in states
        if state.weak_claim_ids
    ]
    by_id = {state.concept_id: state for state in states}
    path = [identity for identity in context["initial_learning_path"]
            if by_id[identity].status != "completed" and by_id[identity].assessable_claim_ids]
    action = _next_action(context, session, list(states), cycles, learning_path=path)
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
        "policy": "claim-completion/v2",
        "learning_path": path,
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
        assessment_cycles=cycles,
        learning_path=path,
    )


@contextmanager
def progress_snapshot(learner: TrustedLearner, study_session_id: UUID, *, dsn=None):
    """同次讀取共用已驗證教材與一致 DB snapshot，不以重讀全部資料來偵測競態。"""
    from runtime.storage.knowledge_reads import learning
    from .assessment_state import _read_cycles, unavailable_claims, completed_concepts
    from .inherited_progress import inherited_progress

    with database_session(dsn) as db:
        try:
            db.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            )
            db.info["read_snapshot"] = {"documents": {}, "inherited": {}, "cycles": {}}
            row = _row(db, _learner(learner), study_session_id)
            material = db.execute(
                select(Material.material_id).where(
                    Material.learner_id == learner.learner_id,
                    Material.material_id == row.material_id,
                    Material.discard_requested_at.is_(None),
                )
            ).one_or_none()
            if material is None:
                raise LearnerProgressError("LEARNER_PROGRESS_UNAVAILABLE")
            context = learning(
                db,
                learner.learner_id,
                row.material_id,
                row.knowledge_structure_revision,
            )
            _validate_plan(row, context)
            study = _stored(row)
            events = _read_events(db, row)
            if len(events) != study.last_event_number:
                raise LearnerProgressError("LEARNER_PROGRESS_STALE")
            inherited, inherited_cycles = inherited_progress(
                db, learner, study.material_id, study.knowledge_structure_revision, context
            )
            cycles = _read_cycles(db, row)
            evidence = (
                tuple(
                    sorted(
                        (*inherited, *events),
                        key=lambda event: (
                            event.created_at,
                            str(event.answer_event_id),
                        ),
                    )
                )
                if inherited
                else events
            )
            states = derive_learning_states(
                context,
                evidence,
                unavailable_claim_ids=unavailable_claims(db, row, context),
                completed_concept_ids=completed_concepts(db, row)
                | {cycle["concept_id"] for cycle in inherited_cycles},
            )
            current_cycles = {cycle["concept_id"] for cycle in cycles}
            weak = {state.concept_id for state in states if state.weak_claim_ids}
            cycles += [
                cycle
                for cycle in inherited_cycles
                if cycle["concept_id"] not in current_cycles | weak
            ]
            snapshot = _snapshot(study, context, states, cycles)
        except LearnerProgressError:
            raise
        except Exception:
            raise LearnerProgressError("LEARNER_PROGRESS_UNAVAILABLE") from None
        yield db, study, context, snapshot


def derive_learner_progress(
    learner: TrustedLearner, study_session_id: UUID, *, dsn: str | None = None
) -> LearnerProgressSnapshot:
    with progress_snapshot(learner, study_session_id, dsn=dsn) as (_, _, _, progress):
        return progress


def apply_guidance(
    learner: TrustedLearner,
    study_session_id: UUID,
    guidance_revision: str,
    *,
    dsn: str | None = None,
) -> LearnerProgressSnapshot:
    """只套用目前權威投影的 advance/complete；同 revision 重播不會多前進一步。"""
    from datetime import UTC, datetime
    from .assessment_state import _scope, has_active_set

    try:
        with database_session(dsn) as session:
            # 與題組建立／提交共用 Material → Study 鎖，阻止核對與套用之間插入作答。
            stored, _, _ = _scope(session, learner, study_session_id, lock=True)
            if stored.last_applied_guidance_revision != guidance_revision:
                before = derive_learner_progress(learner, study_session_id, dsn=dsn)
                action = before.next_action
                if (
                    stored.status not in ("active", "no_safe")
                    or before.guidance_revision != guidance_revision
                    or action.action not in ("advance", "complete")
                ):
                    raise LearnerProgressError("LEARNER_GUIDANCE_STALE")
                active_concept = (
                    stored.current_concept_id if action.action == "advance" else None
                )
                if has_active_set(session, study_session_id, active_concept):
                    raise LearnerProgressError("ASSESSMENT_SET_ACTIVE")
                if action.action == "advance":
                    if action.target_concept_id is None:
                        raise LearnerProgressError("LEARNER_GUIDANCE_STALE")
                    stored.current_concept_id = action.target_concept_id
                    stored.status = "active"
                else:
                    stored.status = "completed"
                    stored.completed_at = datetime.now(UTC)
                stored.last_applied_guidance_revision = guidance_revision
                stored.last_applied_progress_sha256 = canonical_sha256(
                    before.model_dump(mode="json")
                )
    except LearnerProgressError:
        raise
    except Exception:
        raise LearnerProgressError("LEARNER_PROGRESS_UNAVAILABLE") from None
    return derive_learner_progress(learner, study_session_id, dsn=dsn)
