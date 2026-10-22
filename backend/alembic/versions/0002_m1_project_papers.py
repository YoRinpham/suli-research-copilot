"""Add M1 project, paper and page-bound chunk persistence."""

import sqlalchemy as sa
from alembic import op


revision = "0002_m1_project_papers"
down_revision = "0001_enable_pgvector"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("research_question", sa.Text(), nullable=False),
        sa.Column("question_revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("question_revision >= 1", name="ck_projects_revision_positive"),
        sa.CheckConstraint("length(trim(title)) > 0", name="ck_projects_title_nonempty"),
        sa.CheckConstraint("length(trim(research_question)) > 0", name="ck_projects_question_nonempty"),
    )
    op.create_table(
        "papers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("title", sa.String(512), nullable=True),
        sa.Column("source_filename", sa.String(255), nullable=False),
        sa.Column("storage_key", sa.String(100), nullable=False, unique=True),
        sa.Column("parse_status", sa.String(32), nullable=False),
        sa.Column("quality_note", sa.Text(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("page_count IS NULL OR page_count > 0", name="ck_papers_page_count_positive"),
        sa.CheckConstraint(
            "parse_status IN ('uploaded','parsing','parsed','parse_failed','low_quality','embedding_failed','indexed')",
            name="ck_papers_parse_status",
        ),
    )
    op.create_index("ix_papers_project_id", "papers", ["project_id"])
    op.create_table(
        "paper_chunks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("paper_id", sa.String(36), sa.ForeignKey("papers.id"), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("section", sa.String(255), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.UniqueConstraint("paper_id", "page_number", "chunk_index", name="uq_chunk_page_order"),
        sa.CheckConstraint("page_number >= 1", name="ck_chunk_page_positive"),
        sa.CheckConstraint("chunk_index >= 0", name="ck_chunk_index_nonnegative"),
        sa.CheckConstraint("length(trim(text)) > 0", name="ck_chunk_text_nonempty"),
    )
    op.create_index("ix_paper_chunks_paper_id", "paper_chunks", ["paper_id"])


def downgrade() -> None:
    op.drop_index("ix_paper_chunks_paper_id", table_name="paper_chunks")
    op.drop_table("paper_chunks")
    op.drop_index("ix_papers_project_id", table_name="papers")
    op.drop_table("papers")
    op.drop_table("projects")
