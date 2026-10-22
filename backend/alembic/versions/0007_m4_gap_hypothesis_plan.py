"""Add corpus-bounded gaps, falsifiable hypotheses, and reviewed plans."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op


revision = "0007_m4_gap_hypothesis_plan"
down_revision = "0006_m3_human_authority"
branch_labels = None
depends_on = None


def _identity(project: bool = True) -> list[sa.Column]:
    columns = [sa.Column("id", sa.String(36), primary_key=True)]
    if project:
        columns.append(sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False))
    return columns


def _lineage(table: str, stage: str, *, selection: bool = True) -> list:
    columns: list = [
        sa.Column("question_revision", sa.Integer(), nullable=False),
        sa.Column("origin", sa.String(24), nullable=False),
        sa.Column("supersedes_id", sa.String(36), sa.ForeignKey(f"{table}.id"), unique=True),
        sa.Column("generation_run_id", sa.String(36), sa.ForeignKey("generation_runs.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("question_revision >= 1", name=f"ck_{stage}_revision"),
        sa.CheckConstraint("(origin = 'model' AND generation_run_id IS NOT NULL AND supersedes_id IS NULL) OR (origin = 'human_revision' AND generation_run_id IS NULL AND supersedes_id IS NOT NULL)", name=f"ck_{stage}_origin_lineage"),
    ]
    if selection:
        columns.extend([sa.Column("selection_status", sa.String(16), nullable=False),
                        sa.CheckConstraint("selection_status IN ('candidate','selected','rejected')", name=f"ck_{stage}_selection")])
    return columns


def upgrade() -> None:
    op.drop_constraint("ck_generation_run_stage", "generation_runs", type_="check")
    op.create_check_constraint("ck_generation_run_stage", "generation_runs",
                               "stage IN ('claim','landscape','gap','hypothesis','experiment_plan')")
    op.create_table("research_gaps", *_identity(),
        sa.Column("landscape_id", sa.String(36), sa.ForeignKey("research_landscapes.id"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False), sa.Column("description", sa.Text(), nullable=False),
        sa.Column("gap_type", sa.String(40), nullable=False), sa.Column("why_it_matters", sa.Text(), nullable=False),
        sa.Column("uncertainty", sa.Text(), nullable=False), sa.Column("assumptions", JSONB(), nullable=False),
        sa.Column("novelty_scope", sa.String(32), nullable=False), sa.Column("ai_assessment", JSONB()),
        *_lineage("research_gaps", "gap"),
        sa.CheckConstraint("length(trim(title)) > 0 AND length(trim(description)) > 0 AND length(trim(why_it_matters)) > 0 AND length(trim(uncertainty)) > 0", name="ck_gap_scientific_text"),
        sa.CheckConstraint("gap_type IN ('contradictory_findings','underexplored_variable','missing_comparison','limited_dataset','insufficient_replication','methodological_limitation','untested_interaction','unexplored_domain','unresolved_cause','evaluation_inconsistency')", name="ck_gap_type"),
        sa.CheckConstraint("novelty_scope = 'supplied_literature'", name="ck_gap_corpus_scope"))
    op.create_index("ix_research_gaps_project_id", "research_gaps", ["project_id"])
    op.create_index("ix_research_gaps_landscape_id", "research_gaps", ["landscape_id"])
    op.create_index("ix_research_gaps_generation_run_id", "research_gaps", ["generation_run_id"])
    op.create_table("gap_landscape_items",
        sa.Column("gap_id", sa.String(36), sa.ForeignKey("research_gaps.id"), primary_key=True),
        sa.Column("item_id", sa.String(36), sa.ForeignKey("landscape_items.id"), primary_key=True))
    op.create_table("gap_claims",
        sa.Column("gap_id", sa.String(36), sa.ForeignKey("research_gaps.id"), primary_key=True),
        sa.Column("claim_id", sa.String(36), sa.ForeignKey("claims.id"), primary_key=True))
    op.create_table("gap_evidence",
        sa.Column("gap_id", sa.String(36), sa.ForeignKey("research_gaps.id"), primary_key=True),
        sa.Column("evidence_id", sa.String(36), sa.ForeignKey("evidence.id"), primary_key=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.CheckConstraint("role IN ('supports','challenges')", name="ck_gap_evidence_role"))

    op.create_table("hypotheses", *_identity(),
        sa.Column("research_gap_id", sa.String(36), sa.ForeignKey("research_gaps.id"), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False), sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("independent_variable", sa.Text(), nullable=False), sa.Column("dependent_variable", sa.Text(), nullable=False),
        sa.Column("control_variables", JSONB(), nullable=False), sa.Column("falsification_condition", sa.Text(), nullable=False),
        sa.Column("assumptions", JSONB(), nullable=False), sa.Column("uncertainty", sa.Text(), nullable=False),
        sa.Column("ai_assessment", JSONB()), *_lineage("hypotheses", "hypothesis"),
        sa.CheckConstraint("length(trim(statement)) > 0 AND length(trim(rationale)) > 0 AND length(trim(independent_variable)) > 0 AND length(trim(dependent_variable)) > 0 AND length(trim(falsification_condition)) > 0 AND length(trim(uncertainty)) > 0", name="ck_hypothesis_scientific_text"))
    op.create_index("ix_hypotheses_project_id", "hypotheses", ["project_id"])
    op.create_index("ix_hypotheses_research_gap_id", "hypotheses", ["research_gap_id"])
    op.create_index("ix_hypotheses_generation_run_id", "hypotheses", ["generation_run_id"])
    op.create_table("hypothesis_evidence",
        sa.Column("hypothesis_id", sa.String(36), sa.ForeignKey("hypotheses.id"), primary_key=True),
        sa.Column("evidence_id", sa.String(36), sa.ForeignKey("evidence.id"), primary_key=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.CheckConstraint("role IN ('motivates','challenges')", name="ck_hypothesis_evidence_role"))
    op.create_table("hypothesis_claims",
        sa.Column("hypothesis_id", sa.String(36), sa.ForeignKey("hypotheses.id"), primary_key=True),
        sa.Column("claim_id", sa.String(36), sa.ForeignKey("claims.id"), primary_key=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.CheckConstraint("role IN ('challenges','extends')", name="ck_hypothesis_claim_role"))

    op.create_table("experiment_plans", *_identity(False),
        sa.Column("hypothesis_id", sa.String(36), sa.ForeignKey("hypotheses.id"), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("independent_variable", sa.Text(), nullable=False), sa.Column("dependent_variable", sa.Text(), nullable=False),
        sa.Column("control_variables", JSONB(), nullable=False), sa.Column("experimental_groups", JSONB(), nullable=False),
        sa.Column("baseline", sa.Text(), nullable=False), sa.Column("dataset_requirements", sa.Text(), nullable=False),
        sa.Column("metrics", JSONB(), nullable=False), sa.Column("procedure", sa.Text(), nullable=False),
        sa.Column("analysis_approach", sa.Text(), nullable=False), sa.Column("expected_observations", JSONB(), nullable=False),
        sa.Column("falsification_criteria", sa.Text(), nullable=False), sa.Column("confounders", JSONB(), nullable=False),
        sa.Column("limitations", JSONB(), nullable=False), sa.Column("assumptions", JSONB(), nullable=False),
        sa.Column("uncertainty", sa.Text(), nullable=False), sa.Column("ai_alignment_assessment", JSONB()),
        sa.Column("review_status", sa.String(24), nullable=False), sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        *_lineage("experiment_plans", "plan", selection=False),
        sa.CheckConstraint("length(trim(objective)) > 0 AND length(trim(baseline)) > 0 AND length(trim(falsification_criteria)) > 0", name="ck_plan_scientific_text"),
        sa.CheckConstraint("review_status IN ('pending_review','accepted','rejected')", name="ck_plan_review"))
    op.create_index("ix_experiment_plans_hypothesis_id", "experiment_plans", ["hypothesis_id"])
    op.create_index("ix_experiment_plans_generation_run_id", "experiment_plans", ["generation_run_id"])


def downgrade() -> None:
    op.drop_index("ix_experiment_plans_generation_run_id", table_name="experiment_plans")
    op.drop_index("ix_experiment_plans_hypothesis_id", table_name="experiment_plans")
    op.drop_table("experiment_plans")
    op.drop_table("hypothesis_claims")
    op.drop_table("hypothesis_evidence")
    for name in ("ix_hypotheses_generation_run_id", "ix_hypotheses_research_gap_id", "ix_hypotheses_project_id"):
        op.drop_index(name, table_name="hypotheses")
    op.drop_table("hypotheses")
    op.drop_table("gap_evidence")
    op.drop_table("gap_claims")
    op.drop_table("gap_landscape_items")
    for name in ("ix_research_gaps_generation_run_id", "ix_research_gaps_landscape_id", "ix_research_gaps_project_id"):
        op.drop_index(name, table_name="research_gaps")
    op.drop_table("research_gaps")
    op.drop_constraint("ck_generation_run_stage", "generation_runs", type_="check")
    op.create_check_constraint("ck_generation_run_stage", "generation_runs", "stage IN ('claim','landscape')")
