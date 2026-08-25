from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
import json
import re
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from runtime.learner_session import TrustedLearner
from runtime.storage.database import DatabaseConfigurationError
from runtime.storage.tables import StudySession, database_session

from .map_context import MapContext, MapContextError, _read_map_context


_MAP_REVISION_PATTERN = re.compile(r"^knowledge-map:sha256:[0-9a-f]{64}$")


class StudySessionError(RuntimeError):
    """StudySession request 或儲存狀態無法安全處理。"""


@dataclass(frozen=True)
class StoredStudySession:
    study_session_id: UUID
    learner_id: UUID
    material_id: UUID
    knowledge_map_revision: str
    current_formal_concept_id: str | None
    status: str
    started_at: datetime
    completed_at: datetime | None
    last_event_number: int
    idempotency_key_sha256: bytes = field(repr=False)
    request_fingerprint: bytes = field(repr=False)


def _error(reason: str) -> StudySessionError:
    return StudySessionError(reason)


def _learner_id(learner: TrustedLearner) -> UUID:
    if not isinstance(learner, TrustedLearner) or not isinstance(
        learner.learner_id, UUID
    ):
        raise _error("STUDY_SESSION_REQUEST_INVALID")
    return learner.learner_id


def _key_digest(idempotency_key: str) -> bytes:
    try:
        encoded = idempotency_key.encode("utf-8")
    except (AttributeError, UnicodeError):
        raise _error("STUDY_SESSION_REQUEST_INVALID") from None
    if not 1 <= len(encoded) <= 256:
        raise _error("STUDY_SESSION_REQUEST_INVALID")
    return sha256(encoded).digest()


def _create_fingerprint(
    material_id: UUID,
    knowledge_map_revision: str,
    current_formal_concept_id: str | None,
) -> bytes:
    request = json.dumps(
        {
            "current_formal_concept_id": current_formal_concept_id,
            "knowledge_map_revision": knowledge_map_revision,
            "material_id": str(material_id),
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return sha256(request).digest()


def _read_stored_row(
    session: Session,
    learner_id: UUID,
    study_session_id: UUID,
    *,
    for_update: bool = False,
) -> StudySession:
    statement = select(StudySession).where(
        StudySession.learner_id == learner_id,
        StudySession.study_session_id == study_session_id,
    )
    if for_update:
        statement = statement.with_for_update()
    stored = session.scalar(statement)
    if stored is None:
        raise _error("STUDY_SESSION_UNAVAILABLE")
    return stored


def _validate_binding(session: Session, stored: StudySession) -> MapContext:
    try:
        context = _read_map_context(
            session,
            stored.learner_id,
            stored.material_id,
            stored.knowledge_map_revision,
        )
    except MapContextError:
        raise _error("STUDY_SESSION_UNAVAILABLE") from None
    known_concepts = {
        concept.formal_concept_id for concept in context.formal_concepts
    }
    if (
        stored.current_formal_concept_id is not None
        and stored.current_formal_concept_id not in known_concepts
    ):
        raise _error("STUDY_SESSION_UNAVAILABLE")
    return context


def _stored_session(stored: StudySession) -> StoredStudySession:
    if stored.status == "active":
        if stored.completed_at is not None:
            raise _error("STUDY_SESSION_LIFECYCLE_CONFLICT")
    elif stored.status == "completed":
        if stored.completed_at is None or stored.completed_at < stored.started_at:
            raise _error("STUDY_SESSION_LIFECYCLE_CONFLICT")
    else:
        raise _error("STUDY_SESSION_LIFECYCLE_CONFLICT")
    if (
        type(stored.last_event_number) is not int
        or stored.last_event_number < 0
        or len(bytes(stored.idempotency_key_sha256)) != 32
        or len(bytes(stored.request_fingerprint)) != 32
    ):
        raise _error("STUDY_SESSION_UNAVAILABLE")
    return StoredStudySession(
        study_session_id=stored.study_session_id,
        learner_id=stored.learner_id,
        material_id=stored.material_id,
        knowledge_map_revision=stored.knowledge_map_revision,
        current_formal_concept_id=stored.current_formal_concept_id,
        status=stored.status,
        started_at=stored.started_at,
        completed_at=stored.completed_at,
        last_event_number=stored.last_event_number,
        idempotency_key_sha256=bytes(stored.idempotency_key_sha256),
        request_fingerprint=bytes(stored.request_fingerprint),
    )


def create_study_session(
    learner: TrustedLearner,
    material_id: UUID,
    knowledge_map_revision: str,
    idempotency_key: str,
    *,
    current_formal_concept_id: str | None = None,
    dsn: str | None = None,
) -> StoredStudySession:
    """建立 owner-bound StudySession；相同 request replay 回傳同一筆。"""

    learner_id = _learner_id(learner)
    if (
        not isinstance(material_id, UUID)
        or not isinstance(knowledge_map_revision, str)
        or _MAP_REVISION_PATTERN.fullmatch(knowledge_map_revision) is None
        or (
            current_formal_concept_id is not None
            and not isinstance(current_formal_concept_id, str)
        )
    ):
        raise _error("STUDY_SESSION_REQUEST_INVALID")
    key_digest = _key_digest(idempotency_key)
    fingerprint = _create_fingerprint(
        material_id, knowledge_map_revision, current_formal_concept_id
    )

    try:
        with database_session(dsn) as session:
            context = _read_map_context(
                session, learner_id, material_id, knowledge_map_revision
            )
            known_concepts = {
                concept.formal_concept_id for concept in context.formal_concepts
            }
            if (
                current_formal_concept_id is not None
                and current_formal_concept_id not in known_concepts
            ):
                raise _error("STUDY_SESSION_TARGET_INVALID")
            started_at = datetime.now(UTC)
            session.execute(
                insert(StudySession)
                .values(
                    study_session_id=uuid4(),
                    learner_id=learner_id,
                    material_id=material_id,
                    knowledge_map_revision=knowledge_map_revision,
                    current_formal_concept_id=current_formal_concept_id,
                    status="active",
                    idempotency_key_sha256=key_digest,
                    request_fingerprint=fingerprint,
                    started_at=started_at,
                    completed_at=None,
                    last_event_number=0,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        StudySession.learner_id,
                        StudySession.idempotency_key_sha256,
                    ]
                )
            )
            stored = session.scalar(
                select(StudySession).where(
                    StudySession.learner_id == learner_id,
                    StudySession.idempotency_key_sha256 == key_digest,
                )
            )
            if stored is None:
                raise _error("STUDY_SESSION_STORAGE_FAILED")
            if bytes(stored.request_fingerprint) != fingerprint:
                raise _error("STUDY_SESSION_IDEMPOTENCY_CONFLICT")
            return _stored_session(stored)
    except StudySessionError:
        raise
    except MapContextError:
        raise _error("STUDY_SESSION_MAP_UNAVAILABLE") from None
    except (
        DatabaseConfigurationError,
        SQLAlchemyError,
        KeyError,
        TypeError,
        ValueError,
    ):
        raise _error("STUDY_SESSION_STORAGE_FAILED") from None


def read_study_session(
    learner: TrustedLearner,
    study_session_id: UUID,
    *,
    dsn: str | None = None,
) -> StoredStudySession:
    """只以 server 已解析的 learner identity 讀取 StudySession。"""

    learner_id = _learner_id(learner)
    if not isinstance(study_session_id, UUID):
        raise _error("STUDY_SESSION_REQUEST_INVALID")
    try:
        with database_session(dsn) as session:
            stored = _read_stored_row(session, learner_id, study_session_id)
            _validate_binding(session, stored)
            return _stored_session(stored)
    except StudySessionError:
        raise
    except (
        DatabaseConfigurationError,
        SQLAlchemyError,
        KeyError,
        TypeError,
        ValueError,
    ):
        raise _error("STUDY_SESSION_STORAGE_FAILED") from None


def complete_study_session(
    learner: TrustedLearner,
    study_session_id: UUID,
    *,
    dsn: str | None = None,
) -> StoredStudySession:
    """完成 active StudySession；重複完成是同一筆資料的安全 replay。"""

    learner_id = _learner_id(learner)
    if not isinstance(study_session_id, UUID):
        raise _error("STUDY_SESSION_REQUEST_INVALID")
    try:
        with database_session(dsn) as session:
            stored = _read_stored_row(
                session, learner_id, study_session_id, for_update=True
            )
            _validate_binding(session, stored)
            if stored.status == "active":
                stored.status = "completed"
                stored.completed_at = datetime.now(UTC)
                session.flush()
            elif stored.status != "completed":
                raise _error("STUDY_SESSION_LIFECYCLE_CONFLICT")
            return _stored_session(stored)
    except StudySessionError:
        raise
    except (
        DatabaseConfigurationError,
        SQLAlchemyError,
        KeyError,
        TypeError,
        ValueError,
    ):
        raise _error("STUDY_SESSION_STORAGE_FAILED") from None
