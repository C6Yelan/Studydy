from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
import json
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from runtime.storage.tables import (
    AnswerEvent, Assessment, AssessmentSet, AssessmentSetItem,
)

from .assessments import (
    AssessmentError,
    _stored as validate_stored_assessment,
    _provenance,
    _provenance_for_row,
)


class AnswerSubmissionError(RuntimeError):
    pass


class AnswerFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_: str = Field(alias="schema", pattern=r"^answer-feedback/v1$")
    answer_event_id: UUID
    study_session_id: UUID
    assessment_revision: str
    question_id: str
    selected_option_id: str
    correct_option_id: str | None = Field(default=None, exclude_if=lambda value: value is None)
    is_correct: bool
    rationale: str
    source_evidence_ids: list[str]
    event_number: int
    created_at: datetime


@dataclass(frozen=True)
class StoredAnswerEvent:
    answer_event_id: UUID
    study_session_id: UUID
    material_id: UUID
    knowledge_structure_revision: str
    assessment_revision: str
    question_id: str
    semantic_identity: str
    mastery_qualified: bool
    target_concept_id: str
    target_claim_id: str
    source_evidence_ids: tuple[str, ...]
    selected_option_id: str
    is_correct: bool
    event_number: int
    created_at: datetime
    idempotency_key_sha256: bytes = field(repr=False)
    request_fingerprint: bytes = field(repr=False)
    assisted: bool = False


@dataclass(frozen=True)
class AnswerSubmission:
    event: StoredAnswerEvent
    feedback: AnswerFeedback


def _key(value: str) -> bytes:
    if not isinstance(value, str) or not 1 <= len(value.encode()) <= 256:
        raise AnswerSubmissionError("ANSWER_SUBMISSION_INVALID")
    return sha256(value.encode()).digest()


def _fingerprint(
    session_id: UUID, assessment: str, question: str, option: str
) -> bytes:
    return sha256(
        json.dumps(
            {
                "study_session_id": str(session_id),
                "assessment_revision": assessment,
                "question_id": question,
                "selected_option_id": option,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).digest()


def _assessment(session, study, revision: str, provenance_by_set) -> Assessment:
    row = session.scalar(
        select(Assessment).where(
            Assessment.study_session_id == study.study_session_id,
            Assessment.knowledge_structure_revision
            == study.knowledge_structure_revision,
            Assessment.assessment_revision == revision,
        )
    )
    if (
        row is None
        or not isinstance(row.public_document, dict)
        or not isinstance(row.private_answer_document, dict)
    ):
        raise AnswerSubmissionError("ANSWER_ASSESSMENT_UNAVAILABLE")
    try:
        validate_stored_assessment(
            row, _provenance_for_row(session, row, provenance_by_set)
        )
    except AssessmentError:
        raise AnswerSubmissionError("ANSWER_ASSESSMENT_UNAVAILABLE")
    return row


def _assisted_revisions(session, sid):
    return set(
        session.scalars(
            select(AssessmentSetItem.assessment_revision)
            .join(AssessmentSet, AssessmentSet.set_id == AssessmentSetItem.set_id)
            .where(
                AssessmentSet.study_session_id == sid,
                AssessmentSet.kind == "remediation",
                AssessmentSetItem.assessment_revision.is_not(None),
            )
        )
    )


def _event(
    row: AnswerEvent, assessment: Assessment, study, *, assisted=False
) -> StoredAnswerEvent:
    option_ids = {
        option["option_id"] for option in assessment.public_document["options"]
    }
    if (
        row.study_session_id != study.study_session_id
        or row.material_id != study.material_id
        or row.knowledge_structure_revision != study.knowledge_structure_revision
        or row.assessment_revision != assessment.assessment_revision
        or row.question_id != assessment.question_id
        or row.semantic_identity != assessment.semantic_identity
        or row.target_concept_id != assessment.target_concept_id
        or row.target_claim_id != assessment.target_claim_id
        or row.mastery_qualified != assessment.mastery_qualified
        or row.selected_option_id not in option_ids
        or row.is_correct
        != (
            row.selected_option_id
            == assessment.private_answer_document["correct_option_id"]
        )
        or type(row.event_number) is not int
        or row.event_number < 1
        or len(bytes(row.idempotency_key_sha256)) != 32
        or len(bytes(row.request_fingerprint)) != 32
        or bytes(row.request_fingerprint)
        != _fingerprint(
            row.study_session_id,
            row.assessment_revision,
            row.question_id,
            row.selected_option_id,
        )
    ):
        raise AnswerSubmissionError("ANSWER_EVENT_UNAVAILABLE")
    return StoredAnswerEvent(
        row.answer_event_id,
        row.study_session_id,
        row.material_id,
        row.knowledge_structure_revision,
        row.assessment_revision,
        row.question_id,
        row.semantic_identity,
        row.mastery_qualified,
        row.target_concept_id,
        row.target_claim_id,
        tuple(assessment.public_document["source_evidence_ids"]),
        row.selected_option_id,
        row.is_correct,
        row.event_number,
        row.created_at,
        bytes(row.idempotency_key_sha256),
        bytes(row.request_fingerprint),
        assisted,
    )


def _feedback(event: StoredAnswerEvent, assessment: Assessment, *, include_answer_key=False) -> AnswerFeedback:
    return AnswerFeedback.model_validate(
        {
            "schema": "answer-feedback/v1",
            "answer_event_id": event.answer_event_id,
            "study_session_id": event.study_session_id,
            "assessment_revision": event.assessment_revision,
            "question_id": event.question_id,
            "selected_option_id": event.selected_option_id,
            **({"correct_option_id": assessment.private_answer_document["correct_option_id"]} if include_answer_key else {}),
            "is_correct": event.is_correct,
            "rationale": assessment.private_answer_document["rationale"]
            if assessment.generation_provenance["policy"] in {"source-span-single-choice/v2", "source-span-single-choice/v3"}
            else "",
            "source_evidence_ids": assessment.public_document["source_evidence_ids"],
            "event_number": event.event_number,
            "created_at": event.created_at,
        }
    )


def record_answer(session, study, assessment, selected_option_id, idempotency_key):
    """在呼叫者的交易中保存已驗證選項；由整組交卷統一呼叫評分。"""
    from .assessment_sets import record_set_answer

    study_session_id = study.study_session_id
    assessment_revision, question_id = (
        assessment.assessment_revision,
        assessment.question_id,
    )
    key = _key(idempotency_key)
    fingerprint = _fingerprint(
        study_session_id, assessment_revision, question_id, selected_option_id
    )
    from .learning_goals import freeze_goal_day
    freeze_goal_day(session, study.learner_id, study.material_id)
    study.last_event_number += 1
    record_set_answer(session, assessment)
    created = AnswerEvent(
        answer_event_id=uuid4(),
        study_session_id=study_session_id,
        material_id=study.material_id,
        knowledge_structure_revision=study.knowledge_structure_revision,
        assessment_revision=assessment_revision,
        question_id=question_id,
        semantic_identity=assessment.semantic_identity,
        target_concept_id=assessment.target_concept_id,
        target_claim_id=assessment.target_claim_id,
        selected_option_id=selected_option_id,
        is_correct=selected_option_id
        == assessment.private_answer_document["correct_option_id"],
        mastery_qualified=assessment.mastery_qualified,
        event_number=study.last_event_number,
        idempotency_key_sha256=key,
        request_fingerprint=fingerprint,
        created_at=datetime.now(UTC),
    )
    session.add(created)
    session.flush()
    event = _event(
        created,
        assessment,
        study,
        assisted=assessment_revision in _assisted_revisions(session, study_session_id),
    )
    return AnswerSubmission(event, _feedback(event, assessment))


def _read_events(session, study):
    """Study 與教材 scope 已在同一 snapshot 驗證；保留逐筆作答與私有答案檢核。"""
    rows = session.execute(
        select(AnswerEvent, Assessment, AssessmentSet)
        .outerjoin(
            Assessment,
            (Assessment.assessment_revision == AnswerEvent.assessment_revision)
            & (Assessment.study_session_id == study.study_session_id)
            & (
                Assessment.knowledge_structure_revision
                == study.knowledge_structure_revision
            ),
        )
        .outerjoin(
            AssessmentSetItem,
            AssessmentSetItem.assessment_revision == Assessment.assessment_revision,
        )
        .outerjoin(
            AssessmentSet,
            (AssessmentSet.set_id == AssessmentSetItem.set_id)
            & (AssessmentSet.study_session_id == study.study_session_id)
            & (
                AssessmentSet.knowledge_structure_revision
                == study.knowledge_structure_revision
            ),
        )
        .where(AnswerEvent.study_session_id == study.study_session_id)
        .order_by(AnswerEvent.event_number)
    ).all()
    provenance_by_set = {}
    events = []
    for row, assessment, group in rows:
        # 外連接保留缺題目／題組的事件，不能因 join 遺漏而把損壞歷史當作未作答。
        if assessment is None or group is None:
            raise AnswerSubmissionError("ANSWER_ASSESSMENT_UNAVAILABLE")
        try:
            if group.set_id not in provenance_by_set:
                provenance_by_set[group.set_id] = _provenance(group)
            validate_stored_assessment(assessment, provenance_by_set[group.set_id])
        except AssessmentError:
            raise AnswerSubmissionError("ANSWER_ASSESSMENT_UNAVAILABLE") from None
        events.append(
            _event(row, assessment, study, assisted=group.kind == "remediation")
        )
    if [event.event_number for event in events] != list(
        range(1, len(events) + 1)
    ) or len(events) > study.last_event_number:
        raise AnswerSubmissionError("ANSWER_EVENT_UNAVAILABLE")
    return tuple(events)
