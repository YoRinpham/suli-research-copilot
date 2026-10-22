"""Separate AI assessment from explicit human scientific authority."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op


revision = "0006_m3_human_authority"
down_revision = "0005_m3_scientific_cognition"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_claim_grounding", "claims", type_="check")
    op.alter_column("claims", "grounding_status", new_column_name="legacy_grounding_status",
                    existing_type=sa.String(32), nullable=True)
    op.create_check_constraint("ck_claim_legacy_grounding", "claims",
        "legacy_grounding_status IS NULL OR legacy_grounding_status IN ('supported','inferred','unsupported','insufficient_evidence')")
    op.add_column("claims", sa.Column("ai_grounding_assessment", sa.String(32)))
    op.add_column("claims", sa.Column("ai_risk_flags", JSONB()))
    op.add_column("claims", sa.Column("ai_justification", sa.Text()))
    op.add_column("claims", sa.Column("ai_evidence_refs", JSONB()))
    op.add_column("claims", sa.Column("ai_support_excerpts", JSONB()))
    op.add_column("claims", sa.Column("human_grounding_status", sa.String(32), nullable=False,
                                       server_default="pending_review"))
    op.add_column("claims", sa.Column("human_reviewed_at", sa.DateTime(timezone=True)))
    op.create_check_constraint("ck_claim_ai_grounding", "claims",
        "ai_grounding_assessment IS NULL OR ai_grounding_assessment IN ('directly_supported','inferentially_supported','unsupported','contradicted','insufficient_evidence')")
    op.create_check_constraint("ck_claim_human_grounding", "claims",
        "human_grounding_status IN ('pending_review','supported','inferred','unsupported','insufficient_evidence')")

    op.add_column("research_landscapes", sa.Column("review_status", sa.String(24), nullable=False,
                                                    server_default="pending_review"))
    op.add_column("research_landscapes", sa.Column("reviewed_at", sa.DateTime(timezone=True)))
    op.add_column("research_landscapes", sa.Column("ai_summary_assessments", JSONB()))
    op.create_check_constraint("ck_landscape_review", "research_landscapes",
                               "review_status IN ('pending_review','accepted','rejected')")
    op.add_column("landscape_items", sa.Column("ai_conflict_assessment", JSONB()))


def downgrade() -> None:
    op.drop_column("landscape_items", "ai_conflict_assessment")
    op.drop_constraint("ck_landscape_review", "research_landscapes", type_="check")
    op.drop_column("research_landscapes", "ai_summary_assessments")
    op.drop_column("research_landscapes", "reviewed_at")
    op.drop_column("research_landscapes", "review_status")
    op.drop_constraint("ck_claim_human_grounding", "claims", type_="check")
    op.drop_constraint("ck_claim_ai_grounding", "claims", type_="check")
    op.drop_column("claims", "human_reviewed_at")
    op.drop_column("claims", "human_grounding_status")
    op.drop_column("claims", "ai_support_excerpts")
    op.drop_column("claims", "ai_evidence_refs")
    op.drop_column("claims", "ai_justification")
    op.drop_column("claims", "ai_risk_flags")
    op.drop_column("claims", "ai_grounding_assessment")
    op.drop_constraint("ck_claim_legacy_grounding", "claims", type_="check")
    op.execute("UPDATE claims SET legacy_grounding_status = 'insufficient_evidence' WHERE legacy_grounding_status IS NULL")
    op.alter_column("claims", "legacy_grounding_status", new_column_name="grounding_status",
                    existing_type=sa.String(32), nullable=False)
    op.create_check_constraint("ck_claim_grounding", "claims",
        "grounding_status IN ('supported','inferred','unsupported','insufficient_evidence')")
