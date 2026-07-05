"""initial schema

Revision ID: 202607060001
Revises:
Create Date: 2026-07-06
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "202607060001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

relation_type = postgresql.ENUM(
    "prerequisite",
    "contains",
    "similar",
    "confusing",
    "application",
    "example",
    name="relation_type",
    create_type=False,
)

evidence_type = postgresql.ENUM(
    "quote",
    "summary",
    "page_reference",
    "inferred",
    name="evidence_type",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    relation_type.create(bind, checkfirst=True)
    evidence_type.create(bind, checkfirst=True)

    op.create_table(
        "materials",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=100), nullable=False),
        sa.Column("chapter_range", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "concepts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("score_value", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("score_level", sa.String(length=50), nullable=True),
        sa.Column("score_detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("score_reason", sa.Text(), nullable=True),
        sa.Column("needs_review", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("exclude_reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    op.create_table(
        "material_blocks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("material_id", sa.Integer(), nullable=False),
        sa.Column("block_index", sa.Integer(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("block_type", sa.String(length=50), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.CheckConstraint("block_index >= 0", name="ck_material_blocks_block_index_nonnegative"),
        sa.ForeignKeyConstraint(["material_id"], ["materials.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("material_id", "block_index", name="uq_material_blocks_material_block_index"),
    )

    op.create_table(
        "concept_relations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_concept_id", sa.Integer(), nullable=False),
        sa.Column("target_concept_id", sa.Integer(), nullable=False),
        sa.Column("relation_type", relation_type, nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("score_value", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("score_level", sa.String(length=50), nullable=True),
        sa.Column("score_detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("score_reason", sa.Text(), nullable=True),
        sa.Column("needs_review", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("exclude_reason", sa.Text(), nullable=True),
        sa.CheckConstraint("source_concept_id <> target_concept_id", name="ck_concept_relations_no_self_edge"),
        sa.ForeignKeyConstraint(["source_concept_id"], ["concepts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_concept_id"], ["concepts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_concept_id", "target_concept_id", "relation_type", name="uq_concept_relations_edge"),
    )

    op.create_table(
        "learning_paths",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("material_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(["material_id"], ["materials.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "evidence",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("material_id", sa.Integer(), nullable=False),
        sa.Column("block_id", sa.Integer(), nullable=True),
        sa.Column("concept_id", sa.Integer(), nullable=True),
        sa.Column("relation_id", sa.Integer(), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("quote_text", sa.Text(), nullable=True),
        sa.Column("evidence_type", evidence_type, nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint(
            "(concept_id IS NOT NULL AND relation_id IS NULL) OR "
            "(concept_id IS NULL AND relation_id IS NOT NULL)",
            name="ck_evidence_exactly_one_target",
        ),
        sa.ForeignKeyConstraint(["block_id"], ["material_blocks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["concept_id"], ["concepts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["material_id"], ["materials.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["relation_id"], ["concept_relations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "learning_path_nodes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("learning_path_id", sa.Integer(), nullable=False),
        sa.Column("concept_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.CheckConstraint("position > 0", name="ck_learning_path_nodes_position_positive"),
        sa.ForeignKeyConstraint(["concept_id"], ["concepts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["learning_path_id"], ["learning_paths.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("learning_path_id", "concept_id", name="uq_learning_path_nodes_path_concept"),
        sa.UniqueConstraint("learning_path_id", "position", name="uq_learning_path_nodes_path_position"),
    )


def downgrade() -> None:
    op.drop_table("learning_path_nodes")
    op.drop_table("evidence")
    op.drop_table("learning_paths")
    op.drop_table("concept_relations")
    op.drop_table("material_blocks")
    op.drop_table("concepts")
    op.drop_table("materials")

    bind = op.get_bind()
    evidence_type.drop(bind, checkfirst=True)
    relation_type.drop(bind, checkfirst=True)
