"""M6 immutable report and generation stage.

Revision ID: 0010_m6_report_delivery
Revises: 0009_m5_data_analysis
"""

from alembic import op

revision = "0010_m6_report_delivery"
down_revision = "0009_m5_data_analysis"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE research_reports (
        id varchar(36) PRIMARY KEY, project_id varchar(36) NOT NULL REFERENCES projects(id),
        question_revision integer NOT NULL CHECK (question_revision >= 1),
        title varchar(255) NOT NULL, origin varchar(24) NOT NULL,
        generation_run_id varchar(36) REFERENCES generation_runs(id),
        review_status varchar(24) NOT NULL CHECK (review_status IN ('pending_review','accepted','rejected')),
        source_bundle jsonb NOT NULL, source_bundle_hash varchar(64) NOT NULL,
        narrative_blocks jsonb NOT NULL,
        supersedes_id varchar(36) UNIQUE REFERENCES research_reports(id),
        created_at timestamptz NOT NULL, reviewed_at timestamptz,
        CONSTRAINT ck_report_origin CHECK (
          (origin = 'model' AND generation_run_id IS NOT NULL AND supersedes_id IS NULL) OR
          (origin = 'human_revision' AND generation_run_id IS NULL AND supersedes_id IS NOT NULL)))""")
    op.create_index("ix_research_reports_project_id", "research_reports", ["project_id"])
    op.drop_constraint("ck_generation_run_stage", "generation_runs", type_="check")
    op.create_check_constraint("ck_generation_run_stage", "generation_runs",
        "stage IN ('claim','landscape','gap','hypothesis','experiment_plan','cleaning_plan','analysis_spec','result_interpretation','research_report')")
    op.execute("CREATE UNIQUE INDEX uq_active_research_report_generation ON generation_runs(project_id, input_digest) WHERE stage='research_report' AND status='started'")


def downgrade() -> None:
    op.execute("DROP INDEX uq_active_research_report_generation")
    op.drop_table("research_reports")
    op.execute("DELETE FROM generation_runs WHERE stage = 'research_report'")
    op.drop_constraint("ck_generation_run_stage", "generation_runs", type_="check")
    op.create_check_constraint("ck_generation_run_stage", "generation_runs",
        "stage IN ('claim','landscape','gap','hypothesis','experiment_plan','cleaning_plan','analysis_spec','result_interpretation')")
