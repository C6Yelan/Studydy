from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
import json
import re
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from runtime.learner_session import TrustedLearner
from runtime.storage.tables import StudySession, database_session

from .map_context import MapContext, _context_from_validated_document
from runtime.storage.knowledge_structures import _read_verified_document, KnowledgeStructureStoreError
from runtime.source_normalization import SourceError


_REVISION = re.compile(r"knowledge-structure:sha256:[0-9a-f]{64}")


class StudySessionError(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredStudySession:
    study_session_id: UUID
    learner_id: UUID
    material_id: UUID
    knowledge_structure_revision: str
    current_concept_id: str | None
    no_safe_claim_ids: tuple[str, ...]
    deferred_concept_ids: tuple[str, ...]
    status: str
    started_at: datetime
    completed_at: datetime | None
    last_event_number: int
    idempotency_key_sha256: bytes = field(repr=False)
    request_fingerprint: bytes = field(repr=False)


def _learner(learner: TrustedLearner) -> UUID:
    if not isinstance(learner, TrustedLearner) or not isinstance(learner.learner_id, UUID):
        raise StudySessionError("STUDY_SESSION_REQUEST_INVALID")
    return learner.learner_id


def _key(value: str) -> bytes:
    if not isinstance(value, str) or not 1 <= len(value.encode()) <= 256:
        raise StudySessionError("STUDY_SESSION_REQUEST_INVALID")
    return sha256(value.encode()).digest()


def _fingerprint(material_id: UUID, revision: str, concept_id: str | None) -> bytes:
    return sha256(json.dumps(
        {
            "material_id": str(material_id),
            "knowledge_structure_revision": revision,
            "current_concept_id": concept_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()).digest()


def _context(
    session, learner_id: UUID, material_id: UUID, revision: str, *, lock: bool = False
) -> MapContext:
    try:
        document = _read_verified_document(session, learner_id, material_id, revision=revision, lock=lock)
        return _context_from_validated_document(material_id, document)
    except (KnowledgeStructureStoreError, SourceError):
        raise StudySessionError("STUDY_SESSION_MAP_UNAVAILABLE") from None


def _row(session, learner_id: UUID, study_session_id: UUID, *, lock: bool = False) -> StudySession:
    statement = select(StudySession).where(
        StudySession.learner_id == learner_id,
        StudySession.study_session_id == study_session_id,
    )
    stored = session.scalar(statement.with_for_update() if lock else statement)
    if stored is None:
        raise StudySessionError("STUDY_SESSION_UNAVAILABLE")
    return stored


def _validate(session, row: StudySession) -> MapContext:
    context = _context(session, row.learner_id, row.material_id, row.knowledge_structure_revision)
    _validate_context(row, context)
    return context


def _validate_context(row, context):
    concept_ids = {concept.concept_id for concept in context.concepts}
    claim_ids = {claim.claim_id for concept in context.concepts for claim in concept.claims}
    if (
        row.current_concept_id is not None and row.current_concept_id not in concept_ids
        or not set(row.no_safe_claim_ids) <= claim_ids
        or not set(row.deferred_concept_ids) <= concept_ids
        or len(row.no_safe_claim_ids) != len(set(row.no_safe_claim_ids))
        or len(row.deferred_concept_ids) != len(set(row.deferred_concept_ids))
    ):
        raise StudySessionError("STUDY_SESSION_UNAVAILABLE")


def _stored(row: StudySession) -> StoredStudySession:
    if (
        row.status not in {"active", "no_safe", "completed"}
        or (row.status == "completed") != (row.completed_at is not None)
    ):
        raise StudySessionError("STUDY_SESSION_LIFECYCLE_CONFLICT")
    return StoredStudySession(
        row.study_session_id, row.learner_id, row.material_id,
        row.knowledge_structure_revision, row.current_concept_id,
        tuple(row.no_safe_claim_ids), tuple(row.deferred_concept_ids), row.status,
        row.started_at, row.completed_at, row.last_event_number,
        bytes(row.idempotency_key_sha256), bytes(row.request_fingerprint),
    )


def create_study_session(
    learner: TrustedLearner,
    material_id: UUID,
    knowledge_structure_revision: str,
    idempotency_key: str,
    *,
    current_concept_id: str | None = None,
    dsn: str | None = None,
) -> StoredStudySession:
    learner_id = _learner(learner)
    if (
        not isinstance(material_id, UUID)
        or not isinstance(knowledge_structure_revision, str)
        or _REVISION.fullmatch(knowledge_structure_revision) is None
    ):
        raise StudySessionError("STUDY_SESSION_REQUEST_INVALID")
    key = _key(idempotency_key)
    fingerprint = _fingerprint(material_id, knowledge_structure_revision, current_concept_id)
    try:
        with database_session(dsn) as session:
            # 在不可變的地圖身分上序列化建立請求，包括首次寫入。
            context = _context(session, learner_id, material_id, knowledge_structure_revision, lock=True)
            initial_intent = session.scalar(select(StudySession).where(
                StudySession.learner_id == learner_id, StudySession.idempotency_key_sha256 == key,
            ))
            if initial_intent is not None and bytes(initial_intent.request_fingerprint) != fingerprint:
                raise StudySessionError("STUDY_SESSION_IDEMPOTENCY_CONFLICT")
            canonical = session.scalars(select(StudySession).where(
                StudySession.learner_id == learner_id, StudySession.material_id == material_id,
                StudySession.knowledge_structure_revision == knowledge_structure_revision,
            )).one_or_none()
            if canonical is not None:
                # 後續同範圍請求只讀取既有 session；建立時的意圖指紋保持不變。
                _validate(session, canonical)
                return _stored(canonical)
            known = {concept.concept_id for concept in context.concepts}
            from .inherited_progress import preferred_focus
            selected = (
                current_concept_id
                or preferred_focus(session, learner_id, material_id, knowledge_structure_revision)
                or (context.initial_learning_path[0] if context.initial_learning_path else None)
            )
            if selected not in known:
                raise StudySessionError("STUDY_SESSION_TARGET_INVALID")
            session.execute(insert(StudySession).values(
                study_session_id=uuid4(), learner_id=learner_id, material_id=material_id,
                knowledge_structure_revision=knowledge_structure_revision,
                current_concept_id=selected, no_safe_claim_ids=[], deferred_concept_ids=[],
                last_applied_guidance_revision=None, last_applied_progress_sha256=None,
                status="active", idempotency_key_sha256=key, request_fingerprint=fingerprint,
                started_at=datetime.now(UTC), completed_at=None, last_event_number=0,
            ).on_conflict_do_nothing(index_elements=[
                StudySession.learner_id, StudySession.idempotency_key_sha256,
            ]))
            stored = session.scalar(select(StudySession).where(
                StudySession.learner_id == learner_id,
                StudySession.idempotency_key_sha256 == key,
            ))
            if stored is None or bytes(stored.request_fingerprint) != fingerprint:
                raise StudySessionError("STUDY_SESSION_IDEMPOTENCY_CONFLICT")
            _validate(session, stored)
            return _stored(stored)
    except StudySessionError:
        raise
    except Exception:
        raise StudySessionError("STUDY_SESSION_STORAGE_FAILED") from None


def read_study_session(
    learner: TrustedLearner, study_session_id: UUID, *, dsn: str | None = None
) -> StoredStudySession:
    learner_id = _learner(learner)
    try:
        with database_session(dsn) as session:
            stored = _row(session, learner_id, study_session_id)
            _validate(session, stored)
            return _stored(stored)
    except StudySessionError:
        raise
    except Exception:
        raise StudySessionError("STUDY_SESSION_STORAGE_FAILED") from None


def set_current_study_concept(
    learner: TrustedLearner, study_session_id: UUID, concept_id: str,
    *, dsn: str | None = None,
) -> StoredStudySession:
    learner_id = _learner(learner)
    try:
        with database_session(dsn) as session:
            stored = _row(session, learner_id, study_session_id, lock=True)
            context = _validate(session, stored)
            # 焦點只決定導覽位置；已建立題組由自己的 Concept／KS／成員綁定保護。
            if (
                stored.status not in {"active", "no_safe"}
                or concept_id not in {concept.concept_id for concept in context.concepts}
            ):
                raise StudySessionError("STUDY_SESSION_TARGET_INVALID")
            stored.current_concept_id = concept_id
            stored.status = "active"
            stored.last_applied_guidance_revision = None
            stored.last_applied_progress_sha256 = None
            return _stored(stored)
    except StudySessionError:
        raise
    except Exception:
        raise StudySessionError("STUDY_SESSION_STORAGE_FAILED") from None
