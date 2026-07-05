from enum import Enum
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Enum as SQLEnum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class RelationType(str, Enum):
    prerequisite = "prerequisite"
    contains = "contains"
    similar = "similar"
    confusing = "confusing"
    application = "application"
    example = "example"


class EvidenceType(str, Enum):
    quote = "quote"
    summary = "summary"
    page_reference = "page_reference"
    inferred = "inferred"


relation_type_enum = SQLEnum(RelationType, name="relation_type")
evidence_type_enum = SQLEnum(EvidenceType, name="evidence_type")


class Material(Base):
    __tablename__ = "materials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(100), nullable=False)
    chapter_range: Mapped[Optional[str]] = mapped_column(String(255))


class MaterialBlock(Base):
    __tablename__ = "material_blocks"
    __table_args__ = (
        UniqueConstraint("material_id", "block_index", name="uq_material_blocks_material_block_index"),
        CheckConstraint("block_index >= 0", name="ck_material_blocks_block_index_nonnegative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id", ondelete="CASCADE"), nullable=False)
    block_index: Mapped[int] = mapped_column(Integer, nullable=False)
    page_number: Mapped[Optional[int]] = mapped_column(Integer)
    block_type: Mapped[Optional[str]] = mapped_column(String(50))
    content: Mapped[str] = mapped_column(Text, nullable=False)


class Concept(Base):
    __tablename__ = "concepts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    description: Mapped[Optional[str]] = mapped_column(Text)
    score_value: Mapped[Optional[float]] = mapped_column(Numeric(5, 2))
    score_level: Mapped[Optional[str]] = mapped_column(String(50))
    score_detail: Mapped[Optional[dict]] = mapped_column(JSONB)
    score_reason: Mapped[Optional[str]] = mapped_column(Text)
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    exclude_reason: Mapped[Optional[str]] = mapped_column(Text)


class ConceptRelation(Base):
    __tablename__ = "concept_relations"
    __table_args__ = (
        UniqueConstraint("source_concept_id", "target_concept_id", "relation_type", name="uq_concept_relations_edge"),
        CheckConstraint("source_concept_id <> target_concept_id", name="ck_concept_relations_no_self_edge"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_concept_id: Mapped[int] = mapped_column(ForeignKey("concepts.id", ondelete="CASCADE"), nullable=False)
    target_concept_id: Mapped[int] = mapped_column(ForeignKey("concepts.id", ondelete="CASCADE"), nullable=False)
    relation_type: Mapped[RelationType] = mapped_column(relation_type_enum, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    score_value: Mapped[Optional[float]] = mapped_column(Numeric(5, 2))
    score_level: Mapped[Optional[str]] = mapped_column(String(50))
    score_detail: Mapped[Optional[dict]] = mapped_column(JSONB)
    score_reason: Mapped[Optional[str]] = mapped_column(Text)
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    exclude_reason: Mapped[Optional[str]] = mapped_column(Text)


class Evidence(Base):
    __tablename__ = "evidence"
    __table_args__ = (
        CheckConstraint(
            "(concept_id IS NOT NULL AND relation_id IS NULL) OR "
            "(concept_id IS NULL AND relation_id IS NOT NULL)",
            name="ck_evidence_exactly_one_target",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id", ondelete="CASCADE"), nullable=False)
    block_id: Mapped[Optional[int]] = mapped_column(ForeignKey("material_blocks.id", ondelete="SET NULL"))
    concept_id: Mapped[Optional[int]] = mapped_column(ForeignKey("concepts.id", ondelete="CASCADE"))
    relation_id: Mapped[Optional[int]] = mapped_column(ForeignKey("concept_relations.id", ondelete="CASCADE"))
    page_number: Mapped[Optional[int]] = mapped_column(Integer)
    quote_text: Mapped[Optional[str]] = mapped_column(Text)
    evidence_type: Mapped[EvidenceType] = mapped_column(evidence_type_enum, nullable=False)
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSONB)


class LearningPath(Base):
    __tablename__ = "learning_paths"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)


class LearningPathNode(Base):
    __tablename__ = "learning_path_nodes"
    __table_args__ = (
        UniqueConstraint("learning_path_id", "position", name="uq_learning_path_nodes_path_position"),
        UniqueConstraint("learning_path_id", "concept_id", name="uq_learning_path_nodes_path_concept"),
        CheckConstraint("position > 0", name="ck_learning_path_nodes_position_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    learning_path_id: Mapped[int] = mapped_column(ForeignKey("learning_paths.id", ondelete="CASCADE"), nullable=False)
    concept_id: Mapped[int] = mapped_column(ForeignKey("concepts.id", ondelete="CASCADE"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
