"""A disposable database proves base upgrade and populated M5 downgrade."""

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.persistence.models import (
    AnalysisRun, AnalysisSpec, CleaningPlan, CleaningRun, Dataset, DatasetVersion,
    ExperimentPlan, FigureArtifact, GenerationRun, Hypothesis, Project,
    ResearchGap, ResearchLandscape, ResultInterpretation, StatisticalResult,
    VisualizationSpec, now_utc,
)


pytestmark = pytest.mark.skipif(os.getenv("RUN_DB_INTEGRATION") != "1", reason="Requires disposable PostgreSQL")


def test_clean_upgrade_and_populated_m5_downgrade_preserves_m4():
    configured = make_url(get_settings().database_url)
    admin_url = configured.set(database="postgres").render_as_string(hide_password=False)
    admin_url = admin_url.replace("postgresql+psycopg://", "postgresql://", 1)
    database_name = "m5_gate_" + uuid4().hex[:12]
    with psycopg.connect(admin_url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
    engine = None
    try:
        database_url = configured.set(database=database_name).render_as_string(hide_password=False)
        environment = os.environ.copy()
        environment["DATABASE_URL"] = database_url
        backend_root = Path(__file__).parents[1]

        def alembic(*args: str) -> str:
            result = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=backend_root,
                                    env=environment, capture_output=True, text=True)
            assert result.returncode == 0, result.stderr[-3000:]
            return result.stdout + result.stderr

        alembic("upgrade", "0009_m5_data_analysis")
        assert "0009_m5_data_analysis" in alembic("current")
        alembic("downgrade", "0008_m4_gate_fixes")
        assert "0008_m4_gate_fixes" in alembic("current")
        engine = create_engine(database_url)
        with Session(engine) as session:
            project = Project(title="M5 downgrade fixture", research_question="Does A differ from B?")
            session.add(project)
            session.flush()

            def generation(stage: str, status: str) -> GenerationRun:
                run = GenerationRun(project_id=project.id, stage=stage, status=status,
                    provider="fixture", model="qwen3.8-max", prompt_name="fixture", prompt_version="fixture",
                    prompt_hash="a" * 64, schema_version="fixture", research_question_snapshot=project.research_question,
                    question_revision=1, input_manifest=[], input_digest="a" * 64, attempt_count=0,
                    completed_at=now_utc() if status != "started" else None)
                session.add(run)
                session.flush()
                return run

            landscape = ResearchLandscape(project_id=project.id, summary="A and B need comparison.",
                uncertainty="Small corpus", review_status="accepted", reviewed_at=now_utc(),
                question_revision=1, origin="model", generation_run_id=generation("landscape", "succeeded").id)
            session.add(landscape)
            session.flush()
            gap = ResearchGap(project_id=project.id, landscape_id=landscape.id, title="Comparison gap",
                description="Within this corpus the comparison is limited.", gap_type="missing_comparison",
                why_it_matters="Test A and B", uncertainty="Small corpus", assumptions=[],
                novelty_scope="supplied_literature", selection_status="selected", question_revision=1,
                origin="model", generation_run_id=generation("gap", "succeeded").id)
            session.add(gap)
            session.flush()
            hypothesis = Hypothesis(project_id=project.id, research_gap_id=gap.id,
                statement="A will exceed B.", rationale="Compare A and B.", independent_variable="A versus B",
                dependent_variable="score", control_variables=[], falsification_condition="A does not exceed B.",
                assumptions=[], uncertainty="Small sample", selection_status="selected", question_revision=1,
                origin="model", generation_run_id=generation("hypothesis", "succeeded").id)
            session.add(hypothesis)
            session.flush()
            plan = ExperimentPlan(hypothesis_id=hypothesis.id, objective="Compare A and B",
                independent_variable="A versus B", dependent_variable="score", control_variables=[],
                experimental_groups=["A", "B"], baseline="B", dataset_requirements="CSV", metrics=["score"],
                procedure="Measure both groups", analysis_approach="Welch test", expected_observations=[],
                falsification_criteria="A does not exceed B", confounders=[], limitations=[], assumptions=[],
                uncertainty="Small sample", review_status="accepted", reviewed_at=now_utc(),
                question_revision=1, origin="model", generation_run_id=generation("experiment_plan", "succeeded").id)
            session.add(plan)
            session.commit()
            project_id, plan_id = project.id, plan.id

        alembic("upgrade", "0009_m5_data_analysis")
        with Session(engine) as session:
            project = session.get(Project, project_id)
            plan = session.get(ExperimentPlan, plan_id)
            for stage in ("cleaning_plan", "analysis_spec", "result_interpretation"):
                for status in ("started", "failed", "succeeded"):
                    generation(stage, status)
            model_run = session.scalar(select(GenerationRun).where(
                GenerationRun.project_id == project_id, GenerationRun.stage == "result_interpretation",
                GenerationRun.status == "succeeded"))
            dataset = Dataset(project_id=project_id, experiment_plan_id=plan_id,
                original_filename="fixture.csv", storage_key="datasets/raw.csv", byte_size=12,
                sha256="a" * 64, parser_version="fixture")
            session.add(dataset)
            session.flush()
            profile = {"row_count": 2, "column_count": 1, "columns": [{"name": "score"}]}
            raw = DatasetVersion(dataset_id=dataset.id, kind="raw", storage_key="datasets/raw.csv",
                sha256="a" * 64, row_count=2, column_count=1, profile=profile)
            session.add(raw)
            session.flush()
            cleaning = CleaningPlan(project_id=project_id, experiment_plan_id=plan_id,
                source_version_id=raw.id, operations=[], rationale="No-op", origin="human", review_status="accepted")
            session.add(cleaning)
            session.flush()
            clean_run = CleaningRun(project_id=project_id, cleaning_plan_id=cleaning.id,
                source_version_id=raw.id, status="succeeded", engine_version="fixture", effects=[])
            session.add(clean_run)
            session.flush()
            derived = DatasetVersion(dataset_id=dataset.id, parent_version_id=raw.id,
                cleaning_run_id=clean_run.id, kind="cleaned", storage_key="datasets/derived.csv",
                sha256="b" * 64, row_count=2, column_count=1, profile=profile)
            session.add(derived)
            session.flush()
            spec = AnalysisSpec(project_id=project_id, experiment_plan_id=plan_id,
                hypothesis_id=plan.hypothesis_id, dataset_version_id=derived.id, method="descriptive",
                column_mapping={"outcome": "score"}, parameters={}, rationale="Describe data",
                origin="human", review_status="accepted")
            session.add(spec)
            session.flush()
            analysis = AnalysisRun(project_id=project_id, analysis_spec_id=spec.id,
                dataset_version_id=derived.id, hypothesis_id=plan.hypothesis_id, method="descriptive",
                column_mapping=spec.column_mapping, parameters={}, status="succeeded", engine_version="fixture")
            session.add(analysis)
            session.flush()
            result = StatisticalResult(project_id=project_id, analysis_run_id=analysis.id,
                method="descriptive_summary", values={"mean": 2})
            session.add(result)
            session.flush()
            visual = VisualizationSpec(project_id=project_id, dataset_version_id=derived.id,
                analysis_run_id=analysis.id, statistical_result_id=result.id, chart_type="histogram",
                x_column="score", title="Scores", options={}, origin="human", status="succeeded")
            session.add(visual)
            session.flush()
            figure = FigureArtifact(project_id=project_id, visualization_spec_id=visual.id,
                dataset_version_id=derived.id, analysis_run_id=analysis.id,
                storage_key="figures/fixture.png", sha256="c" * 64, format="png", renderer_version="fixture")
            session.add(figure)
            session.flush()
            interpretation = ResultInterpretation(project_id=project_id, analysis_run_id=analysis.id,
                summary="The result remains uncertain.", hypothesis_relationship="Uncertain.",
                uncertainty="Small dataset", limitations=[], alternative_explanations=[],
                statistical_result_ids=[result.id], figure_artifact_ids=[figure.id],
                ai_assessment={}, review_status="accepted", question_revision=1,
                origin="model", generation_run_id=model_run.id)
            session.add(interpretation)
            session.commit()

        alembic("downgrade", "0008_m4_gate_fixes")
        assert "0008_m4_gate_fixes" in alembic("current")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT COUNT(*) FROM experiment_plans WHERE id = :id"), {"id": plan_id}) == 1
            assert connection.scalar(text("SELECT COUNT(*) FROM generation_runs WHERE stage IN ('cleaning_plan','analysis_spec','result_interpretation')")) == 0
            assert connection.scalar(text("SELECT to_regclass('public.datasets')")) is None
    finally:
        if engine is not None:
            engine.dispose()
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database_name)))
