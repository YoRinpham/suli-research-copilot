"""Add active embedding claim, fixed vector space, and selected Evidence."""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import VECTOR


revision = "0004_m2_embedding_evidence"
down_revision = "0003_m1_parse_recovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("papers", sa.Column("embedding_started_at", sa.DateTime(timezone=True), nullable=True))
    op.drop_constraint("ck_papers_parse_status", "papers", type_="check")
    op.create_check_constraint(
        "ck_papers_parse_status", "papers",
        "parse_status IN ('uploaded','parsing','parsed','embedding','parse_failed','low_quality','embedding_failed','indexed')",
    )
    op.add_column("paper_chunks", sa.Column("embedding", VECTOR(1024), nullable=True))
    op.add_column("paper_chunks", sa.Column("embedding_model", sa.String(128), nullable=True))
    op.add_column("paper_chunks", sa.Column("embedding_dimension", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_chunk_embedding_complete", "paper_chunks",
        "(embedding IS NULL AND embedding_model IS NULL AND embedding_dimension IS NULL) OR "
        "(embedding IS NOT NULL AND embedding_model IS NOT NULL AND embedding_dimension = 1024)",
    )
    op.create_table(
        "evidence",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("paper_chunk_id", sa.String(36), sa.ForeignKey("paper_chunks.id"), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("evidence_type", sa.String(32), nullable=False),
        sa.Column("relevance_note", sa.Text(), nullable=True),
        sa.Column("selection_status", sa.String(16), nullable=False),
        sa.Column("question_revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(trim(source_text)) > 0", name="ck_evidence_source_nonempty"),
        sa.CheckConstraint("question_revision >= 1", name="ck_evidence_revision_positive"),
        sa.CheckConstraint("selection_status IN ('selected','rejected')", name="ck_evidence_selection"),
        sa.CheckConstraint(
            "evidence_type IN ('reported_result','method_description','dataset_detail','limitation','author_claim','other')",
            name="ck_evidence_type",
        ),
    )
    op.create_index("ix_evidence_project_id", "evidence", ["project_id"])
    op.create_index("ix_evidence_paper_chunk_id", "evidence", ["paper_chunk_id"])


def downgrade() -> None:
    op.drop_index("ix_evidence_paper_chunk_id", table_name="evidence")
    op.drop_index("ix_evidence_project_id", table_name="evidence")
    op.drop_table("evidence")
    op.drop_constraint("ck_chunk_embedding_complete", "paper_chunks", type_="check")
    op.drop_column("paper_chunks", "embedding_dimension")
    op.drop_column("paper_chunks", "embedding_model")
    op.drop_column("paper_chunks", "embedding")
    op.drop_constraint("ck_papers_parse_status", "papers", type_="check")
    op.create_check_constraint(
        "ck_papers_parse_status", "papers",
        "parse_status IN ('uploaded','parsing','parsed','parse_failed','low_quality','embedding_failed','indexed')",
    )
    op.drop_column("papers", "embedding_started_at")
