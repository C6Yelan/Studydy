from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from pdf_evidence.ocr_page_evidence import canonical_sha256
from runtime.learner_session import TrustedLearner
from runtime.storage.database import DatabaseConfigurationError
from runtime.storage.tables import database_session

from .answer_events import (
    AnswerSubmissionError,
    _read_session_answer_events,
)
from .learning_states import (
    ConceptLearningState,
    LearningStateError,
    LearningStateSnapshot,
    _learning_state_snapshot,
)
from .map_context import MapContext, MapContextError
from .study_sessions import (
    StudySessionError,
    _learner_id,
    _read_stored_row,
    _validate_binding,
)


class WeaknessError(RuntimeError):
    """Weakness 或 immediate prerequisite evidence 無法安全推導。"""


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


class ImmediatePrerequisiteGap(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    category: Literal["possible_prerequisite_gap"]
    target_formal_concept_id: str = Field(
        pattern=r"^formal-concept:sha256:[0-9a-f]{64}$"
    )
    prerequisite_formal_concept_id: str = Field(
        pattern=r"^formal-concept:sha256:[0-9a-f]{64}$"
    )
    prerequisite_label: str = Field(min_length=1)
    relation_id: str = Field(
        pattern=r"^formal-relation:sha256:[0-9a-f]{64}$"
    )
    prerequisite_status: Literal[
        "not_started", "learning", "needs_review", "mastered"
    ]
    prerequisite_confidence: Literal["none", "limited", "supported"]
    supporting_answer_event_ids: list[UUID]
    remediation_intent: Literal["relearn_prerequisite"]
    reason: str = Field(min_length=1)


class WeaknessSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_: Literal["weakness/v1"] = Field(alias="schema")
    study_session_id: UUID
    base_knowledge_map_revision: str = Field(
        pattern=r"^knowledge-map:sha256:[0-9a-f]{64}$"
    )
    source_learning_state_revision: str = Field(
        pattern=r"^learning-state:sha256:[0-9a-f]{64}$"
    )
    event_watermark: int = Field(ge=0)
    current_formal_concept_id: str | None
    weakness_revision: str = Field(
        pattern=r"^weakness:sha256:[0-9a-f]{64}$"
    )
    findings: list[WeaknessFinding]
    immediate_prerequisite_gaps: list[ImmediatePrerequisiteGap]


def _error(reason: str) -> WeaknessError:
    return WeaknessError(reason)


def _finding(
    label: str,
    state: ConceptLearningState,
) -> WeaknessFinding | None:
    if state.status == "mastered":
        return None
    if not state.source_answer_event_ids:
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


def _immediate_prerequisite_gaps(
    context: MapContext,
    learning_state: LearningStateSnapshot,
    current_formal_concept_id: str | None,
) -> list[ImmediatePrerequisiteGap]:
    if current_formal_concept_id is None:
        return []
    concepts = {
        concept.formal_concept_id: concept
        for concept in context.formal_concepts
    }
    states = {
        state.formal_concept_id: state
        for state in learning_state.concept_states
    }
    gaps = []
    for relation in context.relations:
        if (
            relation.relation_type != "prerequisite"
            or relation.is_in_prerequisite_cycle
            or relation.target_formal_concept_id
            != current_formal_concept_id
        ):
            continue
        prerequisite = concepts.get(relation.source_formal_concept_id)
        prerequisite_state = states.get(relation.source_formal_concept_id)
        if prerequisite is None or prerequisite_state is None:
            raise _error("WEAKNESS_PREREQUISITE_BINDING_INVALID")
        if prerequisite_state.status == "mastered":
            continue
        gaps.append(
            ImmediatePrerequisiteGap(
                category="possible_prerequisite_gap",
                target_formal_concept_id=current_formal_concept_id,
                prerequisite_formal_concept_id=(
                    prerequisite.formal_concept_id
                ),
                prerequisite_label=prerequisite.label,
                relation_id=relation.relation_id,
                prerequisite_status=prerequisite_state.status,
                prerequisite_confidence=prerequisite_state.confidence,
                supporting_answer_event_ids=(
                    prerequisite_state.source_answer_event_ids
                ),
                remediation_intent="relearn_prerequisite",
                reason=(
                    "目前目標有一個尚未掌握的正式 immediate prerequisite。"
                ),
            )
        )
    return sorted(
        gaps,
        key=lambda gap: (
            gap.prerequisite_formal_concept_id,
            gap.relation_id,
        ),
    )


def _weakness_snapshot(
    study_session_id: UUID,
    current_formal_concept_id: str | None,
    context: MapContext,
    learning_state: LearningStateSnapshot,
) -> WeaknessSnapshot:
    states = {
        state.formal_concept_id: state
        for state in learning_state.concept_states
    }
    findings = []
    for concept in context.formal_concepts:
        state = states.get(concept.formal_concept_id)
        if state is None:
            raise _error("WEAKNESS_STATE_BINDING_INVALID")
        finding = _finding(concept.label, state)
        if finding is not None:
            findings.append(finding)
    gaps = _immediate_prerequisite_gaps(
        context, learning_state, current_formal_concept_id
    )
    identity = {
        "schema": "weakness/v1",
        "study_session_id": str(study_session_id),
        "base_knowledge_map_revision": context.knowledge_map_revision,
        "source_learning_state_revision": learning_state.state_revision,
        "event_watermark": learning_state.event_watermark,
        "current_formal_concept_id": current_formal_concept_id,
        "findings": [finding.model_dump(mode="json") for finding in findings],
        "immediate_prerequisite_gaps": [
            gap.model_dump(mode="json") for gap in gaps
        ],
    }
    return WeaknessSnapshot.model_validate(
        {
            **identity,
            "study_session_id": study_session_id,
            "findings": findings,
            "immediate_prerequisite_gaps": gaps,
            "weakness_revision": (
                "weakness:sha256:" + canonical_sha256(identity)
            ),
        }
    )


def derive_weakness(
    learner: TrustedLearner,
    study_session_id: UUID,
    *,
    dsn: str | None = None,
) -> WeaknessSnapshot:
    """由同 session Learning State 與正式 immediate prerequisite 推導。"""

    if not isinstance(study_session_id, UUID):
        raise _error("WEAKNESS_REQUEST_INVALID")
    try:
        learner_id = _learner_id(learner)
        with database_session(dsn) as session:
            study_session = _read_stored_row(
                session, learner_id, study_session_id, for_update=True
            )
            context = _validate_binding(session, study_session)
            events = _read_session_answer_events(session, study_session)
            learning_state = _learning_state_snapshot(
                study_session_id,
                study_session.knowledge_map_revision,
                study_session.last_event_number,
                context,
                events,
            )
            return _weakness_snapshot(
                study_session_id,
                study_session.current_formal_concept_id,
                context,
                learning_state,
            )
    except WeaknessError:
        raise
    except (
        StudySessionError,
        AnswerSubmissionError,
        LearningStateError,
        MapContextError,
    ):
        raise _error("WEAKNESS_UNAVAILABLE") from None
    except (DatabaseConfigurationError, SQLAlchemyError, TypeError, ValueError):
        raise _error("WEAKNESS_STORAGE_FAILED") from None
