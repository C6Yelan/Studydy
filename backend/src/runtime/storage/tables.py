from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Double,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    LargeBinary,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import (
    ARRAY,
    JSONB,
    TSVECTOR,
    UUID as PostgreSQLUUID,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .evidence_json import EvidenceJSONB


class Base(DeclarativeBase):
    """ORM 映射；資料表 DDL 以 migration 為準。"""


class Learner(Base):
    __tablename__ = "learners"

    learner_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    email: Mapped[str | None] = mapped_column(Text, unique=True)
    password_hash: Mapped[str | None] = mapped_column(Text)
    # 只在個人檔案讀寫時載入；登入與 session 解析不需要名稱。
    display_name: Mapped[str | None] = mapped_column(Text, deferred=True)


class LearnerSession(Base):
    __tablename__ = "learner_sessions"

    session_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )
    learner_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("learners.learner_id"), nullable=False
    )
    token_sha256: Mapped[bytes] = mapped_column(
        LargeBinary, unique=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    idle_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    absolute_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # 0013：登入時由 User-Agent 解析的「瀏覽器 · 系統」與最後使用時間；舊 session 為 NULL。
    device_label: Mapped[str | None] = mapped_column(Text)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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

    pending_goal: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    material_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )
    learner_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("learners.learner_id"), nullable=False
    )
    source_artifact_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), unique=True
    )
    head_revision: Mapped[str | None] = mapped_column(Text)
    starting_level: Mapped[str | None] = mapped_column(Text)
    upload_idempotency_key_sha256: Mapped[bytes] = mapped_column(
        LargeBinary, nullable=False
    )
    upload_request_fingerprint: Mapped[bytes] = mapped_column(
        LargeBinary, nullable=False
    )
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    discard_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint("learner_id", "material_id", "artifact_id"),
        ForeignKeyConstraint(
            ["learner_id", "material_id"],
            ["materials.learner_id", "materials.material_id"],
        ),
    )

    artifact_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )
    learner_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    material_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    media_type: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


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
    learner_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    material_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    source_artifact_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    idempotency_key_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    request_fingerprint: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    input_source_set_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    bundle_manifest: Mapped[dict] = mapped_column(JSONB, nullable=False)
    bundle_manifest_sha256: Mapped[str] = mapped_column(Text, nullable=False)

    @property
    def learning_scopes(self):
        return (self.bundle_manifest or {}).get("learning_scopes", {})

    base_revision: Mapped[str | None] = mapped_column(Text)
    worker_token: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    auto_retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    auto_retry_reason: Mapped[str | None] = mapped_column(Text)
    display_summary: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    maintenance: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    runtime_binding: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    runtime_lock_document: Mapped[dict[str, Any]] = mapped_column(
        JSONB(none_as_null=True), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    progress_stage: Mapped[str] = mapped_column(Text, nullable=False)
    completed_pages: Mapped[int] = mapped_column(Integer, nullable=False)
    total_pages: Mapped[int | None] = mapped_column(Integer)
    completed_units: Mapped[int | None] = mapped_column(Integer)
    total_units: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(Text)
    output_binding: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )


class KnowledgeStructure(Base):
    __tablename__ = "knowledge_structures"
    __table_args__ = (
        UniqueConstraint("learner_id", "material_id", "run_id"),
        ForeignKeyConstraint(
            ["learner_id", "material_id", "run_id"],
            [
                "material_processing_runs.learner_id",
                "material_processing_runs.material_id",
                "material_processing_runs.run_id",
            ],
        ),
    )

    learner_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )
    material_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )
    structure_revision: Mapped[str] = mapped_column(Text, primary_key=True)
    run_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    document: Mapped[dict[str, Any]] = mapped_column(EvidenceJSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class MaterialRetrievalIndex(Base):
    __tablename__ = "material_retrieval_indexes"
    learner_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )
    material_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )
    structure_revision: Mapped[str] = mapped_column(Text, primary_key=True)
    policy: Mapped[str] = mapped_column(Text)
    passage_count: Mapped[int] = mapped_column(Integer)
    content_sha256: Mapped[str] = mapped_column(Text)
    outline: Mapped[list] = mapped_column(EvidenceJSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MaterialPassage(Base):
    __tablename__ = "material_passages"
    learner_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )
    material_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )
    structure_revision: Mapped[str] = mapped_column(Text, primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    evidence_id: Mapped[str] = mapped_column(Text)
    source_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    page: Mapped[int] = mapped_column(Integer)
    section_id: Mapped[str] = mapped_column(Text)
    concept_ids: Mapped[list[str]] = mapped_column(ARRAY(Text))
    payload: Mapped[dict] = mapped_column(EvidenceJSONB)
    keywords: Mapped[Any] = mapped_column(TSVECTOR)


class KnowledgeReadIndex(Base):
    __tablename__ = "knowledge_read_indexes"
    learner_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )
    material_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )
    structure_revision: Mapped[str] = mapped_column(Text, primary_key=True)
    # 不隨 ORM metadata 讀取一併解碼；各讀取入口明確選擇所需欄位。
    outline: Mapped[dict] = mapped_column(EvidenceJSONB, deferred=True)
    outline_sha256: Mapped[str] = mapped_column(Text)
    learning: Mapped[dict] = mapped_column(EvidenceJSONB, deferred=True)
    learning_sha256: Mapped[str] = mapped_column(Text)


class KnowledgeEvidenceRef(Base):
    __tablename__ = "knowledge_evidence_refs"
    learner_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )
    material_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )
    structure_revision: Mapped[str] = mapped_column(Text, primary_key=True)
    evidence_id: Mapped[str] = mapped_column(Text, primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    content_sha256: Mapped[str] = mapped_column(Text)


class CardSourceIndex(Base):
    __tablename__ = "card_source_indexes"
    learner_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )
    material_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )
    structure_revision: Mapped[str] = mapped_column(Text, primary_key=True)
    metadata_: Mapped[dict] = mapped_column("metadata", EvidenceJSONB)
    content_sha256: Mapped[str] = mapped_column(Text)


class CardSourceConcept(Base):
    __tablename__ = "card_source_concepts"
    learner_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )
    material_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )
    structure_revision: Mapped[str] = mapped_column(Text, primary_key=True)
    concept_id: Mapped[str] = mapped_column(Text, primary_key=True)
    position: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(EvidenceJSONB)
    content_sha256: Mapped[str] = mapped_column(Text)


class CardSet(Base):
    __tablename__ = "card_sets"
    __table_args__ = (
        UniqueConstraint("learner_id", "material_id", "idempotency_key_sha256"),
        ForeignKeyConstraint(
            ["learner_id", "material_id"],
            ["materials.learner_id", "materials.material_id"],
        ),
        ForeignKeyConstraint(
            ["learner_id", "material_id", "knowledge_structure_revision"],
            [
                "knowledge_structures.learner_id",
                "knowledge_structures.material_id",
                "knowledge_structures.structure_revision",
            ],
        ),
    )

    card_set_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )
    learner_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    material_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    knowledge_structure_revision: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    concept_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    card_count: Mapped[int | None] = mapped_column(Integer)
    page_counts: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("1")
    )
    idempotency_key_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    request_fingerprint: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'ready'")
    )
    generation: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    lease_token: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Podcast(Base):
    __tablename__ = "podcasts"
    __table_args__ = (
        UniqueConstraint("learner_id", "material_id", "idempotency_key_sha256"),
        ForeignKeyConstraint(
            ["learner_id", "material_id"],
            ["materials.learner_id", "materials.material_id"],
        ),
        ForeignKeyConstraint(
            ["learner_id", "material_id", "knowledge_structure_revision"],
            [
                "knowledge_structures.learner_id",
                "knowledge_structures.material_id",
                "knowledge_structures.structure_revision",
            ],
        ),
    )

    podcast_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )
    learner_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    material_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    knowledge_structure_revision: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    concept_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("1")
    )
    idempotency_key_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    request_fingerprint: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    episodes: Mapped[list[dict]] = mapped_column(EvidenceJSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    error_code: Mapped[str | None] = mapped_column(Text)
    lease_token: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 建立時勾選才在每集音訊完成後自動生成影片（0020）。
    video_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)


class StudySession(Base):
    __tablename__ = "study_sessions"
    __table_args__ = (
        UniqueConstraint("learner_id", "idempotency_key_sha256"),
        UniqueConstraint("study_session_id", "knowledge_structure_revision"),
        ForeignKeyConstraint(
            ["learner_id", "material_id", "knowledge_structure_revision"],
            [
                "knowledge_structures.learner_id",
                "knowledge_structures.material_id",
                "knowledge_structures.structure_revision",
            ],
        ),
    )

    study_session_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )
    learner_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    material_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    knowledge_structure_revision: Mapped[str] = mapped_column(Text, nullable=False)
    current_concept_id: Mapped[str | None] = mapped_column(Text)
    no_safe_claim_ids: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("ARRAY[]::text[]")
    )
    deferred_concept_ids: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("ARRAY[]::text[]")
    )
    last_applied_guidance_revision: Mapped[str | None] = mapped_column(Text)
    last_applied_progress_sha256: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    request_fingerprint: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    start_receipts: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    last_event_number: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )


class Assessment(Base):
    __tablename__ = "assessments"
    __table_args__ = (
        UniqueConstraint("study_session_id", "question_id"),
        UniqueConstraint("study_session_id", "semantic_identity"),
        UniqueConstraint("study_session_id", "request_idempotency_key_sha256"),
        ForeignKeyConstraint(
            ["study_session_id", "knowledge_structure_revision"],
            [
                "study_sessions.study_session_id",
                "study_sessions.knowledge_structure_revision",
            ],
        ),
    )

    assessment_revision: Mapped[str] = mapped_column(Text, primary_key=True)
    study_session_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    knowledge_structure_revision: Mapped[str] = mapped_column(Text, nullable=False)
    question_id: Mapped[str] = mapped_column(Text, nullable=False)
    semantic_identity: Mapped[str] = mapped_column(Text, nullable=False)
    learning_angle: Mapped[str] = mapped_column(Text, nullable=False)
    target_concept_id: Mapped[str] = mapped_column(Text, nullable=False)
    target_claim_id: Mapped[str] = mapped_column(Text, nullable=False)
    public_document: Mapped[dict[str, Any]] = mapped_column(
        EvidenceJSONB, nullable=False
    )
    private_answer_document: Mapped[dict[str, Any]] = mapped_column(
        EvidenceJSONB, nullable=False
    )
    generation_provenance: Mapped[dict[str, Any]] = mapped_column(
        EvidenceJSONB, nullable=False
    )
    mastery_qualified: Mapped[bool] = mapped_column(Boolean, nullable=False)
    request_idempotency_key_sha256: Mapped[bytes] = mapped_column(
        LargeBinary, nullable=False
    )
    request_fingerprint: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class AssessmentSet(Base):
    __tablename__ = "assessment_sets"

    set_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    learner_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    material_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    study_session_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    knowledge_structure_revision: Mapped[str] = mapped_column(Text, nullable=False)
    target_concept_id: Mapped[str | None] = mapped_column(Text)
    diagnostic_set_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    target_plan: Mapped[dict] = mapped_column(JSONB, nullable=False)
    requested_count: Mapped[int] = mapped_column(Integer, nullable=False)
    runtime_lock_document: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    set_version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    idempotency_key_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    request_fingerprint: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    action_receipts: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    lease_token: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    sealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AssessmentSetItem(Base):
    __tablename__ = "assessment_set_items"

    set_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    study_session_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    knowledge_structure_revision: Mapped[str] = mapped_column(Text, nullable=False)
    target_concept_id: Mapped[str] = mapped_column(Text, nullable=False)
    target_claim_id: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    prepared_document: Mapped[dict | None] = mapped_column(
        EvidenceJSONB(none_as_null=True)
    )
    assessment_revision: Mapped[str | None] = mapped_column(Text)


class AnswerEvent(Base):
    __tablename__ = "answer_events"
    __table_args__ = (
        UniqueConstraint("study_session_id", "assessment_revision"),
        UniqueConstraint("study_session_id", "event_number"),
        UniqueConstraint("study_session_id", "idempotency_key_sha256"),
        ForeignKeyConstraint(
            ["study_session_id", "knowledge_structure_revision"],
            [
                "study_sessions.study_session_id",
                "study_sessions.knowledge_structure_revision",
            ],
        ),
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
    knowledge_structure_revision: Mapped[str] = mapped_column(Text, nullable=False)
    assessment_revision: Mapped[str] = mapped_column(
        Text, ForeignKey("assessments.assessment_revision"), nullable=False
    )
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class MaterialSource(Base):
    __tablename__ = "material_sources"
    source_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )
    learner_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    material_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    original_artifact_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    original_name: Mapped[str] = mapped_column(Text)
    media_type: Mapped[str] = mapped_column(Text)
    analysis_scope: Mapped[dict | None] = mapped_column(EvidenceJSONB)
    idempotency_key_sha256: Mapped[bytes] = mapped_column(LargeBinary)
    request_fingerprint: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SourceNormalization(Base):
    __tablename__ = "source_normalizations"
    normalization_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )
    learner_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    material_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    source_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    policy: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    normalized_artifact_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True)
    )
    mapping_artifact_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True)
    )
    page_count: Mapped[int | None] = mapped_column(Integer)
    analysis_scope: Mapped[dict | None] = mapped_column(EvidenceJSONB)
    error_code: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MaterialSourceSet(Base):
    __tablename__ = "material_source_sets"
    source_set_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )
    learner_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    material_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    manifest: Mapped[dict] = mapped_column(EvidenceJSONB)
    digest: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MaterialSourceSetItem(Base):
    __tablename__ = "material_source_set_items"
    source_set_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    learner_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    material_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    source_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    normalization_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))


class VoiceConversation(Base):
    __tablename__ = "voice_conversations"
    conversation_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )
    learner_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    material_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    knowledge_structure_revision: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    memory: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idempotency_key_sha256: Mapped[bytes] = mapped_column(LargeBinary)


class VoiceTurn(Base):
    __tablename__ = "voice_turns"
    turn_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )
    conversation_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    request_key: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(Text)
    mode: Mapped[str | None] = mapped_column(Text)
    context: Mapped[dict | None] = mapped_column(JSONB)
    question: Mapped[str] = mapped_column(Text)
    transcription: Mapped[dict | None] = mapped_column(JSONB)
    answer: Mapped[dict | None] = mapped_column(EvidenceJSONB)
    status: Mapped[str] = mapped_column(Text)
    recording: Mapped[bytes | None] = mapped_column(LargeBinary)
    audio: Mapped[bytes | None] = mapped_column(LargeBinary)
    error_code: Mapped[str | None] = mapped_column(Text)
    lease_token: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MaterialResearch(Base):
    __tablename__ = "material_research"
    research_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )
    learner_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    material_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    base_revision: Mapped[str | None] = mapped_column(Text)
    query: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    candidates: Mapped[list] = mapped_column(EvidenceJSONB)
    selection: Mapped[list] = mapped_column(JSONB)
    search_query: Mapped[str | None] = mapped_column(Text)
    cursor: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    run_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    lease_token: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idempotency_key_sha256: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    staged_content: Mapped[bytes | None] = mapped_column(LargeBinary)
    staged_metadata: Mapped[dict | None] = mapped_column(JSONB)
    topic_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    search_state: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )


class ResearchDocument(Base):
    __tablename__ = "material_research_documents"
    research_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )
    sha256: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    content: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)


class TopicScope(Base):
    __tablename__ = "topic_scopes"
    starting_level: Mapped[str | None] = mapped_column(Text)
    learning_goal: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    topic_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )
    learner_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    material_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    base_revision: Mapped[str | None] = mapped_column(Text)
    request: Mapped[str] = mapped_column(Text)
    proposal: Mapped[dict | None] = mapped_column(EvidenceJSONB)
    generation_provider: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(Text)
    research_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    approved_sha256: Mapped[str | None] = mapped_column(Text)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approval_key_sha256: Mapped[bytes | None] = mapped_column(LargeBinary)
    idempotency_key_sha256: Mapped[bytes] = mapped_column(LargeBinary)
    lease_token: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PodcastScenes(Base):
    __tablename__ = "podcast_scenes"
    scene_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )
    podcast_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    episode_index: Mapped[int] = mapped_column(Integer)
    input_sha256: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    alignment: Mapped[dict | None] = mapped_column(EvidenceJSONB)
    checks: Mapped[dict | None] = mapped_column(EvidenceJSONB)
    manifest: Mapped[dict | None] = mapped_column(EvidenceJSONB)
    error_code: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(BigInteger)
    lease_token: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PodcastVideo(Base):
    __tablename__ = "podcast_videos"
    video_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )
    podcast_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    episode_index: Mapped[int] = mapped_column(Integer)
    source_sha256: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    artifact_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    manifest: Mapped[dict | None] = mapped_column(EvidenceJSONB)
    error_code: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(BigInteger)
    lease_token: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CardReviewEvent(Base):
    __tablename__ = "card_review_events"
    review_event_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )
    learner_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    card_set_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("card_sets.card_set_id")
    )
    concept_id: Mapped[str] = mapped_column(Text)
    rating: Mapped[str] = mapped_column(Text)
    review_count: Mapped[int] = mapped_column(Integer)
    idempotency_key_sha256: Mapped[bytes] = mapped_column(LargeBinary)
    request_fingerprint: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CardReviewState(Base):
    __tablename__ = "card_review_states"
    card_set_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("card_sets.card_set_id"), primary_key=True
    )
    concept_id: Mapped[str] = mapped_column(Text, primary_key=True)
    learner_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    review_count: Mapped[int] = mapped_column(Integer)
    last_rating: Mapped[str] = mapped_column(Text)
    last_reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PodcastEpisodeProgress(Base):
    __tablename__ = "podcast_episode_progress"
    podcast_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("podcasts.podcast_id"), primary_key=True
    )
    episode_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    learner_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    position_seconds: Mapped[float] = mapped_column(Double)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PodcastListeningDay(Base):
    __tablename__ = "podcast_listening_days"
    podcast_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("podcasts.podcast_id"), primary_key=True
    )
    episode_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    listened_on: Mapped[date] = mapped_column(Date, primary_key=True)
    learner_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    listened_seconds: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MaterialVersionState(Base):
    __tablename__ = "material_version_state"
    __table_args__ = (ForeignKeyConstraint(["learner_id", "material_id"], ["materials.learner_id", "materials.material_id"], ondelete="CASCADE"),)
    learner_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    material_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    target_revision: Mapped[str | None] = mapped_column(Text)
    prior_revision: Mapped[str | None] = mapped_column(Text)
    previous: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    notice_dismissed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    automatic_status: Mapped[str | None] = mapped_column(Text)
    automatic_move_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    automatic_error: Mapped[str | None] = mapped_column(Text)


class MaterialVersionMove(Base):
    """學生確認的「移到新版」請求；result 為重送時原樣回傳的結果。"""

    __tablename__ = "material_version_moves"
    __table_args__ = (
        ForeignKeyConstraint(
            ["learner_id", "material_id"],
            ["materials.learner_id", "materials.material_id"],
        ),
    )
    move_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    learner_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    material_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    to_revision: Mapped[str] = mapped_column(Text)
    idempotency_key_sha256: Mapped[bytes] = mapped_column(LargeBinary)
    request_fingerprint: Mapped[bytes] = mapped_column(LargeBinary)
    result: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CardSetMove(Base):
    __tablename__ = "card_set_moves"
    target_card_set_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("card_sets.card_set_id"), primary_key=True
    )
    source_card_set_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("card_sets.card_set_id")
    )
    move_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("material_version_moves.move_id")
    )
    learner_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    # {新版概念: 承接排程與卡片身分的舊版概念}
    concept_map: Mapped[dict] = mapped_column(JSONB)


class StudySessionMove(Base):
    __tablename__ = "study_session_moves"
    target_study_session_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("study_sessions.study_session_id"),
        primary_key=True,
    )
    source_study_session_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("study_sessions.study_session_id")
    )
    move_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("material_version_moves.move_id")
    )
    learner_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    completed_concept_ids: Mapped[list[str]] = mapped_column(ARRAY(Text))


class KnowledgeChapters(Base):
    """知識結構版本的章節分區（衍生資料）兼背景佇列；DDL 見 0012。"""

    __tablename__ = "knowledge_chapters"
    __table_args__ = (
        ForeignKeyConstraint(
            ["learner_id", "material_id", "structure_revision"],
            [
                "knowledge_structures.learner_id",
                "knowledge_structures.material_id",
                "knowledge_structures.structure_revision",
            ],
        ),
    )
    learner_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    material_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    structure_revision: Mapped[str] = mapped_column(Text, primary_key=True)
    status: Mapped[str] = mapped_column(Text)
    policy: Mapped[str] = mapped_column(Text)
    chapters: Mapped[list | None] = mapped_column(JSONB(none_as_null=True))
    prompt_sha256: Mapped[str] = mapped_column(Text)
    model_id: Mapped[str | None] = mapped_column(Text)
    failed_windows: Mapped[list] = mapped_column(JSONB)
    calls: Mapped[int] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(Text)
    lease_token: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LearningGoal(Base):
    __tablename__ = "learning_goals"
    material_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    learner_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    target_date: Mapped[date] = mapped_column(Date, nullable=False)
    target_percent: Mapped[int] = mapped_column(Integer, nullable=False)
    baseline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    baseline_revision: Mapped[str] = mapped_column(Text, nullable=False)
    baseline_assessable_points: Mapped[int] = mapped_column(Integer, nullable=False)
    baseline_completed_points: Mapped[int] = mapped_column(Integer, nullable=False)
    baseline_unavailable_claim_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    notice_revision: Mapped[str] = mapped_column(Text, nullable=False)
    notice_points: Mapped[int] = mapped_column(Integer, nullable=False)
    daily_date: Mapped[date] = mapped_column(Date, nullable=False)
    daily_unavailable_claim_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    daily_completed_points: Mapped[int] = mapped_column(Integer, nullable=False)
    daily_quota_points: Mapped[int] = mapped_column(Integer, nullable=False)
    # 第一次設定目標的時間，修改目標不變（成就「立下目標」）。
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MaterialNotes(Base):
    __tablename__ = "material_notes"
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    learner_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    material_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    structure_revision: Mapped[str] = mapped_column(Text, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    status: Mapped[str] = mapped_column(Text)
    generation: Mapped[int] = mapped_column(Integer)
    paragraphs: Mapped[list | None] = mapped_column(JSONB(none_as_null=True))
    runtime_lock: Mapped[dict] = mapped_column(JSONB)
    calls: Mapped[int] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(Text)
    section_plan: Mapped[list | None] = mapped_column(JSONB(none_as_null=True))
    section_results: Mapped[list | None] = mapped_column(JSONB(none_as_null=True))
    progress_stage: Mapped[str | None] = mapped_column(Text)
    generation_diagnostics: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    lease_token: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NoteAnnotation(Base):
    __tablename__ = "note_annotations"
    annotation_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    learner_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    material_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(Text)
    structure_revision: Mapped[str] = mapped_column(Text)
    anchor: Mapped[str] = mapped_column(Text)
    paragraph_snapshot: Mapped[dict] = mapped_column(JSONB)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
