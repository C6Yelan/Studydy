from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from pdf_evidence.ocr_page_evidence import canonical_sha256
from runtime.learner_session import TrustedLearner
from runtime.storage.database import DatabaseConfigurationError
from runtime.storage.tables import StudySession, database_session

from .answer_events import (
    AnswerSubmissionError,
    _read_session_answer_events,
)
from .learning_states import (
    LearningStateError,
    LearningStateSnapshot,
    _learning_state_snapshot,
)
from .map_context import FormalConceptContext, MapContext, MapContextError
from .study_sessions import (
    StoredStudySession,
    StudySessionError,
    _learner_id,
    _read_stored_row,
    _stored_session,
    _validate_binding,
)
from .weaknesses import (
    WeaknessError,
    WeaknessSnapshot,
    _weakness_snapshot,
)


AdaptiveAction = Literal[
    "start",
    "continue",
    "practice",
    "review",
    "relearn_prerequisite",
    "use_resource",
    "follow_path",
    "collect_more_data",
    "no_action",
]


class AdaptivePlanError(RuntimeError):
    """Adaptive Plan binding 或套用狀態無法安全處理。"""


class AdaptiveRoute(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    study_session_id: UUID
    formal_concept_id: str | None
    resource_promotion_id: str | None


class AdaptiveStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    action: AdaptiveAction
    target_formal_concept_id: str | None
    target_label: str | None
    reason: str = Field(min_length=1)
    confidence: Literal["none", "limited", "supported"]
    claim_coverage_complete: bool
    supporting_formal_concept_ids: list[str]
    route: AdaptiveRoute


class AdaptivePlanSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_: Literal["adaptive-plan/v1"] = Field(alias="schema")
    study_session_id: UUID
    base_knowledge_map_revision: str = Field(
        pattern=r"^knowledge-map:sha256:[0-9a-f]{64}$"
    )
    inline_initial_learning_path_sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$"
    )
    source_learning_state_revision: str = Field(
        pattern=r"^learning-state:sha256:[0-9a-f]{64}$"
    )
    source_weakness_revision: str = Field(
        pattern=r"^weakness:sha256:[0-9a-f]{64}$"
    )
    event_watermark: int = Field(ge=0)
    current_formal_concept_id: str | None
    deferred_formal_concept_id: str | None
    primary_step: AdaptiveStep
    fallback_reason: Literal[
        "UNMASTERED_IMMEDIATE_PREREQUISITE",
        "CURRENT_OBSERVED_WEAKNESS",
        "CURRENT_NEEDS_REVIEW",
        "RETURN_DEFERRED_TARGET",
        "CURRENT_EVIDENCE_INSUFFICIENT",
        "CANONICAL_PATH_FIRST_NOT_MASTERED",
        "NO_CURRENT_TARGET_FOLLOW_PATH",
        "ALL_CONCEPTS_MASTERED",
    ]
    adaptive_plan_revision: str = Field(
        pattern=r"^adaptive-plan:sha256:[0-9a-f]{64}$"
    )


class Suggestion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_: Literal["learning-suggestion/v1"] = Field(alias="schema")
    adaptive_plan_revision: str = Field(
        pattern=r"^adaptive-plan:sha256:[0-9a-f]{64}$"
    )
    study_session_id: UUID
    base_knowledge_map_revision: str
    action: AdaptiveAction
    target_formal_concept_id: str | None
    target_label: str | None
    reason: str = Field(min_length=1)
    confidence: Literal["none", "limited", "supported"]
    claim_coverage_complete: bool
    route: AdaptiveRoute
    fallback_action: Literal["follow_path", "collect_more_data", "no_action"]
    fallback_reason: str = Field(min_length=1)


@dataclass(frozen=True)
class AppliedAdaptivePlan:
    plan: AdaptivePlanSnapshot
    study_session: StoredStudySession


def _error(reason: str) -> AdaptivePlanError:
    return AdaptivePlanError(reason)


def _concepts(context: MapContext) -> dict[str, FormalConceptContext]:
    return {
        concept.formal_concept_id: concept
        for concept in context.formal_concepts
    }


def _resource_promotion_id(concept: FormalConceptContext) -> str | None:
    if not concept.supplementary_resources:
        return None
    return min(
        resource.promotion_id
        for resource in concept.supplementary_resources
    )


def _step(
    study_session_id: UUID,
    action: AdaptiveAction,
    concept: FormalConceptContext | None,
    reason: str,
    confidence: Literal["none", "limited", "supported"],
    claim_coverage_complete: bool,
    supporting_formal_concept_ids: list[str],
    *,
    include_resource: bool = False,
) -> AdaptiveStep:
    return AdaptiveStep(
        action=action,
        target_formal_concept_id=(
            None if concept is None else concept.formal_concept_id
        ),
        target_label=None if concept is None else concept.label,
        reason=reason,
        confidence=confidence,
        claim_coverage_complete=claim_coverage_complete,
        supporting_formal_concept_ids=supporting_formal_concept_ids,
        route=AdaptiveRoute(
            study_session_id=study_session_id,
            formal_concept_id=(
                None if concept is None else concept.formal_concept_id
            ),
            resource_promotion_id=(
                _resource_promotion_id(concept)
                if include_resource and concept is not None
                else None
            ),
        ),
    )


def _choose_primary_step(
    study_session: StudySession,
    context: MapContext,
    learning_state: LearningStateSnapshot,
    weakness: WeaknessSnapshot,
) -> tuple[AdaptiveStep, str]:
    concepts = _concepts(context)
    states = {
        state.formal_concept_id: state
        for state in learning_state.concept_states
    }
    path_order = {
        concept_id: index
        for index, concept_id in enumerate(context.initial_learning_path)
    }
    if weakness.immediate_prerequisite_gaps:
        gap = min(
            weakness.immediate_prerequisite_gaps,
            key=lambda item: (
                path_order.get(item.prerequisite_formal_concept_id, 10**9),
                item.prerequisite_formal_concept_id,
            ),
        )
        prerequisite = concepts[gap.prerequisite_formal_concept_id]
        prerequisite_state = states[prerequisite.formal_concept_id]
        return (
            _step(
                study_session.study_session_id,
                "relearn_prerequisite",
                prerequisite,
                "先補強尚未掌握、需要先理解的基礎概念，再回到目前目標。",
                prerequisite_state.confidence,
                prerequisite_state.claim_coverage_complete,
                [prerequisite.formal_concept_id],
                include_resource=True,
            ),
            "UNMASTERED_IMMEDIATE_PREREQUISITE",
        )

    current_id = study_session.current_formal_concept_id
    current_finding = next(
        (
            finding
            for finding in weakness.findings
            if finding.target_formal_concept_id == current_id
            and finding.category in {"observed_weak", "needs_review"}
        ),
        None,
    )
    if current_finding is not None:
        current = concepts[current_finding.target_formal_concept_id]
        action = (
            "practice"
            if current_finding.category == "observed_weak"
            else "review"
        )
        return (
            _step(
                study_session.study_session_id,
                action,
                current,
                current_finding.reason,
                current_finding.confidence,
                current_finding.claim_coverage_complete,
                [current.formal_concept_id],
                include_resource=True,
            ),
            (
                "CURRENT_OBSERVED_WEAKNESS"
                if action == "practice"
                else "CURRENT_NEEDS_REVIEW"
            ),
        )

    first_not_mastered_id = next(
        (
            concept_id
            for concept_id in context.initial_learning_path
            if states[concept_id].status != "mastered"
        ),
        None,
    )
    current_state = states.get(current_id) if current_id is not None else None
    deferred_id = study_session.deferred_formal_concept_id
    if (
        deferred_id is not None
        and current_state is not None
        and current_state.status == "mastered"
        and first_not_mastered_id == deferred_id
    ):
        deferred = concepts[deferred_id]
        deferred_state = states[deferred_id]
        return (
            _step(
                study_session.study_session_id,
                "continue",
                deferred,
                "先修概念已掌握，現在回到原本暫緩的學習目標。",
                deferred_state.confidence,
                deferred_state.claim_coverage_complete,
                [current_id, deferred_id],
            ),
            "RETURN_DEFERRED_TARGET",
        )
    if first_not_mastered_id is None:
        return (
            _step(
                study_session.study_session_id,
                "no_action",
                None,
                "本次學習中的所有概念都已符合掌握條件。",
                "supported",
                True,
                list(context.initial_learning_path),
            ),
            "ALL_CONCEPTS_MASTERED",
        )
    target = concepts[first_not_mastered_id]
    target_state = states[first_not_mastered_id]
    if current_id is None:
        return (
            _step(
                study_session.study_session_id,
                "follow_path",
                target,
                "目前沒有指定目標，從原先建議學習順序中的第一個未掌握概念開始。",
                target_state.confidence,
                target_state.claim_coverage_complete,
                [target.formal_concept_id],
            ),
            "NO_CURRENT_TARGET_FOLLOW_PATH",
        )
    if first_not_mastered_id == current_id and target_state.needs_more_data:
        return (
            _step(
                study_session.study_session_id,
                "collect_more_data",
                target,
                "目前證據不足，先完成新的安全題目再判斷是否需要調整路徑。",
                target_state.confidence,
                target_state.claim_coverage_complete,
                [target.formal_concept_id],
            ),
            "CURRENT_EVIDENCE_INSUFFICIENT",
        )
    return (
        _step(
            study_session.study_session_id,
            "start" if target_state.status == "not_started" else "continue",
            target,
            "依原先的建議學習順序，前往第一個尚未掌握的概念。",
            target_state.confidence,
            target_state.claim_coverage_complete,
            [target.formal_concept_id],
        ),
        "CANONICAL_PATH_FIRST_NOT_MASTERED",
    )


def _adaptive_plan_snapshot(
    study_session: StudySession,
    context: MapContext,
    learning_state: LearningStateSnapshot,
    weakness: WeaknessSnapshot,
) -> AdaptivePlanSnapshot:
    primary_step, fallback_reason = _choose_primary_step(
        study_session, context, learning_state, weakness
    )
    path_sha256 = canonical_sha256(
        {
            "knowledge_map_revision": context.knowledge_map_revision,
            "initial_learning_path": list(context.initial_learning_path),
        }
    )
    identity = {
        "schema": "adaptive-plan/v1",
        "study_session_id": str(study_session.study_session_id),
        "base_knowledge_map_revision": context.knowledge_map_revision,
        "inline_initial_learning_path_sha256": path_sha256,
        "source_learning_state_revision": learning_state.state_revision,
        "source_weakness_revision": weakness.weakness_revision,
        "event_watermark": learning_state.event_watermark,
        "current_formal_concept_id": study_session.current_formal_concept_id,
        "deferred_formal_concept_id": study_session.deferred_formal_concept_id,
        "primary_step": primary_step.model_dump(mode="json"),
        "fallback_reason": fallback_reason,
    }
    return AdaptivePlanSnapshot.model_validate(
        {
            **identity,
            "study_session_id": study_session.study_session_id,
            "primary_step": primary_step,
            "adaptive_plan_revision": (
                "adaptive-plan:sha256:" + canonical_sha256(identity)
            ),
        }
    )


def _derive_in_session(
    session: Session,
    study_session: StudySession,
) -> AdaptivePlanSnapshot:
    context = _validate_binding(session, study_session)
    events = _read_session_answer_events(session, study_session)
    learning_state = _learning_state_snapshot(
        study_session.study_session_id,
        study_session.knowledge_map_revision,
        study_session.last_event_number,
        context,
        events,
    )
    weakness = _weakness_snapshot(
        study_session.study_session_id,
        study_session.current_formal_concept_id,
        context,
        learning_state,
    )
    return _adaptive_plan_snapshot(
        study_session, context, learning_state, weakness
    )


def derive_adaptive_plan(
    learner: TrustedLearner,
    study_session_id: UUID,
    *,
    dsn: str | None = None,
) -> AdaptivePlanSnapshot:
    """產生不修改 canonical Map / path 的 one-primary-step overlay。"""

    if not isinstance(study_session_id, UUID):
        raise _error("ADAPTIVE_PLAN_REQUEST_INVALID")
    try:
        learner_id = _learner_id(learner)
        with database_session(dsn) as session:
            study_session = _read_stored_row(
                session, learner_id, study_session_id, for_update=True
            )
            return _derive_in_session(session, study_session)
    except AdaptivePlanError:
        raise
    except (
        StudySessionError,
        AnswerSubmissionError,
        LearningStateError,
        WeaknessError,
        MapContextError,
    ):
        raise _error("ADAPTIVE_PLAN_UNAVAILABLE") from None
    except (DatabaseConfigurationError, SQLAlchemyError, TypeError, ValueError):
        raise _error("ADAPTIVE_PLAN_STORAGE_FAILED") from None


def apply_adaptive_plan(
    learner: TrustedLearner,
    study_session_id: UUID,
    adaptive_plan_revision: str,
    *,
    dsn: str | None = None,
) -> AppliedAdaptivePlan:
    """重新驗證 exact plan revision 後，只套用 server 已決定的 Concept route。"""

    if (
        not isinstance(study_session_id, UUID)
        or not isinstance(adaptive_plan_revision, str)
    ):
        raise _error("ADAPTIVE_PLAN_REQUEST_INVALID")
    try:
        learner_id = _learner_id(learner)
        with database_session(dsn) as session:
            study_session = _read_stored_row(
                session, learner_id, study_session_id, for_update=True
            )
            if study_session.status != "active":
                raise _error("ADAPTIVE_PLAN_STALE")
            plan = _derive_in_session(session, study_session)
            if plan.adaptive_plan_revision != adaptive_plan_revision:
                raise _error("ADAPTIVE_PLAN_STALE")
            target_id = plan.primary_step.target_formal_concept_id
            if plan.primary_step.action == "relearn_prerequisite":
                if study_session.deferred_formal_concept_id is None:
                    study_session.deferred_formal_concept_id = (
                        plan.current_formal_concept_id
                    )
                study_session.current_formal_concept_id = target_id
            elif (
                target_id is not None
                and target_id != study_session.current_formal_concept_id
            ):
                study_session.current_formal_concept_id = target_id
                if target_id == study_session.deferred_formal_concept_id:
                    study_session.deferred_formal_concept_id = None
            session.flush()
            _validate_binding(session, study_session)
            return AppliedAdaptivePlan(plan, _stored_session(study_session))
    except AdaptivePlanError:
        raise
    except (
        StudySessionError,
        AnswerSubmissionError,
        LearningStateError,
        WeaknessError,
        MapContextError,
    ):
        raise _error("ADAPTIVE_PLAN_UNAVAILABLE") from None
    except (DatabaseConfigurationError, SQLAlchemyError, TypeError, ValueError):
        raise _error("ADAPTIVE_PLAN_STORAGE_FAILED") from None


def project_suggestion(plan: AdaptivePlanSnapshot) -> Suggestion:
    """只投影 Adaptive Plan，不重新執行 weakness 或 routing decision。"""

    if not isinstance(plan, AdaptivePlanSnapshot):
        raise _error("ADAPTIVE_PLAN_REQUEST_INVALID")
    if plan.primary_step.action == "no_action":
        fallback_action = "no_action"
        fallback_reason = "目前不需要其他學習動作。"
    elif plan.primary_step.action in {
        "collect_more_data",
        "practice",
        "review",
        "relearn_prerequisite",
    }:
        fallback_action = "collect_more_data"
        fallback_reason = "若目前動作無法完成，先取得更多可信作答證據。"
    else:
        fallback_action = "follow_path"
        fallback_reason = "若目前步驟無法繼續，回到原先的建議學習順序。"
    return Suggestion(
        schema="learning-suggestion/v1",
        adaptive_plan_revision=plan.adaptive_plan_revision,
        study_session_id=plan.study_session_id,
        base_knowledge_map_revision=plan.base_knowledge_map_revision,
        action=plan.primary_step.action,
        target_formal_concept_id=plan.primary_step.target_formal_concept_id,
        target_label=plan.primary_step.target_label,
        reason=plan.primary_step.reason,
        confidence=plan.primary_step.confidence,
        claim_coverage_complete=(
            plan.primary_step.claim_coverage_complete
        ),
        route=plan.primary_step.route,
        fallback_action=fallback_action,
        fallback_reason=fallback_reason,
    )
