from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from sqlalchemy import (
    BigInteger,
    Boolean,
    CHAR,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    create_engine,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PostgreSQLUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.pool import NullPool

from .database import resolve_database_dsn


class Base(DeclarativeBase):
    """集中描述目前 PostgreSQL schema；migration 仍是 DDL 唯一來源。"""


class SchemaMigration(Base):
    __tablename__ = "schema_migrations"
    __table_args__ = (
        CheckConstraint("sql_sha256 ~ '^[0-9a-f]{64}$'"),
    )

    version: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    sql_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Learner(Base):
    __tablename__ = "learners"

    learner_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LearnerSession(Base):
    __tablename__ = "learner_sessions"
    __table_args__ = (
        UniqueConstraint("token_sha256", name="idx_sessions_resolve"),
        CheckConstraint("octet_length(token_sha256) = 32"),
        CheckConstraint(
            "created_at <= idle_expires_at AND idle_expires_at <= absolute_expires_at"
        ),
    )

    session_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    learner_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("learners.learner_id"), nullable=False
    )
    token_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idle_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Material(Base):
    __tablename__ = "materials"
    __table_args__ = (
        UniqueConstraint("source_artifact_id"),
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
        CheckConstraint("octet_length(upload_idempotency_key_sha256) = 32"),
        CheckConstraint("octet_length(upload_request_fingerprint) = 32"),
    )

    material_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    learner_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("learners.learner_id"), nullable=False
    )
    source_artifact_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    upload_idempotency_key_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    upload_request_fingerprint: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint("learner_id", "material_id", "artifact_id"),
        ForeignKeyConstraint(
            ["learner_id", "material_id"],
            ["materials.learner_id", "materials.material_id"],
        ),
        Index(
            "idx_artifacts_one_source_pdf",
            "learner_id",
            "material_id",
            unique=True,
            postgresql_where="kind = 'source_pdf'",
        ),
        CheckConstraint("kind IN ('source_pdf', 'resource_pdf')"),
        CheckConstraint("media_type = 'application/pdf'"),
        CheckConstraint("octet_length(sha256) = 32"),
        CheckConstraint("size_bytes > 0 AND size_bytes <= 104857600"),
    )

    artifact_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    learner_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    material_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    media_type: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class StudyMaterialOutput(Base):
    __tablename__ = "study_material_outputs"
    __table_args__ = (
        PrimaryKeyConstraint("learner_id", "material_id", "output_revision"),
        ForeignKeyConstraint(
            ["learner_id", "material_id"],
            ["materials.learner_id", "materials.material_id"],
        ),
        CheckConstraint(
            "document ? 'output_id' AND document ->> 'output_id' = output_revision"
        ),
    )

    learner_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    material_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    output_revision: Mapped[str] = mapped_column(Text)
    document: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class KnowledgeMap(Base):
    __tablename__ = "knowledge_maps"
    __table_args__ = (
        PrimaryKeyConstraint("learner_id", "material_id", "map_revision"),
        ForeignKeyConstraint(
            ["learner_id", "material_id", "source_output_revision"],
            [
                "study_material_outputs.learner_id",
                "study_material_outputs.material_id",
                "study_material_outputs.output_revision",
            ],
        ),
        CheckConstraint(
            "document ? 'revision' AND document ->> 'revision' = map_revision"
        ),
        CheckConstraint(
            "document ? 'source_output_id' "
            "AND document ->> 'source_output_id' = source_output_revision"
        ),
    )

    learner_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    material_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    map_revision: Mapped[str] = mapped_column(Text)
    source_output_revision: Mapped[str] = mapped_column(Text, nullable=False)
    document: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ResourceCatalog(Base):
    __tablename__ = "resource_catalogs"
    __table_args__ = (
        PrimaryKeyConstraint("learner_id", "material_id", "catalog_revision"),
        ForeignKeyConstraint(
            ["learner_id", "material_id"],
            ["materials.learner_id", "materials.material_id"],
        ),
        CheckConstraint("char_length(subject) BETWEEN 1 AND 128"),
        CheckConstraint(
            "document ? 'catalog_revision' "
            "AND document ->> 'catalog_revision' = catalog_revision"
        ),
    )

    learner_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    material_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    catalog_revision: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    document: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LearningResourceResult(Base):
    __tablename__ = "learning_resource_results"
    __table_args__ = (
        PrimaryKeyConstraint("learner_id", "material_id", "result_revision"),
        ForeignKeyConstraint(
            ["learner_id", "material_id", "source_output_revision"],
            [
                "study_material_outputs.learner_id",
                "study_material_outputs.material_id",
                "study_material_outputs.output_revision",
            ],
        ),
        ForeignKeyConstraint(
            ["learner_id", "material_id", "catalog_revision"],
            ["resource_catalogs.learner_id", "resource_catalogs.material_id", "resource_catalogs.catalog_revision"],
        ),
        CheckConstraint(
            "document ? 'result_revision' "
            "AND document ->> 'result_revision' = result_revision"
        ),
        CheckConstraint(
            "document ? 'source_study_material_output_revision' "
            "AND document ->> 'source_study_material_output_revision' = source_output_revision"
        ),
        CheckConstraint(
            "document ? 'catalog_revision' "
            "AND document ->> 'catalog_revision' = catalog_revision"
        ),
    )

    learner_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    material_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    result_revision: Mapped[str] = mapped_column(Text)
    source_output_revision: Mapped[str] = mapped_column(Text, nullable=False)
    catalog_revision: Mapped[str] = mapped_column(Text, nullable=False)
    document: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MaterialProcessingRun(Base):
    __tablename__ = "material_processing_runs"
    __table_args__ = (
        UniqueConstraint("learner_id", "idempotency_key_sha256"),
        UniqueConstraint("learner_id", "material_id", "run_id"),
        ForeignKeyConstraint(
            ["learner_id", "material_id", "source_artifact_id"],
            ["artifacts.learner_id", "artifacts.material_id", "artifacts.artifact_id"],
        ),
        Index(
            "idx_material_runs_pending",
            "created_at",
            "run_id",
            postgresql_where="status = 'pending'",
        ),
        CheckConstraint("octet_length(idempotency_key_sha256) = 32"),
        CheckConstraint("octet_length(request_fingerprint) = 32"),
        CheckConstraint(
            "status IN ('running', 'pending', 'succeeded', 'partial', 'failed')"
        ),
        CheckConstraint(
            "error_code IS NULL OR error_code ~ '^[A-Z][A-Z0-9_]{0,99}$'"
        ),
        CheckConstraint(
            "(status IN ('running', 'pending') AND error_code IS NULL "
            "AND output_binding IS NULL AND completed_at IS NULL) OR "
            "(status IN ('succeeded', 'partial') AND error_code IS NULL "
            "AND output_binding IS NOT NULL "
            "AND output_binding ->> 'schema' = 'material-run-output-binding/v3' "
            "AND completed_at IS NOT NULL) OR "
            "(status = 'failed' AND error_code IS NOT NULL "
            "AND output_binding IS NULL AND completed_at IS NOT NULL)"
        ),
        CheckConstraint(
            "progress_stage IN ('queued', 'page_evidence', "
            "'concept_generation', 'knowledge_map_generation', "
            "'publishing', 'completed')"
        ),
        CheckConstraint(
            "completed_pages >= 0 AND ((progress_stage = 'queued' "
            "AND completed_pages = 0 AND total_pages IS NULL) OR "
            "(progress_stage <> 'queued' AND total_pages IS NOT NULL "
            "AND total_pages >= 1 "
            "AND completed_pages <= total_pages)) AND "
            "(progress_stage NOT IN ('knowledge_map_generation', 'publishing') "
            "OR completed_pages = total_pages)"
        ),
        CheckConstraint(
            "(status = 'pending' AND progress_stage = 'queued' "
            "AND completed_pages = 0 AND total_pages IS NULL) OR "
            "(status = 'running' AND progress_stage <> 'completed') OR "
            "(status = 'failed' AND progress_stage <> 'completed') OR "
            "(status IN ('succeeded', 'partial') "
            "AND progress_stage = 'completed' "
            "AND total_pages IS NOT NULL "
            "AND total_pages = completed_pages "
            "AND CASE WHEN output_binding ? 'page_count' "
            "AND jsonb_typeof(output_binding -> 'page_count') = 'number' "
            "AND (output_binding ->> 'page_count') ~ '^[1-9][0-9]*$' "
            "THEN CASE WHEN (output_binding ->> 'page_count')::numeric "
            "<= 2147483647 THEN total_pages = "
            "(output_binding ->> 'page_count')::integer ELSE FALSE END "
            "ELSE FALSE END)"
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


class StudySession(Base):
    __tablename__ = "study_sessions"
    __table_args__ = (
        UniqueConstraint("learner_id", "idempotency_key_sha256"),
        UniqueConstraint(
            "study_session_id",
            "knowledge_map_revision",
            name="study_sessions_map_binding_unique",
        ),
        ForeignKeyConstraint(
            ["learner_id", "material_id", "knowledge_map_revision"],
            ["knowledge_maps.learner_id", "knowledge_maps.material_id", "knowledge_maps.map_revision"],
        ),
        CheckConstraint("octet_length(idempotency_key_sha256) = 32"),
        CheckConstraint("octet_length(request_fingerprint) = 32"),
        CheckConstraint(
            "current_formal_concept_id IS NULL OR "
            "current_formal_concept_id ~ '^formal-concept:sha256:[0-9a-f]{64}$'"
        ),
        CheckConstraint(
            "deferred_formal_concept_id IS NULL OR "
            "deferred_formal_concept_id ~ '^formal-concept:sha256:[0-9a-f]{64}$'"
        ),
        CheckConstraint(
            "deferred_formal_concept_id IS NULL OR "
            "current_formal_concept_id IS NULL OR "
            "deferred_formal_concept_id <> current_formal_concept_id"
        ),
        CheckConstraint("status IN ('active', 'completed')"),
        CheckConstraint("last_event_number >= 0"),
        CheckConstraint(
            "(status = 'active' AND completed_at IS NULL) OR "
            "(status = 'completed' AND completed_at IS NOT NULL "
            "AND completed_at >= started_at)"
        ),
    )

    study_session_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )
    learner_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    material_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    knowledge_map_revision: Mapped[str] = mapped_column(Text, nullable=False)
    current_formal_concept_id: Mapped[str | None] = mapped_column(Text)
    deferred_formal_concept_id: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    request_fingerprint: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_event_number: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )


class Assessment(Base):
    __tablename__ = "assessments"
    __table_args__ = (
        UniqueConstraint("study_session_id", "question_id"),
        UniqueConstraint(
            "study_session_id",
            "assessment_revision",
            "question_id",
            name="assessments_session_revision_question_unique",
        ),
        UniqueConstraint("study_session_id", "request_idempotency_key_sha256"),
        ForeignKeyConstraint(
            ["study_session_id", "knowledge_map_revision"],
            [
                "study_sessions.study_session_id",
                "study_sessions.knowledge_map_revision",
            ],
        ),
        CheckConstraint(
            "assessment_revision ~ '^assessment:sha256:[0-9a-f]{64}$'"
        ),
        CheckConstraint("question_id ~ '^question:sha256:[0-9a-f]{64}$'"),
        CheckConstraint(
            "target_formal_concept_id ~ '^formal-concept:sha256:[0-9a-f]{64}$'"
        ),
        CheckConstraint("target_claim_id ~ '^claim:sha256:[0-9a-f]{64}$'"),
        CheckConstraint(
            "policy_revision = 'single-choice-assessment-policy/v1'"
        ),
        CheckConstraint(
            "jsonb_typeof(public_document) = 'object' "
            "AND public_document ?& ARRAY['schema', 'study_session_id', "
            "'knowledge_map_revision', 'assessment_revision', 'question_id', "
            "'target_formal_concept_id', 'target_claim_id', "
            "'source_evidence_ids', 'question_type', 'prompt', 'options', "
            "'policy_revision'] "
            "AND public_document ->> 'schema' = "
            "'single-choice-assessment-public/v1' "
            "AND public_document ->> 'study_session_id' = study_session_id::text "
            "AND public_document ->> 'knowledge_map_revision' = knowledge_map_revision "
            "AND public_document ->> 'assessment_revision' = assessment_revision "
            "AND public_document ->> 'question_id' = question_id "
            "AND public_document ->> 'target_formal_concept_id' = target_formal_concept_id "
            "AND public_document ->> 'target_claim_id' = target_claim_id "
            "AND public_document ->> 'question_type' = 'single_choice' "
            "AND public_document ->> 'policy_revision' = policy_revision"
        ),
        CheckConstraint(
            "jsonb_typeof(private_answer_document) = 'object' "
            "AND private_answer_document ?& ARRAY['schema', "
            "'assessment_revision', 'question_id', 'correct_option_id', "
            "'rationale', 'source_evidence_ids', 'private_answer_sha256'] "
            "AND private_answer_document ->> 'schema' = "
            "'single-choice-assessment-answer/v1' "
            "AND private_answer_document ->> 'assessment_revision' = "
            "assessment_revision "
            "AND private_answer_document ->> 'question_id' = question_id"
        ),
        CheckConstraint(
            "generation_provenance IS NULL OR ("
            "jsonb_typeof(generation_provenance) = 'object' "
            "AND generation_provenance ->> 'schema' = "
            "'assessment-generation-provenance/v2' "
            "AND generation_provenance ->> 'assessment_revision' = "
            "assessment_revision "
            "AND generation_provenance ->> 'question_id' = question_id)"
        ),
        CheckConstraint(
            "(request_idempotency_key_sha256 IS NULL AND "
            "request_fingerprint IS NULL) OR "
            "(octet_length(request_idempotency_key_sha256) = 32 AND "
            "octet_length(request_fingerprint) = 32)"
        ),
    )

    assessment_revision: Mapped[str] = mapped_column(Text, primary_key=True)
    study_session_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    knowledge_map_revision: Mapped[str] = mapped_column(Text, nullable=False)
    question_id: Mapped[str] = mapped_column(Text, nullable=False)
    target_formal_concept_id: Mapped[str] = mapped_column(Text, nullable=False)
    target_claim_id: Mapped[str] = mapped_column(Text, nullable=False)
    public_document: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    private_answer_document: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False
    )
    generation_provenance: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    request_idempotency_key_sha256: Mapped[bytes | None] = mapped_column(
        LargeBinary
    )
    request_fingerprint: Mapped[bytes | None] = mapped_column(LargeBinary)
    policy_revision: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class AnswerEvent(Base):
    __tablename__ = "answer_events"
    __table_args__ = (
        UniqueConstraint("study_session_id", "assessment_revision"),
        UniqueConstraint("study_session_id", "event_number"),
        UniqueConstraint("study_session_id", "idempotency_key_sha256"),
        ForeignKeyConstraint(
            ["study_session_id", "knowledge_map_revision"],
            [
                "study_sessions.study_session_id",
                "study_sessions.knowledge_map_revision",
            ],
        ),
        ForeignKeyConstraint(
            ["study_session_id", "assessment_revision", "question_id"],
            [
                "assessments.study_session_id",
                "assessments.assessment_revision",
                "assessments.question_id",
            ],
        ),
        CheckConstraint(
            "assessment_revision ~ '^assessment:sha256:[0-9a-f]{64}$'"
        ),
        CheckConstraint("question_id ~ '^question:sha256:[0-9a-f]{64}$'"),
        CheckConstraint(
            "target_formal_concept_id ~ '^formal-concept:sha256:[0-9a-f]{64}$'"
        ),
        CheckConstraint("target_claim_id ~ '^claim:sha256:[0-9a-f]{64}$'"),
        CheckConstraint("selected_option_id ~ '^option:sha256:[0-9a-f]{64}$'"),
        CheckConstraint("event_number >= 1"),
        CheckConstraint("octet_length(idempotency_key_sha256) = 32"),
        CheckConstraint("octet_length(request_fingerprint) = 32"),
    )

    answer_event_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )
    study_session_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    material_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    knowledge_map_revision: Mapped[str] = mapped_column(Text, nullable=False)
    assessment_revision: Mapped[str] = mapped_column(Text, nullable=False)
    question_id: Mapped[str] = mapped_column(Text, nullable=False)
    target_formal_concept_id: Mapped[str] = mapped_column(Text, nullable=False)
    target_claim_id: Mapped[str] = mapped_column(Text, nullable=False)
    selected_option_id: Mapped[str] = mapped_column(Text, nullable=False)
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False)
    event_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    idempotency_key_sha256: Mapped[bytes] = mapped_column(
        LargeBinary, nullable=False
    )
    request_fingerprint: Mapped[bytes] = mapped_column(
        LargeBinary, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


@contextmanager
def database_session(dsn: str | None = None) -> Generator[Session, None, None]:
    """每個 public operation 使用獨立 transaction，不保留全域 Session。"""

    resolved_dsn = resolve_database_dsn(dsn)
    engine = create_engine(
        "postgresql+psycopg://",
        creator=lambda: psycopg.connect(resolved_dsn),
        poolclass=NullPool,
        hide_parameters=True,
    )
    try:
        with Session(engine, expire_on_commit=False) as session:
            with session.begin():
                yield session
    finally:
        engine.dispose()


@contextmanager
def deferred_artifact_session(
    dsn: str | None = None,
) -> Generator[Session, None, None]:
    """建立 source material 與 artifact 時延後檢查循環 FK。"""

    with database_session(dsn) as session:
        session.execute(text("SET CONSTRAINTS materials_source_artifact_fk DEFERRED"))
        yield session
