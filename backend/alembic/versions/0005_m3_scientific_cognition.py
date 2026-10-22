"""M3 GenerationRun, Claim and ResearchLandscape with relational lineage."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op


revision = "0005_m3_scientific_cognition"
down_revision = "0004_m2_embedding_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "generation_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("stage", sa.String(24), nullable=False), sa.Column("status", sa.String(16), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False), sa.Column("model", sa.String(128), nullable=False),
        sa.Column("prompt_name", sa.String(128), nullable=False), sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("prompt_hash", sa.String(64), nullable=False), sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("research_question_snapshot", sa.Text(), nullable=False),
        sa.Column("question_revision", sa.Integer(), nullable=False),
        sa.Column("input_manifest", JSONB(), nullable=False), sa.Column("input_digest", sa.String(64), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False), sa.Column("error_type", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("stage IN ('claim','landscape')", name="ck_generation_run_stage"),
        sa.CheckConstraint("status IN ('started','succeeded','failed')", name="ck_generation_run_status"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_generation_run_attempts"),
        sa.CheckConstraint("question_revision >= 1", name="ck_generation_run_revision"),
    )
    op.create_index("ix_generation_runs_project_id", "generation_runs", ["project_id"])
    op.create_table(
        "claims",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("text", sa.Text(), nullable=False), sa.Column("claim_type", sa.String(24), nullable=False),
        sa.Column("grounding_status", sa.String(32), nullable=False), sa.Column("confidence", sa.String(16), nullable=False),
        sa.Column("uncertainty", sa.Text(), nullable=False), sa.Column("selection_status", sa.String(16), nullable=False),
        sa.Column("question_revision", sa.Integer(), nullable=False), sa.Column("origin", sa.String(24), nullable=False),
        sa.Column("supersedes_id", sa.String(36), sa.ForeignKey("claims.id"), unique=True),
        sa.Column("generation_run_id", sa.String(36), sa.ForeignKey("generation_runs.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(trim(text)) > 0 AND length(trim(uncertainty)) > 0", name="ck_claim_scientific_text"),
        sa.CheckConstraint("claim_type IN ('finding','method','comparison','limitation','other')", name="ck_claim_type"),
        sa.CheckConstraint("grounding_status IN ('supported','inferred','unsupported','insufficient_evidence')", name="ck_claim_grounding"),
        sa.CheckConstraint("confidence IN ('low','medium','high','unknown')", name="ck_claim_confidence"),
        sa.CheckConstraint("selection_status IN ('candidate','selected','rejected')", name="ck_claim_selection"),
        sa.CheckConstraint("question_revision >= 1", name="ck_claim_revision"),
        sa.CheckConstraint("(origin = 'model' AND generation_run_id IS NOT NULL AND supersedes_id IS NULL) OR "
                           "(origin = 'human_revision' AND generation_run_id IS NULL AND supersedes_id IS NOT NULL)",
                           name="ck_claim_origin_lineage"),
    )
    op.create_index("ix_claims_project_id", "claims", ["project_id"])
    op.create_index("ix_claims_generation_run_id", "claims", ["generation_run_id"])
    op.create_table(
        "claim_evidence",
        sa.Column("claim_id", sa.String(36), sa.ForeignKey("claims.id"), primary_key=True),
        sa.Column("evidence_id", sa.String(36), sa.ForeignKey("evidence.id"), primary_key=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.CheckConstraint("role IN ('supports','challenges')", name="ck_claim_evidence_role"),
    )
    op.create_table(
        "research_landscapes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False), sa.Column("uncertainty", sa.Text(), nullable=False),
        sa.Column("question_revision", sa.Integer(), nullable=False), sa.Column("origin", sa.String(24), nullable=False),
        sa.Column("supersedes_id", sa.String(36), sa.ForeignKey("research_landscapes.id"), unique=True),
        sa.Column("generation_run_id", sa.String(36), sa.ForeignKey("generation_runs.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(trim(summary)) > 0 AND length(trim(uncertainty)) > 0", name="ck_landscape_scientific_text"),
        sa.CheckConstraint("question_revision >= 1", name="ck_landscape_revision"),
        sa.CheckConstraint("(origin = 'model' AND generation_run_id IS NOT NULL AND supersedes_id IS NULL) OR "
                           "(origin = 'human_revision' AND generation_run_id IS NULL AND supersedes_id IS NOT NULL)",
                           name="ck_landscape_origin_lineage"),
    )
    op.create_index("ix_research_landscapes_project_id", "research_landscapes", ["project_id"])
    op.create_index("ix_research_landscapes_generation_run_id", "research_landscapes", ["generation_run_id"])
    op.create_table(
        "landscape_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("landscape_id", sa.String(36), sa.ForeignKey("research_landscapes.id"), nullable=False),
        sa.Column("category", sa.String(24), nullable=False), sa.Column("text", sa.Text(), nullable=False),
        sa.Column("uncertainty", sa.Text(), nullable=False),
        sa.CheckConstraint("category IN ('approach','method','dataset','metric','finding','conflict')", name="ck_landscape_item_category"),
        sa.CheckConstraint("length(trim(text)) > 0 AND length(trim(uncertainty)) > 0", name="ck_landscape_item_text"),
    )
    op.create_index("ix_landscape_items_landscape_id", "landscape_items", ["landscape_id"])
    op.create_table(
        "landscape_item_claims",
        sa.Column("item_id", sa.String(36), sa.ForeignKey("landscape_items.id"), primary_key=True),
        sa.Column("claim_id", sa.String(36), sa.ForeignKey("claims.id"), primary_key=True),
    )
    op.create_table(
        "landscape_item_evidence",
        sa.Column("item_id", sa.String(36), sa.ForeignKey("landscape_items.id"), primary_key=True),
        sa.Column("evidence_id", sa.String(36), sa.ForeignKey("evidence.id"), primary_key=True),
    )


def downgrade() -> None:
    op.drop_table("landscape_item_evidence")
    op.drop_table("landscape_item_claims")
    op.drop_index("ix_landscape_items_landscape_id", table_name="landscape_items")
    op.drop_table("landscape_items")
    op.drop_index("ix_research_landscapes_generation_run_id", table_name="research_landscapes")
    op.drop_index("ix_research_landscapes_project_id", table_name="research_landscapes")
    op.drop_table("research_landscapes")
    op.drop_table("claim_evidence")
    op.drop_index("ix_claims_generation_run_id", table_name="claims")
    op.drop_index("ix_claims_project_id", table_name="claims")
    op.drop_table("claims")
    op.drop_index("ix_generation_runs_project_id", table_name="generation_runs")
    op.drop_table("generation_runs")
