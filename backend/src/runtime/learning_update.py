from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
import json
import re
from typing import Any, Sequence
from uuid import UUID, uuid4

from sqlalchemy import func, select

from learning_state.assessment import score_submission
from learning_state.learning_state import build_learning_state

from .storage.domain_revisions import DomainRevisionError, read_assessment_for_scoring
from .storage.tables import (
    AnswerEvent as AnswerEventRow,
    Assessment,
    LearningState as LearningStateRow,
    Material,
    database_session,
)

_REVISION = re.compile(r"^[a-z-]+:sha256:[0-9a-f]{64}$")
_EVENT_LIMIT = 1_000
_RESPONSE_LIMIT = 200


class LearningUpdateError(RuntimeError):
    """Learning update 失敗且不揭露答案、教材或資料庫細節。"""


@dataclass(frozen=True)
class AssessmentResponse:
    question_id: str
    selected_option_id: str


@dataclass(frozen=True)
class LearningStateRecord:
    learner_id: UUID
    material_id: UUID
    state_revision: str
    map_revision: str
    path_revision: str
    assessment_revision: str
    submission_id: UUID
    replayed: bool
    document: dict[str, Any] = field(repr=False)


def _canonical(value: Any) -> bytes:
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False).encode("utf-8")
    except (RecursionError, TypeError, ValueError):
        raise LearningUpdateError("LEARNING_UPDATE_INVALID") from None
    return sha256(encoded).digest()


def _key_digest(value: Any) -> bytes:
    try:
        encoded = value.encode("utf-8")
    except (AttributeError, UnicodeError):
        raise LearningUpdateError("LEARNING_UPDATE_INVALID") from None
    if not 1 <= len(encoded) <= 256:
        raise LearningUpdateError("LEARNING_UPDATE_INVALID")
    return sha256(encoded).digest()


def _responses(value: Any) -> list[AssessmentResponse]:
    if isinstance(value, (str, bytes)):
        raise LearningUpdateError("LEARNING_UPDATE_INVALID")
    try:
        items = list(value)
    except Exception:
        raise LearningUpdateError("LEARNING_UPDATE_INVALID") from None
    if not 1 <= len(items) <= _RESPONSE_LIMIT or any(not isinstance(item, AssessmentResponse) for item in items):
        raise LearningUpdateError("LEARNING_UPDATE_INVALID")
    normalized = sorted(items, key=lambda item: item.question_id)
    if (
        len({item.question_id for item in normalized}) != len(normalized)
        or any(not item.question_id or not item.selected_option_id for item in normalized)
    ):
        raise LearningUpdateError("LEARNING_UPDATE_INVALID")
    return normalized


def _record(row: Sequence[Any], replayed: bool) -> LearningStateRecord:
    return LearningStateRecord(
        learner_id=row[0],
        material_id=row[1],
        state_revision=row[2],
        map_revision=row[3],
        path_revision=row[4],
        assessment_revision=row[5],
        submission_id=row[6],
        replayed=replayed,
        document=deepcopy(row[7]),
    )


def _validated_state_record(
    state_row: Sequence[Any],
    *,
    replayed: bool,
    dsn: str | None,
) -> LearningStateRecord:
    document = state_row[7]
    if not isinstance(document, dict):
        raise LearningUpdateError("LEARNING_STATE_UNAVAILABLE")
    event_ids = document.get("source_answer_event_ids")
    if (
        not isinstance(event_ids, list)
        or any(not isinstance(event_id, str) for event_id in event_ids)
        or len(set(event_ids)) != len(event_ids)
    ):
        raise LearningUpdateError("LEARNING_STATE_UNAVAILABLE")
    try:
        with database_session(dsn) as session:
            event_rows = session.execute(
                select(AnswerEventRow.document).where(
                    AnswerEventRow.learner_id == state_row[0],
                    AnswerEventRow.material_id == state_row[1],
                    AnswerEventRow.answer_event_id.in_(event_ids),
                )
            ).all()
        bundle = read_assessment_for_scoring(
            state_row[0], state_row[1], state_row[3], state_row[4], state_row[5], dsn=dsn
        )
        events = [row[0] for row in event_rows]
        if {
            event["answer_event_id"] for event in events if isinstance(event, dict)
        } != set(event_ids) or len(events) != len(event_ids):
            raise LearningUpdateError("LEARNING_STATE_UNAVAILABLE")
        rebuilt = build_learning_state(
            trusted_learner_id=str(state_row[0]),
            knowledge_map=bundle.knowledge_map,
            learning_path=bundle.learning_path,
            assessment=bundle.assessment,
            answer_events=events,
            learning_event_stream=[],
        )
    except LearningUpdateError:
        raise
    except Exception:
        raise LearningUpdateError("LEARNING_STATE_UNAVAILABLE") from None
    if rebuilt != document:
        raise LearningUpdateError("LEARNING_STATE_UNAVAILABLE")
    return _record(state_row, replayed)


def submit_learning_update(
    learner_id: UUID,
    material_id: UUID,
    map_revision: str,
    path_revision: str,
    assessment_revision: str,
    responses: Sequence[AssessmentResponse],
    idempotency_key: str,
    *,
    dsn: str | None = None,
) -> LearningStateRecord:
    """以 server time/answer key 同步評分，並在一個 transaction 保存。"""
    if (
        not isinstance(learner_id, UUID)
        or not isinstance(material_id, UUID)
        or any(not isinstance(value, str) or _REVISION.fullmatch(value) is None for value in (map_revision, path_revision, assessment_revision))
    ):
        raise LearningUpdateError("LEARNING_UPDATE_INVALID")
    normalized = _responses(responses)
    key = _key_digest(idempotency_key)
    fingerprint = _canonical(
        {
            "material_id": str(material_id),
            "map_revision": map_revision,
            "path_revision": path_revision,
            "assessment_revision": assessment_revision,
            "responses": [
                {"question_id": item.question_id, "selected_option_id": item.selected_option_id}
                for item in normalized
            ],
        }
    )
    try:
        with database_session(dsn) as session:
            locked_material = session.execute(
                select(Material.material_id)
                .where(
                    Material.learner_id == learner_id,
                    Material.material_id == material_id,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if locked_material is None:
                raise LearningUpdateError("LEARNING_UPDATE_INVALID")
            existing = session.execute(
                select(
                    LearningStateRow.learner_id,
                    LearningStateRow.material_id,
                    LearningStateRow.state_revision,
                    LearningStateRow.map_revision,
                    LearningStateRow.path_revision,
                    LearningStateRow.assessment_revision,
                    LearningStateRow.submission_id,
                    LearningStateRow.document,
                    LearningStateRow.request_fingerprint,
                )
                .where(
                    LearningStateRow.learner_id == learner_id,
                    LearningStateRow.idempotency_key_sha256 == key,
                )
                .with_for_update()
            ).one_or_none()
            if existing is not None:
                if bytes(existing[8]) != fingerprint:
                    raise LearningUpdateError("IDEMPOTENCY_CONFLICT")
                return _validated_state_record(existing[:8], replayed=True, dsn=dsn)
            try:
                bundle = read_assessment_for_scoring(
                    learner_id,
                    material_id,
                    map_revision,
                    path_revision,
                    assessment_revision,
                    dsn=dsn,
                )
            except DomainRevisionError as error:
                if str(error) == "ASSESSMENT_STORAGE_FAILED":
                    raise LearningUpdateError("LEARNING_UPDATE_STORAGE_FAILED") from None
                raise LearningUpdateError("LEARNING_UPDATE_INVALID") from None
            questions = {
                item["question_id"]: item
                for item in bundle.assessment["questions"]
            }
            if any(
                item.question_id not in questions
                or item.selected_option_id
                not in {
                    option["option_id"]
                    for option in questions[item.question_id]["options"]
                }
                for item in normalized
            ):
                raise LearningUpdateError("LEARNING_UPDATE_INVALID")
            assessment_exists = session.execute(
                select(Assessment.assessment_revision)
                .where(
                    Assessment.learner_id == learner_id,
                    Assessment.material_id == material_id,
                    Assessment.assessment_revision == assessment_revision,
                    Assessment.map_revision == map_revision,
                    Assessment.path_revision == path_revision,
                )
                .with_for_update(read=True)
            ).scalar_one_or_none()
            if assessment_exists is None:
                raise LearningUpdateError("LEARNING_UPDATE_INVALID")
            event_count = session.scalar(
                select(func.count())
                .select_from(AnswerEventRow)
                .where(
                    AnswerEventRow.learner_id == learner_id,
                    AnswerEventRow.material_id == material_id,
                )
            )
            prior_rows = session.execute(
                select(AnswerEventRow.document)
                .where(
                    AnswerEventRow.learner_id == learner_id,
                    AnswerEventRow.material_id == material_id,
                    AnswerEventRow.assessment_revision == assessment_revision,
                )
                .order_by(AnswerEventRow.created_at, AnswerEventRow.answer_event_id)
            ).all()
            if event_count is None:
                raise LearningUpdateError("LEARNING_UPDATE_STORAGE_FAILED")
            if event_count + len(normalized) > _EVENT_LIMIT:
                raise LearningUpdateError("ANSWER_EVENT_CAPACITY_EXCEEDED")
            submitted_at = datetime.now(UTC).isoformat()
            scored = score_submission(
                bundle.assessment,
                {
                    "schema": "assessment-submission/v1",
                    "assessment_id": bundle.assessment["assessment_id"],
                    "assessment_revision": assessment_revision,
                    "idempotency_key": key.hex(),
                    "submitted_at": submitted_at,
                    "responses": [
                        {"question_id": item.question_id, "selected_option_id": item.selected_option_id}
                        for item in normalized
                    ],
                },
                trusted_learner_id=str(learner_id),
                knowledge_map=bundle.knowledge_map,
                learning_path_revision=path_revision,
                existing_events=[row[0] for row in prior_rows],
            )
            if scored.get("processing") != "succeeded" or scored.get("replayed") is not False:
                raise LearningUpdateError("LEARNING_SCORING_FAILED")
            new_events = scored["answer_events"]
            all_events = [row[0] for row in prior_rows] + new_events
            state = build_learning_state(
                trusted_learner_id=str(learner_id),
                knowledge_map=bundle.knowledge_map,
                learning_path=bundle.learning_path,
                assessment=bundle.assessment,
                answer_events=all_events,
                learning_event_stream=[],
            )
            submission_id = uuid4()
            created = datetime.now(UTC)
            for event in new_events:
                session.add(
                    AnswerEventRow(
                        answer_event_id=event["answer_event_id"],
                        submission_id=submission_id,
                        learner_id=learner_id,
                        material_id=material_id,
                        assessment_revision=assessment_revision,
                        question_id=event["question_id"],
                        document=event,
                        created_at=created,
                    )
                )
            session.add(
                LearningStateRow(
                    state_revision=state["revision"],
                    submission_id=submission_id,
                    learner_id=learner_id,
                    material_id=material_id,
                    map_revision=map_revision,
                    path_revision=path_revision,
                    assessment_revision=assessment_revision,
                    idempotency_key_sha256=key,
                    request_fingerprint=fingerprint,
                    document=state,
                    created_at=created,
                )
            )
        return LearningStateRecord(
            learner_id, material_id, state["revision"], map_revision, path_revision,
            assessment_revision, submission_id, False, deepcopy(state)
        )
    except LearningUpdateError:
        raise
    except Exception:
        raise LearningUpdateError("LEARNING_UPDATE_STORAGE_FAILED") from None


def read_learning_state(
    learner_id: UUID,
    material_id: UUID,
    state_revision: str,
    *,
    dsn: str | None = None,
) -> LearningStateRecord:
    """讀取 owner-scoped State，並由 immutable AnswerEvents 重新驗證。"""
    if (
        not isinstance(learner_id, UUID)
        or not isinstance(material_id, UUID)
        or not isinstance(state_revision, str)
        or _REVISION.fullmatch(state_revision) is None
    ):
        raise LearningUpdateError("LEARNING_STATE_UNAVAILABLE")
    try:
        with database_session(dsn) as session:
            state_row = session.execute(
                select(
                    LearningStateRow.learner_id,
                    LearningStateRow.material_id,
                    LearningStateRow.state_revision,
                    LearningStateRow.map_revision,
                    LearningStateRow.path_revision,
                    LearningStateRow.assessment_revision,
                    LearningStateRow.submission_id,
                    LearningStateRow.document,
                ).where(
                    LearningStateRow.learner_id == learner_id,
                    LearningStateRow.material_id == material_id,
                    LearningStateRow.state_revision == state_revision,
                )
            ).one_or_none()
            if state_row is None:
                raise LearningUpdateError("LEARNING_STATE_UNAVAILABLE")
        return _validated_state_record(state_row, replayed=False, dsn=dsn)
    except LearningUpdateError:
        raise
    except Exception:
        raise LearningUpdateError("LEARNING_STATE_UNAVAILABLE") from None
