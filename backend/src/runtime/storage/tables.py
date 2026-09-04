from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, ForeignKeyConstraint, Integer, LargeBinary, Text, UniqueConstraint, create_engine, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID as PostgreSQLUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.pool import NullPool

from .database import resolve_database_dsn


class Base(DeclarativeBase):
    """Final pre-release schema；DDL 唯一來源仍是 migration。"""


class SchemaMigration(Base):
    __tablename__ = "schema_migrations"

    version: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    sql_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Learner(Base):
    __tablename__ = "learners"

    learner_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LearnerSession(Base):
    __tablename__ = "learner_sessions"

    session_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    learner_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), ForeignKey("learners.learner_id"), nullable=False)
    token_sha256: Mapped[bytes] = mapped_column(LargeBinary, unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idle_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Material(Base):
    __tablename__ = "materials"
    __table_args__ = (
        UniqueConstraint("learner_id", "material_id"),
        UniqueConstraint("learner_id", "upload_idempotency_key_sha256"),
        ForeignKeyConstraint(
            ["learner_id", "material_id", "source_artifact_id"],
            ["artifacts.learner_id", "artifacts.material_id", "artifacts.artifact_id"],
            name="materials_source_artifact_fk",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
    )

    material_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    learner_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), ForeignKey("learners.learner_id"), nullable=False)
    source_artifact_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), unique=True, nullable=False)
    upload_idempotency_key_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    upload_request_fingerprint: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint("learner_id", "material_id", "artifact_id"),
        ForeignKeyConstraint(["learner_id", "material_id"], ["materials.learner_id", "materials.material_id"]),
    )

    artifact_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    learner_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    material_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    media_type: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MaterialProcessingRun(Base):
    __tablename__ = "material_processing_runs"
    __table_args__ = (
        UniqueConstraint("learner_id", "material_id", "run_id"),
        UniqueConstraint("learner_id", "idempotency_key_sha256"),
        ForeignKeyConstraint(
            ["learner_id", "material_id", "source_artifact_id"],
            ["artifacts.learner_id", "artifacts.material_id", "artifacts.artifact_id"],
        ),
    )

    run_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    learner_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    material_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    source_artifact_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    idempotency_key_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    request_fingerprint: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    runtime_binding: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    progress_stage: Mapped[str] = mapped_column(Text, nullable=False)
    completed_pages: Mapped[int] = mapped_column(Integer, nullable=False)
    total_pages: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(Text)
    output_binding: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KnowledgeStructure(Base):
    __tablename__ = "knowledge_structures"
    __table_args__ = (
        UniqueConstraint("learner_id", "material_id", "run_id"),
        ForeignKeyConstraint(
            ["learner_id", "material_id", "run_id"],
            ["material_processing_runs.learner_id", "material_processing_runs.material_id", "material_processing_runs.run_id"],
        ),
    )

    learner_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    material_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    structure_revision: Mapped[str] = mapped_column(Text, primary_key=True)
    run_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    document: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class StudySession(Base):
    __tablename__ = "study_sessions"
    __table_args__ = (
        UniqueConstraint("learner_id", "idempotency_key_sha256"),
        UniqueConstraint("study_session_id", "knowledge_structure_revision"),
        ForeignKeyConstraint(
            ["learner_id", "material_id", "knowledge_structure_revision"],
            ["knowledge_structures.learner_id", "knowledge_structures.material_id", "knowledge_structures.structure_revision"],
        ),
    )

    study_session_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    learner_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    material_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    knowledge_structure_revision: Mapped[str] = mapped_column(Text, nullable=False)
    current_concept_id: Mapped[str | None] = mapped_column(Text)
    no_safe_claim_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default=text("ARRAY[]::text[]"))
    deferred_concept_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default=text("ARRAY[]::text[]"))
    last_applied_guidance_revision: Mapped[str | None] = mapped_column(Text)
    last_applied_progress_sha256: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    request_fingerprint: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_event_number: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))


class Assessment(Base):
    __tablename__ = "assessments"
    __table_args__ = (
        UniqueConstraint("study_session_id", "question_id"),
        UniqueConstraint("study_session_id", "semantic_identity"),
        UniqueConstraint("study_session_id", "request_idempotency_key_sha256"),
        ForeignKeyConstraint(
            ["study_session_id", "knowledge_structure_revision"],
            ["study_sessions.study_session_id", "study_sessions.knowledge_structure_revision"],
        ),
    )

    assessment_revision: Mapped[str] = mapped_column(Text, primary_key=True)
    study_session_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    knowledge_structure_revision: Mapped[str] = mapped_column(Text, nullable=False)
    question_id: Mapped[str] = mapped_column(Text, nullable=False)
    semantic_identity: Mapped[str] = mapped_column(Text, nullable=False)
    learning_angle: Mapped[str] = mapped_column(Text, nullable=False)
    target_concept_id: Mapped[str] = mapped_column(Text, nullable=False)
    target_claim_id: Mapped[str] = mapped_column(Text, nullable=False)
    public_document: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    private_answer_document: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    generation_provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    mastery_qualified: Mapped[bool] = mapped_column(Boolean, nullable=False)
    request_idempotency_key_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    request_fingerprint: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AnswerEvent(Base):
    __tablename__ = "answer_events"
    __table_args__ = (
        UniqueConstraint("study_session_id", "assessment_revision"),
        UniqueConstraint("study_session_id", "event_number"),
        UniqueConstraint("study_session_id", "idempotency_key_sha256"),
        ForeignKeyConstraint(
            ["study_session_id", "knowledge_structure_revision"],
            ["study_sessions.study_session_id", "study_sessions.knowledge_structure_revision"],
        ),
    )

    answer_event_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    study_session_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    material_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    knowledge_structure_revision: Mapped[str] = mapped_column(Text, nullable=False)
    assessment_revision: Mapped[str] = mapped_column(Text, ForeignKey("assessments.assessment_revision"), nullable=False)
    question_id: Mapped[str] = mapped_column(Text, nullable=False)
    semantic_identity: Mapped[str] = mapped_column(Text, nullable=False)
    target_concept_id: Mapped[str] = mapped_column(Text, nullable=False)
    target_claim_id: Mapped[str] = mapped_column(Text, nullable=False)
    selected_option_id: Mapped[str] = mapped_column(Text, nullable=False)
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False)
    mastery_qualified: Mapped[bool] = mapped_column(Boolean, nullable=False)
    event_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    idempotency_key_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    request_fingerprint: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


@contextmanager
def database_session(dsn: str | None = None) -> Generator[Session, None, None]:
    resolved = resolve_database_dsn(dsn)
    engine = create_engine(
        "postgresql+psycopg://",
        creator=lambda: psycopg.connect(resolved),
        poolclass=NullPool,
        hide_parameters=True,
    )
    try:
        with Session(engine, expire_on_commit=False) as session, session.begin():
            yield session
    finally:
        engine.dispose()


@contextmanager
def deferred_artifact_session(dsn: str | None = None) -> Generator[Session, None, None]:
    with database_session(dsn) as session:
        session.execute(text("SET CONSTRAINTS materials_source_artifact_fk DEFERRED"))
        yield session
