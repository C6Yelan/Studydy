from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
import json
import re
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from runtime.learner_session import TrustedLearner
from runtime.storage.database import DatabaseConfigurationError
from runtime.storage.tables import (
    AnswerEvent,
    Assessment,
    StudySession,
    database_session,
)

from .assessment_items import (
    AssessmentError,
    StoredAssessment,
    _stored_assessment,
)
from .map_context import MapContextError
from .study_sessions import (
    StudySessionError,
    _key_digest,
    _learner_id,
    _read_stored_row,
    _stored_session,
    _validate_binding,
)


_ASSESSMENT_ID = re.compile(r"^assessment:sha256:[0-9a-f]{64}$")
_QUESTION_ID = re.compile(r"^question:sha256:[0-9a-f]{64}$")
_OPTION_ID = re.compile(r"^option:sha256:[0-9a-f]{64}$")


class AnswerSubmissionError(RuntimeError):
    """作答 request 或可信事件無法安全處理。"""


class AnswerFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_: str = Field(alias="schema", pattern=r"^answer-feedback/v1$")
    answer_event_id: UUID
    study_session_id: UUID
    assessment_revision: str = Field(
        pattern=r"^assessment:sha256:[0-9a-f]{64}$"
    )
    question_id: str = Field(pattern=r"^question:sha256:[0-9a-f]{64}$")
    selected_option_id: str = Field(pattern=r"^option:sha256:[0-9a-f]{64}$")
    is_correct: bool
    rationale: str = Field(min_length=1)
    source_evidence_ids: list[str] = Field(min_length=1)
    event_number: int = Field(ge=1)
    created_at: datetime


@dataclass(frozen=True)
class StoredAnswerEvent:
    answer_event_id: UUID
    study_session_id: UUID
    material_id: UUID
    knowledge_map_revision: str
    assessment_revision: str
    question_id: str
    target_formal_concept_id: str
    target_claim_id: str
    selected_option_id: str
    is_correct: bool
    event_number: int
    created_at: datetime
    idempotency_key_sha256: bytes = field(repr=False)
    request_fingerprint: bytes = field(repr=False)


@dataclass(frozen=True)
class AnswerSubmission:
    event: StoredAnswerEvent
    feedback: AnswerFeedback


def _error(reason: str) -> AnswerSubmissionError:
    return AnswerSubmissionError(reason)


def _submission_fingerprint(
    study_session_id: UUID,
    assessment_revision: str,
    question_id: str,
    selected_option_id: str,
) -> bytes:
    request = json.dumps(
        {
            "assessment_revision": assessment_revision,
            "question_id": question_id,
            "selected_option_id": selected_option_id,
            "study_session_id": str(study_session_id),
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return sha256(request).digest()


def _validate_request(
    study_session_id: UUID,
    assessment_revision: str,
    question_id: str,
    selected_option_id: str,
) -> None:
    if (
        not isinstance(study_session_id, UUID)
        or not isinstance(assessment_revision, str)
        or _ASSESSMENT_ID.fullmatch(assessment_revision) is None
        or not isinstance(question_id, str)
        or _QUESTION_ID.fullmatch(question_id) is None
        or not isinstance(selected_option_id, str)
        or _OPTION_ID.fullmatch(selected_option_id) is None
    ):
        raise _error("ANSWER_SUBMISSION_INVALID")


def _stored_event(
    row: AnswerEvent,
    study_session: StudySession,
    assessment: StoredAssessment,
) -> StoredAnswerEvent:
    option_ids = {
        option.option_id for option in assessment.public_document.options
    }
    expected_correct = (
        row.selected_option_id
        == assessment.private_answer_document.correct_option_id
    )
    if (
        row.study_session_id != study_session.study_session_id
        or row.material_id != study_session.material_id
        or row.knowledge_map_revision != study_session.knowledge_map_revision
        or row.assessment_revision != assessment.assessment_revision
        or row.question_id != assessment.question_id
        or row.target_formal_concept_id
        != assessment.target_formal_concept_id
        or row.target_claim_id != assessment.target_claim_id
        or row.selected_option_id not in option_ids
        or type(row.is_correct) is not bool
        or row.is_correct != expected_correct
        or type(row.event_number) is not int
        or row.event_number < 1
        or len(bytes(row.idempotency_key_sha256)) != 32
        or len(bytes(row.request_fingerprint)) != 32
    ):
        raise _error("ANSWER_EVENT_UNAVAILABLE")
    return StoredAnswerEvent(
        answer_event_id=row.answer_event_id,
        study_session_id=row.study_session_id,
        material_id=row.material_id,
        knowledge_map_revision=row.knowledge_map_revision,
        assessment_revision=row.assessment_revision,
        question_id=row.question_id,
        target_formal_concept_id=row.target_formal_concept_id,
        target_claim_id=row.target_claim_id,
        selected_option_id=row.selected_option_id,
        is_correct=row.is_correct,
        event_number=row.event_number,
        created_at=row.created_at,
        idempotency_key_sha256=bytes(row.idempotency_key_sha256),
        request_fingerprint=bytes(row.request_fingerprint),
    )


def _feedback(
    event: StoredAnswerEvent,
    assessment: StoredAssessment,
) -> AnswerFeedback:
    return AnswerFeedback.model_validate(
        {
            "schema": "answer-feedback/v1",
            "answer_event_id": event.answer_event_id,
            "study_session_id": event.study_session_id,
            "assessment_revision": event.assessment_revision,
            "question_id": event.question_id,
            "selected_option_id": event.selected_option_id,
            "is_correct": event.is_correct,
            "rationale": assessment.private_answer_document.rationale,
            "source_evidence_ids": (
                assessment.private_answer_document.source_evidence_ids
            ),
            "event_number": event.event_number,
            "created_at": event.created_at,
        }
    )


def _read_bound_assessment(
    session,
    study_session: StudySession,
    assessment_revision: str,
) -> StoredAssessment:
    row = session.scalar(
        select(Assessment).where(
            Assessment.study_session_id == study_session.study_session_id,
            Assessment.assessment_revision == assessment_revision,
        )
    )
    if row is None:
        raise _error("ANSWER_ASSESSMENT_UNAVAILABLE")
    try:
        assessment = _stored_assessment(row)
    except AssessmentError:
        raise _error("ANSWER_ASSESSMENT_UNAVAILABLE") from None
    if assessment.knowledge_map_revision != study_session.knowledge_map_revision:
        raise _error("ANSWER_ASSESSMENT_UNAVAILABLE")
    return assessment


def submit_answer(
    learner: TrustedLearner,
    study_session_id: UUID,
    assessment_revision: str,
    question_id: str,
    selected_option_id: str,
    idempotency_key: str,
    *,
    dsn: str | None = None,
) -> AnswerSubmission:
    """由 private answer deterministic 評分並建立唯一可信 AnswerEvent。"""

    _validate_request(
        study_session_id,
        assessment_revision,
        question_id,
        selected_option_id,
    )
    try:
        learner_id = _learner_id(learner)
        key_digest = _key_digest(idempotency_key)
        fingerprint = _submission_fingerprint(
            study_session_id,
            assessment_revision,
            question_id,
            selected_option_id,
        )
        with database_session(dsn) as session:
            study_session = _read_stored_row(
                session, learner_id, study_session_id, for_update=True
            )
            _validate_binding(session, study_session)
            assessment = _read_bound_assessment(
                session, study_session, assessment_revision
            )

            replay = session.scalar(
                select(AnswerEvent).where(
                    AnswerEvent.study_session_id == study_session_id,
                    AnswerEvent.idempotency_key_sha256 == key_digest,
                )
            )
            if replay is not None:
                if bytes(replay.request_fingerprint) != fingerprint:
                    raise _error("ANSWER_IDEMPOTENCY_CONFLICT")
                event = _stored_event(replay, study_session, assessment)
                return AnswerSubmission(event, _feedback(event, assessment))

            if (
                _stored_session(study_session).status != "active"
                or assessment.question_id != question_id
                or study_session.current_formal_concept_id
                != assessment.target_formal_concept_id
            ):
                raise _error("ANSWER_SUBMISSION_STALE")
            option_ids = {
                option.option_id
                for option in assessment.public_document.options
            }
            if selected_option_id not in option_ids:
                raise _error("ANSWER_OPTION_INVALID")
            duplicate = session.scalar(
                select(AnswerEvent.answer_event_id).where(
                    AnswerEvent.study_session_id == study_session_id,
                    AnswerEvent.assessment_revision == assessment_revision,
                )
            )
            if duplicate is not None:
                raise _error("ANSWER_ALREADY_SUBMITTED")

            study_session.last_event_number += 1
            row = AnswerEvent(
                answer_event_id=uuid4(),
                study_session_id=study_session_id,
                material_id=study_session.material_id,
                knowledge_map_revision=study_session.knowledge_map_revision,
                assessment_revision=assessment.assessment_revision,
                question_id=assessment.question_id,
                target_formal_concept_id=assessment.target_formal_concept_id,
                target_claim_id=assessment.target_claim_id,
                selected_option_id=selected_option_id,
                is_correct=(
                    selected_option_id
                    == assessment.private_answer_document.correct_option_id
                ),
                event_number=study_session.last_event_number,
                idempotency_key_sha256=key_digest,
                request_fingerprint=fingerprint,
                created_at=datetime.now(UTC),
            )
            session.add(row)
            session.flush()
            event = _stored_event(row, study_session, assessment)
            return AnswerSubmission(event, _feedback(event, assessment))
    except AnswerSubmissionError:
        raise
    except (StudySessionError, MapContextError):
        raise _error("ANSWER_STUDY_SESSION_UNAVAILABLE") from None
    except (DatabaseConfigurationError, SQLAlchemyError, TypeError, ValueError):
        raise _error("ANSWER_STORAGE_FAILED") from None


def read_answer_events(
    learner: TrustedLearner,
    study_session_id: UUID,
    *,
    dsn: str | None = None,
) -> tuple[StoredAnswerEvent, ...]:
    """依 server event number 讀取單一 StudySession 的可信作答事件。"""

    if not isinstance(study_session_id, UUID):
        raise _error("ANSWER_SUBMISSION_INVALID")
    try:
        learner_id = _learner_id(learner)
        with database_session(dsn) as session:
            study_session = _read_stored_row(
                session, learner_id, study_session_id
            )
            _validate_binding(session, study_session)
            rows = session.scalars(
                select(AnswerEvent)
                .where(AnswerEvent.study_session_id == study_session_id)
                .order_by(AnswerEvent.event_number)
            )
            events = []
            for row in rows:
                assessment = _read_bound_assessment(
                    session, study_session, row.assessment_revision
                )
                events.append(_stored_event(row, study_session, assessment))
            if len(events) != study_session.last_event_number:
                raise _error("ANSWER_EVENT_UNAVAILABLE")
            return tuple(events)
    except AnswerSubmissionError:
        raise
    except (StudySessionError, MapContextError):
        raise _error("ANSWER_STUDY_SESSION_UNAVAILABLE") from None
    except (DatabaseConfigurationError, SQLAlchemyError, TypeError, ValueError):
        raise _error("ANSWER_STORAGE_FAILED") from None
