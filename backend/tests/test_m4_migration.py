"""Populated M4 downgrade must retain the complete M3 provenance chain."""

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
    Claim, ClaimEvidence, Evidence, ExperimentPlan, GapClaim, GapEvidence, GapLandscapeItem,
    GenerationRun, Hypothesis, HypothesisClaim, HypothesisEvidence, LandscapeItem,
    LandscapeItemClaim, LandscapeItemEvidence, Paper, PaperChunk, Project, ResearchGap,
    ResearchLandscape, now_utc,
)


pytestmark = pytest.mark.skipif(os.getenv("RUN_DB_INTEGRATION") != "1", reason="Requires disposable PostgreSQL database")


def test_populated_m4_downgrade_to_m3_preserves_m3_sources():
    configured = make_url(get_settings().database_url)
    admin_url = configured.set(database="postgres").render_as_string(hide_password=False)
    admin_url = admin_url.replace("postgresql+psycopg://", "postgresql://", 1)
    database_name = "m4_gate_" + uuid4().hex[:12]
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
            assert result.returncode == 0, result.stderr[-2000:]
            return result.stdout + result.stderr

        alembic("upgrade", "0006_m3_human_authority")
        assert "0006_m3_human_authority" in alembic("current")
        engine = create_engine(database_url)
        with Session(engine) as session:
            project = Project(title="M4 downgrade fixture", research_question="What has been studied?")
            session.add(project)
            session.flush()
            paper = Paper(project_id=project.id, source_filename="source.pdf", storage_key="source.pdf",
                          parse_status="indexed", page_count=1)
            session.add(paper)
            session.flush()
            chunk = PaperChunk(paper_id=paper.id, page_number=1, chunk_index=0, text="Condition C was not tested.")
            session.add(chunk)
            session.flush()
            evidence = Evidence(project_id=project.id, paper_chunk_id=chunk.id, source_text=chunk.text,
                                evidence_type="limitation", selection_status="selected", question_revision=1)
            session.add(evidence)
            session.flush()

            def run(stage: str, status: str) -> GenerationRun:
                record = GenerationRun(project_id=project.id, stage=stage, status=status, provider="fixture",
                    model="fixture", prompt_name="fixture", prompt_version="fixture", prompt_hash="a" * 64,
                    schema_version="fixture", research_question_snapshot=project.research_question,
                    question_revision=1, input_manifest=[], input_digest="a" * 64,
                    attempt_count=1, completed_at=now_utc() if status != "started" else None)
                session.add(record)
                session.flush()
                return record

            claim_run, landscape_run = run("claim", "succeeded"), run("landscape", "succeeded")
            claim = Claim(project_id=project.id, text="C was not tested.", claim_type="limitation",
                human_grounding_status="supported", human_reviewed_at=now_utc(), confidence="medium",
                uncertainty="One paper", selection_status="selected", question_revision=1,
                origin="model", generation_run_id=claim_run.id)
            session.add(claim)
            session.flush()
            session.add(ClaimEvidence(claim_id=claim.id, evidence_id=evidence.id, role="supports"))
            landscape = ResearchLandscape(project_id=project.id, summary="C remains untested here.",
                uncertainty="One paper", review_status="accepted", reviewed_at=now_utc(),
                question_revision=1, origin="model", generation_run_id=landscape_run.id)
            session.add(landscape)
            session.flush()
            item = LandscapeItem(landscape_id=landscape.id, category="finding", text="C was not tested.",
                                 uncertainty="One paper")
            session.add(item)
            session.flush()
            session.add_all([LandscapeItemClaim(item_id=item.id, claim_id=claim.id),
                             LandscapeItemEvidence(item_id=item.id, evidence_id=evidence.id)])
            m3_ids = (project.id, paper.id, chunk.id, evidence.id, claim.id, landscape.id,
                      item.id, claim_run.id, landscape_run.id)
            session.commit()

        alembic("upgrade", "head")
        with Session(engine) as session:
            project_id, _paper_id, _chunk_id, evidence_id, claim_id, landscape_id, item_id, *_ = m3_ids
            m4_runs = {stage: {} for stage in ("gap", "hypothesis", "experiment_plan")}
            for stage in m4_runs:
                for status in ("succeeded", "failed", "started"):
                    m4_runs[stage][status] = run_for_project(session, project_id, stage, status)
            gap = ResearchGap(project_id=project_id, landscape_id=landscape_id, title="Untested C",
                description="C may be a gap in this corpus.", gap_type="underexplored_variable",
                why_it_matters="Test C", uncertainty="One paper", assumptions=[],
                novelty_scope="supplied_literature", selection_status="selected", question_revision=1,
                origin="model", generation_run_id=m4_runs["gap"]["succeeded"])
            session.add(gap)
            session.flush()
            session.add_all([GapLandscapeItem(gap_id=gap.id, item_id=item_id),
                             GapClaim(gap_id=gap.id, claim_id=claim_id),
                             GapEvidence(gap_id=gap.id, evidence_id=evidence_id, role="supports")])
            hypothesis = Hypothesis(project_id=project_id, research_gap_id=gap.id,
                statement="A will exceed B under C", rationale="Untested C", independent_variable="A versus B",
                dependent_variable="accuracy", control_variables=[], falsification_condition="A does not exceed B",
                assumptions=[], uncertainty="One paper", selection_status="selected", question_revision=1,
                origin="model", generation_run_id=m4_runs["hypothesis"]["succeeded"])
            session.add(hypothesis)
            session.flush()
            session.add_all([HypothesisEvidence(hypothesis_id=hypothesis.id, evidence_id=evidence_id, role="motivates"),
                             HypothesisClaim(hypothesis_id=hypothesis.id, claim_id=claim_id, role="extends")])
            session.add(ExperimentPlan(hypothesis_id=hypothesis.id, objective="Test A and B under C",
                independent_variable="A versus B", dependent_variable="accuracy", control_variables=[],
                experimental_groups=["A", "B"], baseline="B", dataset_requirements="Data under C",
                metrics=["accuracy"], procedure="Compare groups", analysis_approach="Compare accuracy",
                expected_observations=[], falsification_criteria="No gain", confounders=[], limitations=[],
                assumptions=[], uncertainty="Data access unknown", review_status="pending_review",
                question_revision=1, origin="model", generation_run_id=m4_runs["experiment_plan"]["succeeded"]))
            session.commit()

        alembic("downgrade", "0006_m3_human_authority")
        assert "0006_m3_human_authority" in alembic("current")
        with engine.connect() as connection:
            project_id, paper_id, chunk_id, evidence_id, claim_id, landscape_id, item_id, claim_run_id, landscape_run_id = m3_ids
            for table_name in ("research_gaps", "hypotheses", "experiment_plans", "gap_evidence"):
                assert connection.scalar(text("SELECT to_regclass(:name)"), {"name": table_name}) is None
            assert connection.execute(text("SELECT stage, id FROM generation_runs ORDER BY stage")).all() == [
                ("claim", claim_run_id), ("landscape", landscape_run_id)]
            assert connection.scalar(text("SELECT title FROM projects WHERE id=:id"), {"id": project_id}) == "M4 downgrade fixture"
            assert connection.scalar(text("SELECT source_filename FROM papers WHERE id=:id"), {"id": paper_id}) == "source.pdf"
            assert connection.scalar(text("SELECT page_number FROM paper_chunks WHERE id=:id"), {"id": chunk_id}) == 1
            assert connection.scalar(text("SELECT source_text FROM evidence WHERE id=:id"), {"id": evidence_id}) == "Condition C was not tested."
            assert connection.scalar(text("SELECT text FROM claims WHERE id=:id"), {"id": claim_id}) == "C was not tested."
            assert connection.scalar(text("SELECT review_status FROM research_landscapes WHERE id=:id"), {"id": landscape_id}) == "accepted"
            assert connection.scalar(text("SELECT text FROM landscape_items WHERE id=:id"), {"id": item_id}) == "C was not tested."
            assert "'claim'" in connection.scalar(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='ck_generation_run_stage'"))
            assert "'gap'" not in connection.scalar(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='ck_generation_run_stage'"))
    finally:
        if engine is not None:
            engine.dispose()
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database_name)))


def run_for_project(session: Session, project_id: str, stage: str, status: str) -> str:
    record = GenerationRun(project_id=project_id, stage=stage, status=status, provider="fixture",
        model="fixture", prompt_name="fixture", prompt_version="fixture", prompt_hash="b" * 64,
        schema_version="fixture", research_question_snapshot="What has been studied?", question_revision=1,
        input_manifest=[], input_digest="b" * 64, attempt_count=1,
        completed_at=now_utc() if status != "started" else None)
    session.add(record)
    session.flush()
    return record.id
