"""M5 immutable experimental data, deterministic computation, and reviewed interpretation."""

from alembic import op


revision = "0009_m5_data_analysis"
down_revision = "0008_m4_gate_fixes"
branch_labels = None
depends_on = None


TABLES = (
    """CREATE TABLE datasets (
        id varchar(36) PRIMARY KEY, project_id varchar(36) NOT NULL REFERENCES projects(id),
        experiment_plan_id varchar(36) NOT NULL REFERENCES experiment_plans(id),
        original_filename varchar(255) NOT NULL, storage_key varchar(255) NOT NULL UNIQUE,
        byte_size integer NOT NULL CHECK (byte_size > 0), sha256 varchar(64) NOT NULL,
        parser_version varchar(32) NOT NULL, created_at timestamptz NOT NULL)""",
    """CREATE TABLE dataset_versions (
        id varchar(36) PRIMARY KEY, dataset_id varchar(36) NOT NULL REFERENCES datasets(id),
        parent_version_id varchar(36) REFERENCES dataset_versions(id),
        cleaning_run_id varchar(36) UNIQUE,
        kind varchar(16) NOT NULL CHECK (kind IN ('raw','cleaned')),
        storage_key varchar(255) NOT NULL UNIQUE, sha256 varchar(64) NOT NULL,
        row_count integer NOT NULL, column_count integer NOT NULL, profile jsonb NOT NULL,
        created_at timestamptz NOT NULL,
        CONSTRAINT ck_dataset_version_shape CHECK (row_count > 0 AND column_count > 0),
        CONSTRAINT ck_dataset_version_lineage CHECK (
            (kind = 'raw' AND parent_version_id IS NULL AND cleaning_run_id IS NULL) OR
            (kind = 'cleaned' AND parent_version_id IS NOT NULL AND cleaning_run_id IS NOT NULL)))""",
    """CREATE TABLE cleaning_plans (
        id varchar(36) PRIMARY KEY, project_id varchar(36) NOT NULL REFERENCES projects(id),
        experiment_plan_id varchar(36) NOT NULL REFERENCES experiment_plans(id),
        source_version_id varchar(36) NOT NULL REFERENCES dataset_versions(id),
        operations jsonb NOT NULL, rationale text NOT NULL, origin varchar(16) NOT NULL,
        review_status varchar(24) NOT NULL CHECK (review_status IN ('pending_review','accepted','rejected')),
        generation_run_id varchar(36) REFERENCES generation_runs(id),
        created_at timestamptz NOT NULL, reviewed_at timestamptz,
        CONSTRAINT ck_cleaning_plan_origin CHECK (origin IN ('human','model')))""",
    """CREATE TABLE cleaning_runs (
        id varchar(36) PRIMARY KEY, project_id varchar(36) NOT NULL REFERENCES projects(id),
        cleaning_plan_id varchar(36) NOT NULL REFERENCES cleaning_plans(id),
        source_version_id varchar(36) NOT NULL REFERENCES dataset_versions(id),
        status varchar(16) NOT NULL CHECK (status IN ('started','succeeded','failed')),
        engine_version varchar(32) NOT NULL, rows_before integer, rows_after integer,
        columns_before integer, columns_after integer, effects jsonb, error_type varchar(64),
        started_at timestamptz NOT NULL, completed_at timestamptz)""",
    """CREATE TABLE analysis_specs (
        id varchar(36) PRIMARY KEY, project_id varchar(36) NOT NULL REFERENCES projects(id),
        experiment_plan_id varchar(36) NOT NULL REFERENCES experiment_plans(id),
        hypothesis_id varchar(36) NOT NULL REFERENCES hypotheses(id),
        dataset_version_id varchar(36) NOT NULL REFERENCES dataset_versions(id),
        method varchar(32) NOT NULL, column_mapping jsonb NOT NULL, parameters jsonb NOT NULL,
        rationale text NOT NULL, origin varchar(16) NOT NULL,
        review_status varchar(24) NOT NULL CHECK (review_status IN ('pending_review','accepted','rejected')),
        generation_run_id varchar(36) REFERENCES generation_runs(id),
        created_at timestamptz NOT NULL, reviewed_at timestamptz,
        CONSTRAINT ck_analysis_spec_origin CHECK (origin IN ('human','model')))""",
    """CREATE TABLE analysis_runs (
        id varchar(36) PRIMARY KEY, project_id varchar(36) NOT NULL REFERENCES projects(id),
        analysis_spec_id varchar(36) NOT NULL REFERENCES analysis_specs(id),
        dataset_version_id varchar(36) NOT NULL REFERENCES dataset_versions(id),
        hypothesis_id varchar(36) NOT NULL REFERENCES hypotheses(id), method varchar(32) NOT NULL,
        column_mapping jsonb NOT NULL, parameters jsonb NOT NULL,
        status varchar(16) NOT NULL CHECK (status IN ('started','succeeded','failed')),
        engine_version varchar(32) NOT NULL, error_type varchar(64),
        started_at timestamptz NOT NULL, completed_at timestamptz)""",
    """CREATE TABLE statistical_results (
        id varchar(36) PRIMARY KEY, project_id varchar(36) NOT NULL REFERENCES projects(id),
        analysis_run_id varchar(36) NOT NULL REFERENCES analysis_runs(id),
        method varchar(48) NOT NULL, values jsonb NOT NULL, created_at timestamptz NOT NULL)""",
    """CREATE TABLE visualization_specs (
        id varchar(36) PRIMARY KEY, project_id varchar(36) NOT NULL REFERENCES projects(id),
        dataset_version_id varchar(36) NOT NULL REFERENCES dataset_versions(id),
        analysis_run_id varchar(36) REFERENCES analysis_runs(id),
        statistical_result_id varchar(36) REFERENCES statistical_results(id),
        chart_type varchar(32) NOT NULL CHECK (chart_type IN ('histogram','box_plot','scatter','group_summary')),
        x_column varchar(255), y_column varchar(255), group_column varchar(255),
        title varchar(255) NOT NULL, options jsonb NOT NULL, origin varchar(16) NOT NULL,
        status varchar(16) NOT NULL CHECK (status IN ('started','succeeded','failed')),
        error_type varchar(64), created_at timestamptz NOT NULL, completed_at timestamptz)""",
    """CREATE TABLE figure_artifacts (
        id varchar(36) PRIMARY KEY, project_id varchar(36) NOT NULL REFERENCES projects(id),
        visualization_spec_id varchar(36) NOT NULL REFERENCES visualization_specs(id),
        dataset_version_id varchar(36) NOT NULL REFERENCES dataset_versions(id),
        analysis_run_id varchar(36) REFERENCES analysis_runs(id),
        storage_key varchar(255) NOT NULL UNIQUE, sha256 varchar(64) NOT NULL,
        format varchar(8) NOT NULL, renderer_version varchar(32) NOT NULL,
        created_at timestamptz NOT NULL)""",
    """CREATE TABLE result_interpretations (
        id varchar(36) PRIMARY KEY, project_id varchar(36) NOT NULL REFERENCES projects(id),
        analysis_run_id varchar(36) NOT NULL REFERENCES analysis_runs(id),
        summary text NOT NULL, hypothesis_relationship text NOT NULL, uncertainty text NOT NULL,
        limitations jsonb NOT NULL, alternative_explanations jsonb NOT NULL,
        statistical_result_ids jsonb NOT NULL, figure_artifact_ids jsonb NOT NULL,
        ai_assessment jsonb, review_status varchar(24) NOT NULL
            CHECK (review_status IN ('pending_review','accepted','rejected')),
        question_revision integer NOT NULL, origin varchar(24) NOT NULL,
        supersedes_id varchar(36) UNIQUE REFERENCES result_interpretations(id),
        generation_run_id varchar(36) REFERENCES generation_runs(id),
        created_at timestamptz NOT NULL, reviewed_at timestamptz,
        CONSTRAINT ck_interpretation_origin CHECK (
            (origin = 'model' AND generation_run_id IS NOT NULL AND supersedes_id IS NULL) OR
            (origin = 'human_revision' AND generation_run_id IS NULL AND supersedes_id IS NOT NULL)))""",
)


def upgrade() -> None:
    for statement in TABLES:
        op.execute(statement)
    op.execute("ALTER TABLE dataset_versions ADD CONSTRAINT fk_dataset_versions_cleaning_run FOREIGN KEY (cleaning_run_id) REFERENCES cleaning_runs(id) ON DELETE CASCADE")
    op.drop_constraint("ck_generation_run_stage", "generation_runs", type_="check")
    op.create_check_constraint("ck_generation_run_stage", "generation_runs",
        "stage IN ('claim','landscape','gap','hypothesis','experiment_plan','cleaning_plan','analysis_spec','result_interpretation')")
    for name, column in (("datasets", "project_id"), ("dataset_versions", "dataset_id"),
                         ("cleaning_plans", "project_id"), ("cleaning_runs", "project_id"),
                         ("analysis_specs", "project_id"), ("analysis_runs", "project_id"),
                         ("statistical_results", "project_id"), ("visualization_specs", "project_id"),
                         ("figure_artifacts", "project_id"), ("result_interpretations", "project_id")):
        op.create_index(f"ix_{name}_{column}", name, [column])


def downgrade() -> None:
    op.drop_constraint("ck_generation_run_stage", "generation_runs", type_="check")
    op.drop_constraint("fk_dataset_versions_cleaning_run", "dataset_versions", type_="foreignkey")
    for table in ("result_interpretations", "figure_artifacts", "visualization_specs", "statistical_results",
                  "analysis_runs", "analysis_specs", "cleaning_runs", "cleaning_plans", "dataset_versions", "datasets"):
        op.drop_table(table)
    op.execute("DELETE FROM generation_runs WHERE stage IN ('cleaning_plan','analysis_spec','result_interpretation')")
    op.create_check_constraint("ck_generation_run_stage", "generation_runs",
        "stage IN ('claim','landscape','gap','hypothesis','experiment_plan')")
