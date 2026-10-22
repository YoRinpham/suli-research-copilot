from app.persistence.base import Base
from app.persistence import models  # noqa: F401


def test_m5_first_class_tables_and_lineage_columns():
    tables = Base.metadata.tables
    for name in ("datasets", "dataset_versions", "cleaning_plans", "cleaning_runs",
                 "analysis_specs", "analysis_runs", "statistical_results", "visualization_specs",
                 "figure_artifacts", "result_interpretations"):
        assert name in tables
    assert "experiment_plan_id" in tables["datasets"].c
    assert "cleaning_run_id" in tables["dataset_versions"].c
    assert "dataset_version_id" in tables["analysis_runs"].c
    assert "hypothesis_id" in tables["analysis_specs"].c
    assert "analysis_run_id" in tables["statistical_results"].c
    assert "supersedes_id" in tables["result_interpretations"].c
