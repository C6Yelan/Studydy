from __future__ import annotations

from dataclasses import dataclass
import re
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
    "use_resource",
    "follow_path",
    "collect_more_data",
    "defer",
    "resume",
    "no_action",
]

_ADAPTIVE_PLAN_REVISION_PATTERN = re.compile(
    r"^adaptive-plan:sha256:[0-9a-f]{64}$"
)


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
    no_safe_deferred_formal_concept_ids: list[str]
    primary_step: AdaptiveStep
    fallback_reason: Literal[
        "CURRENT_OBSERVED_WEAKNESS",
        "CURRENT_NEEDS_REVIEW",
        "RETURN_DEFERRED_TARGET",
        "CURRENT_EVIDENCE_INSUFFICIENT",
        "CANONICAL_PATH_FIRST_NOT_MASTERED",
        "NO_CURRENT_TARGET_FOLLOW_PATH",
        "ALL_CONCEPTS_MASTERED",
        "NO_SAFE_ADVANCE",
        "NO_SAFE_TARGET_AVAILABLE",
        "RETURN_NO_SAFE_DEFERRED_TARGET",
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


def _study_session_state_sha256(study_session: StudySession) -> str:
    return canonical_sha256(
        {
            "status": study_session.status,
            "current_formal_concept_id": (
                study_session.current_formal_concept_id
            ),
            "deferred_formal_concept_id": (
                study_session.deferred_formal_concept_id
            ),
            "no_safe_claim_ids": list(study_session.no_safe_claim_ids),
            "no_safe_deferred_formal_concept_ids": list(
                study_session.no_safe_deferred_formal_concept_ids
            ),
            "event_watermark": study_session.last_event_number,
        }
    )


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
    current_id = study_session.current_formal_concept_id
    no_safe_claim_ids = set(study_session.no_safe_claim_ids)
    no_safe_concept_ids = {
        concept.formal_concept_id
        for concept in context.formal_concepts
        if {claim.claim_id for claim in concept.claims} <= no_safe_claim_ids
    }
    deferred_no_safe_ids = set(
        study_session.no_safe_deferred_formal_concept_ids
    )

    if current_id in no_safe_concept_ids:
        current_index = context.initial_learning_path.index(current_id)
        next_id = next(
            (
                concept_id
                for concept_id in context.initial_learning_path[
                    current_index + 1 :
                ]
                if states[concept_id].status != "mastered"
                and concept_id not in deferred_no_safe_ids
                and concept_id not in no_safe_concept_ids
            ),
            None,
        )
        if next_id is None:
            resume_id = next(
                (
                    concept_id
                    for concept_id in context.initial_learning_path
                    if concept_id in deferred_no_safe_ids
                ),
                None,
            )
            if resume_id is not None:
                resumed = concepts[resume_id]
                resumed_state = states[resume_id]
                return (
                    _step(
                        study_session.study_session_id,
                        "resume",
                        resumed,
                        "目前重點仍沒有安全題目，回到先前暫緩的教材重點。",
                        resumed_state.confidence,
                        resumed_state.claim_coverage_complete,
                        [current_id, resume_id],
                    ),
                    "RETURN_NO_SAFE_DEFERRED_TARGET",
                )
            return (
                _step(
                    study_session.study_session_id,
                    "no_action",
                    None,
                    "目前沒有其他可安全前往的教材重點。",
                    "none",
                    False,
                    [current_id],
                ),
                "NO_SAFE_TARGET_AVAILABLE",
            )
        target = concepts[next_id]
        target_state = states[next_id]
        return (
            _step(
                study_session.study_session_id,
                "defer",
                target,
                "目前重點沒有可安全提供的題目，先依教材建議順序前往下一個重點。",
                target_state.confidence,
                target_state.claim_coverage_complete,
                [current_id, next_id],
            ),
            "NO_SAFE_ADVANCE",
        )

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
            and concept_id not in deferred_no_safe_ids
        ),
        None,
    )
    current_state = states.get(current_id) if current_id is not None else None
    if current_state is not None and current_state.status == "mastered":
        resume_id = next(
            (
                concept_id
                for concept_id in context.initial_learning_path
                if concept_id in deferred_no_safe_ids
            ),
            None,
        )
        if resume_id is not None:
            resumed = concepts[resume_id]
            resumed_state = states[resume_id]
            return (
                _step(
                    study_session.study_session_id,
                    "resume",
                    resumed,
                    "目前步驟已掌握，回到先前暫緩的教材重點。",
                    resumed_state.confidence,
                    resumed_state.claim_coverage_complete,
                    [current_id, resume_id],
                ),
                "RETURN_NO_SAFE_DEFERRED_TARGET",
            )
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
        blocked_deferred_id = next(
            (
                concept_id
                for concept_id in context.initial_learning_path
                if concept_id in deferred_no_safe_ids
                and states[concept_id].status != "mastered"
            ),
            None,
        )
        if blocked_deferred_id is not None:
            return (
                _step(
                    study_session.study_session_id,
                    "no_action",
                    None,
                    "先前暫緩的教材重點目前無法安全返回。",
                    "supported",
                    False,
                    [current_id, blocked_deferred_id],
                ),
                "NO_SAFE_TARGET_AVAILABLE",
            )
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
        "no_safe_deferred_formal_concept_ids": list(
            study_session.no_safe_deferred_formal_concept_ids
        ),
        "primary_step": primary_step.model_dump(mode="json"),
        "fallback_reason": fallback_reason,
    }
    revision_identity = {
        **identity,
        "no_safe_claim_ids": list(study_session.no_safe_claim_ids),
    }
    return AdaptivePlanSnapshot.model_validate(
        {
            **identity,
            "study_session_id": study_session.study_session_id,
            "primary_step": primary_step,
            "adaptive_plan_revision": (
                "adaptive-plan:sha256:" + canonical_sha256(revision_identity)
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


def record_no_safe_assessment(
    learner: TrustedLearner,
    study_session_id: UUID,
    target_claim_id: str,
    expected_formal_concept_id: str,
    expected_event_number: int,
    *,
    dsn: str | None = None,
) -> StoredStudySession:
    """只記錄 server 已確認無安全題目的 exact-current request。"""

    if (
        not isinstance(study_session_id, UUID)
        or not isinstance(target_claim_id, str)
        or not isinstance(expected_formal_concept_id, str)
        or type(expected_event_number) is not int
        or expected_event_number < 0
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
            context = _validate_binding(session, study_session)
            if (
                study_session.current_formal_concept_id
                != expected_formal_concept_id
                or study_session.last_event_number != expected_event_number
            ):
                raise _error("ADAPTIVE_PLAN_STALE")
            current = next(
                (
                    concept
                    for concept in context.formal_concepts
                    if concept.formal_concept_id
                    == expected_formal_concept_id
                ),
                None,
            )
            if current is None or target_claim_id not in {
                claim.claim_id for claim in current.claims
            }:
                raise _error("ADAPTIVE_PLAN_REQUEST_INVALID")
            study_session.no_safe_claim_ids = sorted(
                {*study_session.no_safe_claim_ids, target_claim_id}
            )
            study_session.last_applied_adaptive_plan_revision = None
            study_session.last_applied_session_state_sha256 = None
            plan = _derive_in_session(session, study_session)
            if plan.fallback_reason == "NO_SAFE_TARGET_AVAILABLE":
                study_session.status = "no_safe"
            session.flush()
            _validate_binding(session, study_session)
            return _stored_session(study_session)
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
        or _ADAPTIVE_PLAN_REVISION_PATTERN.fullmatch(
            adaptive_plan_revision
        ) is None
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
            if (
                study_session.last_applied_adaptive_plan_revision
                == adaptive_plan_revision
            ):
                if study_session.last_applied_session_state_sha256 != (
                    _study_session_state_sha256(study_session)
                ):
                    raise _error("ADAPTIVE_PLAN_STALE")
                replay_plan = _derive_in_session(session, study_session)
                return AppliedAdaptivePlan(
                    replay_plan, _stored_session(study_session)
                )
            plan = _derive_in_session(session, study_session)
            if plan.adaptive_plan_revision != adaptive_plan_revision:
                raise _error("ADAPTIVE_PLAN_STALE")
            target_id = plan.primary_step.target_formal_concept_id
            if plan.primary_step.action == "defer":
                if target_id is None or plan.current_formal_concept_id is None:
                    raise _error("ADAPTIVE_PLAN_UNAVAILABLE")
                deferred_ids = list(
                    study_session.no_safe_deferred_formal_concept_ids
                )
                if plan.current_formal_concept_id not in deferred_ids:
                    deferred_ids.append(plan.current_formal_concept_id)
                study_session.no_safe_deferred_formal_concept_ids = deferred_ids
                study_session.current_formal_concept_id = target_id
            elif plan.primary_step.action == "resume":
                if target_id is None:
                    raise _error("ADAPTIVE_PLAN_UNAVAILABLE")
                study_session.no_safe_deferred_formal_concept_ids = [
                    concept_id
                    for concept_id in (
                        study_session.no_safe_deferred_formal_concept_ids
                    )
                    if concept_id != target_id
                ]
                context = _validate_binding(session, study_session)
                current = next(
                    (
                        concept
                        for concept in context.formal_concepts
                        if concept.formal_concept_id
                        == plan.current_formal_concept_id
                    ),
                    None,
                )
                if current is not None and {
                    claim.claim_id for claim in current.claims
                } <= set(study_session.no_safe_claim_ids):
                    deferred_ids = list(
                        study_session.no_safe_deferred_formal_concept_ids
                    )
                    if current.formal_concept_id not in deferred_ids:
                        deferred_ids.append(current.formal_concept_id)
                    study_session.no_safe_deferred_formal_concept_ids = (
                        deferred_ids
                    )
                target_claim_ids = {
                    claim.claim_id
                    for concept in context.formal_concepts
                    if concept.formal_concept_id == target_id
                    for claim in concept.claims
                }
                study_session.no_safe_claim_ids = [
                    claim_id
                    for claim_id in study_session.no_safe_claim_ids
                    if claim_id not in target_claim_ids
                ]
                study_session.current_formal_concept_id = target_id
            elif (
                target_id is not None
                and target_id != study_session.current_formal_concept_id
            ):
                study_session.current_formal_concept_id = target_id
                if target_id == study_session.deferred_formal_concept_id:
                    study_session.deferred_formal_concept_id = None
            context = _validate_binding(session, study_session)
            deferred_no_safe_ids = set(
                study_session.no_safe_deferred_formal_concept_ids
            )
            study_session.no_safe_deferred_formal_concept_ids = [
                concept_id
                for concept_id in context.initial_learning_path
                if concept_id in deferred_no_safe_ids
            ]
            study_session.last_applied_adaptive_plan_revision = (
                adaptive_plan_revision
            )
            study_session.last_applied_session_state_sha256 = (
                _study_session_state_sha256(study_session)
            )
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
        "defer",
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
