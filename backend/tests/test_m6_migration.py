"""Disposable PostgreSQL verifies M6 head and populated M6-to-M5 downgrade."""

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
    AnalysisRun, AnalysisSpec, Claim, ClaimEvidence, Dataset, DatasetVersion, Evidence,
    ExperimentPlan, GenerationRun, Hypothesis, Paper, PaperChunk, Project, ResearchGap,
    ResearchLandscape, ResearchReport, ResultInterpretation, StatisticalResult, now_utc,
)


pytestmark = pytest.mark.skipif(os.getenv("RUN_DB_INTEGRATION") != "1", reason="Requires disposable PostgreSQL")


def test_m6_upgrade_and_populated_downgrade_preserve_m5():
    configured = make_url(get_settings().database_url)
    admin_url = configured.set(database="postgres").render_as_string(hide_password=False)
    admin_url = admin_url.replace("postgresql+psycopg://", "postgresql://", 1)
    database_name = "m6_gate_" + uuid4().hex[:12]
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

        alembic("upgrade", "head")
        assert "0010_m6_report_delivery" in alembic("current")
        alembic("downgrade", "0009_m5_data_analysis")
        assert "0009_m5_data_analysis" in alembic("current")
        engine = create_engine(database_url)
        with Session(engine) as session:
            project = Project(title="M6 migration", research_question="Synthetic question?")
            session.add(project)
            session.flush()
            project_id = project.id
            def generation(stage: str) -> GenerationRun:
                run = GenerationRun(project_id=project_id, stage=stage, status="succeeded",
                    provider="fixture", model="fixture", prompt_name="fixture", prompt_version="m5",
                    prompt_hash="a" * 64, schema_version="m5", research_question_snapshot=project.research_question,
                    question_revision=1, input_manifest=[], input_digest=uuid4().hex.ljust(64, "0"),
                    attempt_count=1, completed_at=now_utc())
                session.add(run)
                session.flush()
                return run
            paper = Paper(project_id=project_id, title="Synthetic source", source_filename="source.pdf",
                storage_key="migration-source.pdf", parse_status="parsed", page_count=1)
            session.add(paper)
            session.flush()
            chunk = PaperChunk(paper_id=paper.id, page_number=1, chunk_index=0, text="A and B were compared.")
            session.add(chunk)
            session.flush()
            evidence = Evidence(project_id=project_id, paper_chunk_id=chunk.id,
                source_text="A and B were compared.", evidence_type="reported_result",
                selection_status="selected", question_revision=1)
            session.add(evidence)
            claim = Claim(project_id=project_id, text="A and B were compared.", claim_type="comparison",
                human_grounding_status="supported", human_reviewed_at=now_utc(), confidence="medium",
                uncertainty="Synthetic source", selection_status="selected", question_revision=1,
                origin="model", generation_run_id=generation("claim").id)
            session.add(claim)
            session.flush()
            session.add(ClaimEvidence(claim_id=claim.id, evidence_id=evidence.id, role="supports"))
            landscape = ResearchLandscape(project_id=project_id, summary="Comparison is limited.",
                uncertainty="Synthetic source", review_status="accepted", reviewed_at=now_utc(),
                question_revision=1, origin="model", generation_run_id=generation("landscape").id)
            session.add(landscape)
            session.flush()
            gap = ResearchGap(project_id=project_id, landscape_id=landscape.id, title="Small comparison",
                description="More observations are needed here.", gap_type="limited_dataset",
                why_it_matters="Test stability", uncertainty="Synthetic source", assumptions=[],
                novelty_scope="supplied_literature", selection_status="selected", question_revision=1,
                origin="model", generation_run_id=generation("gap").id)
            session.add(gap)
            session.flush()
            hypothesis = Hypothesis(project_id=project_id, research_gap_id=gap.id,
                statement="A may differ from B.", rationale="Limited data", independent_variable="Group",
                dependent_variable="Score", control_variables=[], falsification_condition="No detectable difference",
                assumptions=[], uncertainty="Small dataset", selection_status="selected", question_revision=1,
                origin="model", generation_run_id=generation("hypothesis").id)
            session.add(hypothesis)
            session.flush()
            plan = ExperimentPlan(hypothesis_id=hypothesis.id, objective="Compare groups",
                independent_variable="Group", dependent_variable="Score", control_variables=["batch"],
                experimental_groups=["A", "B"], baseline="B", dataset_requirements="Synthetic CSV",
                metrics=["score"], procedure="Measure both groups", analysis_approach="Welch test",
                expected_observations=["Possible difference"], falsification_criteria="No detectable difference",
                confounders=["batch"], limitations=["small data"], assumptions=[], uncertainty="Pilot",
                review_status="accepted", reviewed_at=now_utc(), question_revision=1, origin="model",
                generation_run_id=generation("experiment_plan").id)
            session.add(plan)
            session.flush()
            dataset = Dataset(project_id=project_id, experiment_plan_id=plan.id, original_filename="data.csv",
                storage_key="migration-data.csv", byte_size=24, sha256="a" * 64, parser_version="fixture")
            session.add(dataset)
            session.flush()
            version = DatasetVersion(dataset_id=dataset.id, kind="raw", storage_key="migration-version.csv",
                sha256="b" * 64, row_count=2, column_count=2, profile={"columns": ["group", "score"]})
            session.add(version)
            session.flush()
            spec = AnalysisSpec(project_id=project_id, experiment_plan_id=plan.id,
                hypothesis_id=hypothesis.id, dataset_version_id=version.id, method="welch_t",
                column_mapping={"outcome": "score", "group": "group"}, parameters={"groups": ["A", "B"]},
                rationale="Accepted comparison", origin="human", review_status="accepted")
            session.add(spec)
            session.flush()
            analysis = AnalysisRun(project_id=project_id, analysis_spec_id=spec.id,
                dataset_version_id=version.id, hypothesis_id=hypothesis.id, method="welch_t",
                column_mapping=spec.column_mapping, parameters=spec.parameters, status="succeeded",
                engine_version="fixture", completed_at=now_utc())
            session.add(analysis)
            session.flush()
            result = StatisticalResult(project_id=project_id, analysis_run_id=analysis.id,
                method="welch_t", values={"p_value": 0.4})
            session.add(result)
            session.flush()
            interpretation = ResultInterpretation(project_id=project_id, analysis_run_id=analysis.id,
                summary="The analysis is inconclusive.", hypothesis_relationship="Undetermined.",
                uncertainty="Small sample", limitations=["small data"], alternative_explanations=[],
                statistical_result_ids=[result.id], figure_artifact_ids=[], ai_assessment={},
                review_status="accepted", question_revision=1, origin="model",
                generation_run_id=generation("result_interpretation").id)
            session.add(interpretation)
            session.commit()
            preserved = {"paper": paper.id, "chunk": chunk.id, "evidence": evidence.id,
                "claim": claim.id, "plan": plan.id, "dataset": dataset.id, "version": version.id,
                "spec": spec.id, "analysis": analysis.id, "result": result.id,
                "interpretation": interpretation.id}
        alembic("upgrade", "head")
        with engine.connect() as connection:
            for table, key in (("papers", "paper"), ("paper_chunks", "chunk"), ("evidence", "evidence"),
                               ("claims", "claim"), ("experiment_plans", "plan"), ("datasets", "dataset"),
                               ("dataset_versions", "version"), ("analysis_specs", "spec"),
                               ("analysis_runs", "analysis"), ("statistical_results", "result"),
                               ("result_interpretations", "interpretation")):
                assert connection.scalar(text(f"SELECT count(*) FROM {table} WHERE id=:id"),
                    {"id": preserved[key]}) == 1
            assert connection.scalar(text("SELECT count(*) FROM claim_evidence WHERE claim_id=:claim AND evidence_id=:evidence"),
                preserved) == 1
        with Session(engine) as session:
            for status in ("started", "failed", "succeeded"):
                run = GenerationRun(project_id=project_id, stage="research_report", status=status,
                    provider="fixture", model="fixture", prompt_name="fixture", prompt_version="m6",
                    prompt_hash="a" * 64, schema_version="m6", research_question_snapshot="Synthetic question?",
                    question_revision=1, input_manifest=[], input_digest="b" * 64, attempt_count=0,
                    completed_at=now_utc() if status != "started" else None)
                session.add(run)
                session.flush()
                if status == "succeeded":
                    session.add(ResearchReport(project_id=project_id, question_revision=1,
                        title="Fixture report", origin="model", generation_run_id=run.id,
                        review_status="accepted", source_bundle={"evidence": [{"id": preserved["evidence"],
                            "paper_chunk_id": preserved["chunk"], "paper_id": preserved["paper"],
                            "page_number": 1}], "results": [{"id": preserved["result"]}],
                            "interpretation": {"id": preserved["interpretation"]}},
                        source_bundle_hash="c" * 64, narrative_blocks=[], reviewed_at=now_utc()))
            session.commit()
        alembic("downgrade", "0009_m5_data_analysis")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM projects WHERE id=:id"), {"id": project_id}) == 1
            assert connection.scalar(text("SELECT count(*) FROM generation_runs WHERE stage='research_report'")) == 0
            assert connection.scalar(text("SELECT to_regclass('public.datasets')")) == "datasets"
            assert connection.scalar(text("SELECT count(*) FROM result_interpretations WHERE id=:id"),
                {"id": preserved["interpretation"]}) == 1
            assert connection.scalar(text("SELECT count(*) FROM claim_evidence WHERE claim_id=:claim AND evidence_id=:evidence"),
                preserved) == 1
    finally:
        if engine is not None:
            engine.dispose()
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database_name)))
