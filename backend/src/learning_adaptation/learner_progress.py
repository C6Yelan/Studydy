from __future__ import annotations

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
    ConceptLearningState,
    LearningStateError,
    _concept_states,
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


GuidanceAction = Literal[
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

_GUIDANCE_REVISION_PATTERN = re.compile(
    r"^learner-guidance:sha256:[0-9a-f]{64}$"
)


class LearnerProgressError(RuntimeError):
    """Learner progress binding 或 guidance 套用狀態無法安全處理。"""


class WeaknessFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    target_formal_concept_id: str = Field(
        pattern=r"^formal-concept:sha256:[0-9a-f]{64}$"
    )
    target_label: str = Field(min_length=1)
    category: Literal[
        "observed_weak", "needs_review", "not_enough_data"
    ]
    confidence: Literal["none", "limited", "supported"]
    claim_coverage_complete: bool
    supporting_answer_event_ids: list[UUID]
    remediation_intent: Literal["practice", "review", "collect_more_data"]
    reason: str = Field(min_length=1)


class GuidanceRoute(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    study_session_id: UUID
    formal_concept_id: str | None
    resource_promotion_id: str | None


class NextAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    action: GuidanceAction
    target_formal_concept_id: str | None
    target_label: str | None
    reason: str = Field(min_length=1)
    confidence: Literal["none", "limited", "supported"]
    claim_coverage_complete: bool
    supporting_formal_concept_ids: list[str]
    route: GuidanceRoute


class LearnerProgressSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_: Literal["learner-progress/v1"] = Field(alias="schema")
    study_session_id: UUID
    material_id: UUID
    base_knowledge_map_revision: str = Field(
        pattern=r"^knowledge-map:sha256:[0-9a-f]{64}$"
    )
    inline_initial_learning_path_sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$"
    )
    event_watermark: int = Field(ge=0)
    status: Literal["active", "completed", "no_safe"]
    current_formal_concept_id: str | None
    no_safe_deferred_formal_concept_ids: list[str]
    concept_states: list[ConceptLearningState] = Field(min_length=1)
    weakness_findings: list[WeaknessFinding]
    next_action: NextAction
    fallback_reason: Literal[
        "CURRENT_OBSERVED_WEAKNESS",
        "CURRENT_NEEDS_REVIEW",
        "CURRENT_EVIDENCE_INSUFFICIENT",
        "CANONICAL_PATH_FIRST_NOT_MASTERED",
        "NO_CURRENT_TARGET_FOLLOW_PATH",
        "ALL_CONCEPTS_MASTERED",
        "NO_SAFE_ADVANCE",
        "NO_SAFE_TARGET_AVAILABLE",
        "RETURN_NO_SAFE_DEFERRED_TARGET",
    ]
    guidance_revision: str = Field(
        pattern=r"^learner-guidance:sha256:[0-9a-f]{64}$"
    )


def _error(reason: str) -> LearnerProgressError:
    return LearnerProgressError(reason)


def _study_session_state_sha256(study_session: StudySession) -> str:
    return canonical_sha256(
        {
            "status": study_session.status,
            "current_formal_concept_id": (
                study_session.current_formal_concept_id
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
    action: GuidanceAction,
    concept: FormalConceptContext | None,
    reason: str,
    confidence: Literal["none", "limited", "supported"],
    claim_coverage_complete: bool,
    supporting_formal_concept_ids: list[str],
    *,
    include_resource: bool = False,
) -> NextAction:
    return NextAction(
        action=action,
        target_formal_concept_id=(
            None if concept is None else concept.formal_concept_id
        ),
        target_label=None if concept is None else concept.label,
        reason=reason,
        confidence=confidence,
        claim_coverage_complete=claim_coverage_complete,
        supporting_formal_concept_ids=supporting_formal_concept_ids,
        route=GuidanceRoute(
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


def _finding(
    label: str,
    state: ConceptLearningState,
) -> WeaknessFinding | None:
    if state.status == "mastered" or not state.source_answer_event_ids:
        return None
    if state.repeated_error:
        category = "observed_weak"
        remediation_intent = "practice"
        reason = "多次可信作答錯誤顯示這個概念目前是明確弱點。"
    elif state.status == "needs_review" or state.post_error_improvement:
        category = "needs_review"
        remediation_intent = "review"
        reason = "近期作答仍不穩定，建議先複習再重新評量。"
    elif state.needs_more_data:
        category = "not_enough_data"
        remediation_intent = "collect_more_data"
        reason = "目前可信作答或必要主張涵蓋不足，尚不能判定弱點。"
    else:
        return None
    return WeaknessFinding(
        target_formal_concept_id=state.formal_concept_id,
        target_label=label,
        category=category,
        confidence=state.confidence,
        claim_coverage_complete=state.claim_coverage_complete,
        supporting_answer_event_ids=state.source_answer_event_ids,
        remediation_intent=remediation_intent,
        reason=reason,
    )


def _weakness_findings(
    context: MapContext,
    concept_states: list[ConceptLearningState],
) -> list[WeaknessFinding]:
    states = {state.formal_concept_id: state for state in concept_states}
    findings = []
    for concept in context.formal_concepts:
        state = states.get(concept.formal_concept_id)
        if state is None:
            raise _error("LEARNER_PROGRESS_STATE_BINDING_INVALID")
        finding = _finding(concept.label, state)
        if finding is not None:
            findings.append(finding)
    return findings


def _choose_next_action(
    study_session: StudySession,
    context: MapContext,
    concept_states: list[ConceptLearningState],
    weakness_findings: list[WeaknessFinding],
) -> tuple[NextAction, str]:
    concepts = _concepts(context)
    states = {
        state.formal_concept_id: state
        for state in concept_states
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
            for finding in weakness_findings
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


def _learner_progress_snapshot(
    study_session: StudySession,
    context: MapContext,
    concept_states: list[ConceptLearningState],
    weakness_findings: list[WeaknessFinding],
) -> LearnerProgressSnapshot:
    next_action, fallback_reason = _choose_next_action(
        study_session, context, concept_states, weakness_findings
    )
    path_sha256 = canonical_sha256(
        {
            "knowledge_map_revision": context.knowledge_map_revision,
            "initial_learning_path": list(context.initial_learning_path),
        }
    )
    identity = {
        "schema": "learner-progress/v1",
        "study_session_id": str(study_session.study_session_id),
        "material_id": str(study_session.material_id),
        "base_knowledge_map_revision": context.knowledge_map_revision,
        "inline_initial_learning_path_sha256": path_sha256,
        "event_watermark": study_session.last_event_number,
        "status": study_session.status,
        "current_formal_concept_id": study_session.current_formal_concept_id,
        "no_safe_deferred_formal_concept_ids": list(
            study_session.no_safe_deferred_formal_concept_ids
        ),
        "concept_states": [
            state.model_dump(mode="json") for state in concept_states
        ],
        "weakness_findings": [
            finding.model_dump(mode="json") for finding in weakness_findings
        ],
        "next_action": next_action.model_dump(mode="json"),
        "fallback_reason": fallback_reason,
    }
    revision_identity = {
        **identity,
        "no_safe_claim_ids": list(study_session.no_safe_claim_ids),
    }
    return LearnerProgressSnapshot.model_validate(
        {
            **identity,
            "study_session_id": study_session.study_session_id,
            "material_id": study_session.material_id,
            "concept_states": concept_states,
            "weakness_findings": weakness_findings,
            "next_action": next_action,
            "guidance_revision": (
                "learner-guidance:sha256:"
                + canonical_sha256(revision_identity)
            ),
        }
    )


def _derive_in_session(
    session: Session,
    study_session: StudySession,
) -> LearnerProgressSnapshot:
    context = _validate_binding(session, study_session)
    events = _read_session_answer_events(session, study_session)
    concept_states = _concept_states(context, events)
    weakness_findings = _weakness_findings(context, concept_states)
    return _learner_progress_snapshot(
        study_session, context, concept_states, weakness_findings
    )


def derive_learner_progress(
    learner: TrustedLearner,
    study_session_id: UUID,
    *,
    dsn: str | None = None,
) -> LearnerProgressSnapshot:
    """在單一 event watermark 產生 progress 與 one-primary-step guidance。"""

    if not isinstance(study_session_id, UUID):
        raise _error("LEARNER_PROGRESS_REQUEST_INVALID")
    try:
        learner_id = _learner_id(learner)
        with database_session(dsn) as session:
            study_session = _read_stored_row(
                session, learner_id, study_session_id, for_update=True
            )
            return _derive_in_session(session, study_session)
    except LearnerProgressError:
        raise
    except (
        StudySessionError,
        AnswerSubmissionError,
        LearningStateError,
        MapContextError,
    ):
        raise _error("LEARNER_PROGRESS_UNAVAILABLE") from None
    except (DatabaseConfigurationError, SQLAlchemyError, TypeError, ValueError):
        raise _error("LEARNER_PROGRESS_STORAGE_FAILED") from None


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
        raise _error("LEARNER_PROGRESS_REQUEST_INVALID")
    try:
        learner_id = _learner_id(learner)
        with database_session(dsn) as session:
            study_session = _read_stored_row(
                session, learner_id, study_session_id, for_update=True
            )
            if study_session.status != "active":
                raise _error("LEARNER_PROGRESS_STALE")
            context = _validate_binding(session, study_session)
            if (
                study_session.current_formal_concept_id
                != expected_formal_concept_id
                or study_session.last_event_number != expected_event_number
            ):
                raise _error("LEARNER_PROGRESS_STALE")
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
                raise _error("LEARNER_PROGRESS_REQUEST_INVALID")
            study_session.no_safe_claim_ids = sorted(
                {*study_session.no_safe_claim_ids, target_claim_id}
            )
            study_session.last_applied_guidance_revision = None
            study_session.last_applied_progress_state_sha256 = None
            progress = _derive_in_session(session, study_session)
            if progress.fallback_reason == "NO_SAFE_TARGET_AVAILABLE":
                study_session.status = "no_safe"
            session.flush()
            _validate_binding(session, study_session)
            return _stored_session(study_session)
    except LearnerProgressError:
        raise
    except (
        StudySessionError,
        AnswerSubmissionError,
        LearningStateError,
        MapContextError,
    ):
        raise _error("LEARNER_PROGRESS_UNAVAILABLE") from None
    except (DatabaseConfigurationError, SQLAlchemyError, TypeError, ValueError):
        raise _error("LEARNER_PROGRESS_STORAGE_FAILED") from None


def apply_guidance(
    learner: TrustedLearner,
    study_session_id: UUID,
    guidance_revision: str,
    *,
    dsn: str | None = None,
) -> LearnerProgressSnapshot:
    """重新驗證 exact guidance 後套用 Concept route，並回傳新 progress。"""

    if (
        not isinstance(study_session_id, UUID)
        or not isinstance(guidance_revision, str)
        or _GUIDANCE_REVISION_PATTERN.fullmatch(
            guidance_revision
        ) is None
    ):
        raise _error("LEARNER_PROGRESS_REQUEST_INVALID")
    try:
        learner_id = _learner_id(learner)
        with database_session(dsn) as session:
            study_session = _read_stored_row(
                session, learner_id, study_session_id, for_update=True
            )
            if study_session.status != "active":
                raise _error("LEARNER_PROGRESS_STALE")
            if (
                study_session.last_applied_guidance_revision
                == guidance_revision
            ):
                if study_session.last_applied_progress_state_sha256 != (
                    _study_session_state_sha256(study_session)
                ):
                    raise _error("LEARNER_PROGRESS_STALE")
                return _derive_in_session(session, study_session)
            progress = _derive_in_session(session, study_session)
            if progress.guidance_revision != guidance_revision:
                raise _error("LEARNER_PROGRESS_STALE")
            target_id = progress.next_action.target_formal_concept_id
            if progress.next_action.action == "defer":
                if target_id is None or progress.current_formal_concept_id is None:
                    raise _error("LEARNER_PROGRESS_UNAVAILABLE")
                deferred_ids = list(
                    study_session.no_safe_deferred_formal_concept_ids
                )
                if progress.current_formal_concept_id not in deferred_ids:
                    deferred_ids.append(progress.current_formal_concept_id)
                study_session.no_safe_deferred_formal_concept_ids = deferred_ids
                study_session.current_formal_concept_id = target_id
            elif progress.next_action.action == "resume":
                if target_id is None:
                    raise _error("LEARNER_PROGRESS_UNAVAILABLE")
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
                        == progress.current_formal_concept_id
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
            context = _validate_binding(session, study_session)
            deferred_no_safe_ids = set(
                study_session.no_safe_deferred_formal_concept_ids
            )
            study_session.no_safe_deferred_formal_concept_ids = [
                concept_id
                for concept_id in context.initial_learning_path
                if concept_id in deferred_no_safe_ids
            ]
            study_session.last_applied_guidance_revision = (
                guidance_revision
            )
            study_session.last_applied_progress_state_sha256 = (
                _study_session_state_sha256(study_session)
            )
            session.flush()
            _validate_binding(session, study_session)
            return _derive_in_session(session, study_session)
    except LearnerProgressError:
        raise
    except (
        StudySessionError,
        AnswerSubmissionError,
        LearningStateError,
        MapContextError,
    ):
        raise _error("LEARNER_PROGRESS_UNAVAILABLE") from None
    except (DatabaseConfigurationError, SQLAlchemyError, TypeError, ValueError):
        raise _error("LEARNER_PROGRESS_STORAGE_FAILED") from None
