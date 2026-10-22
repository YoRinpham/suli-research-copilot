"""Opt-in synthetic M5 smoke against the configured production Qwen provider."""

import json
import os
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from test_m4_database import m4_case
from test_m5_database import m5_case

from app.config.settings import get_settings
from app.persistence.database import get_engine
from app.persistence.models import CleaningPlan, CleaningRun, GenerationRun
from app.providers.llm import DashScopeLLMProvider


pytestmark = [
    pytest.mark.skipif(os.getenv("RUN_REAL_QWEN") != "1", reason="Requires explicit Qwen smoke opt-in"),
    pytest.mark.skipif(os.getenv("RUN_DB_INTEGRATION") != "1", reason="Requires migrated PostgreSQL"),
]


def test_real_qwen_m5_cleaning_proposal_only(m5_case):
    """One bounded live call; a researcher must still review any proposal."""
    settings = get_settings()
    if settings.qwen_api_key is None:
        pytest.skip("Qwen credential unavailable")
    assert settings.qwen_model == "qwen3.8-max"
    client, _fake, project_id, plan_id, *_rest, root = m5_case
    client.app.state.llm_provider = DashScopeLLMProvider(
        settings.qwen_api_key.get_secret_value(), settings.qwen_model, 30, 0,
        base_url=settings.qwen_base_url)

    uploaded = client.post(f"{root}/experiment-plans/{plan_id}/datasets?expected_question_revision=1",
        files={"file": ("synthetic-m5-cleaning.csv", b"score,group\n0,A\n1,A\n2,B\n", "text/csv")})
    assert uploaded.status_code == 201, uploaded.text
    version_id = uploaded.json()["versions"][0]["id"]
    response = client.post(f"{root}/dataset-versions/{version_id}/cleaning-plans/suggest",
        json={"expected_question_revision": 1})
    if response.status_code != 201:
        error = response.json()["error"]
        run_id = error.get("details", {}).get("generation_run_id")
        with Session(get_engine()) as session:
            run = session.get(GenerationRun, run_id)
            assert run is not None and run.status == "failed"
            assert not list(session.scalars(select(CleaningPlan).where(CleaningPlan.project_id == project_id)))
        pytest.fail(f"Qwen cleaning proposal failed: HTTP {response.status_code}; "
                    f"code={error['code']}; category={error.get('details', {}).get('provider_error_category')}; "
                    f"run_error_type={run.error_type}; attempts={run.attempt_count}")

    plan = response.json()["cleaning_plan"]
    assert plan["review_status"] == "pending_review"
    assert plan["operations"]
    with Session(get_engine()) as session:
        persisted = session.get(CleaningPlan, plan["id"])
        assert persisted is not None and persisted.operations == plan["operations"]
        assert not list(session.scalars(select(CleaningRun).where(CleaningRun.cleaning_plan_id == plan["id"])))


def test_real_qwen_m5_synthetic_full_path(m5_case):
    settings = get_settings()
    if settings.qwen_api_key is None:
        pytest.skip("Qwen credential unavailable")
    client, _fake, _project_id, plan_id, _hypothesis_id, _gap_id, _evidence_id, _paper_id, root = m5_case
    client.app.state.llm_provider = DashScopeLLMProvider(
        settings.qwen_api_key.get_secret_value(), "qwen3.8-max", 120, 0,
        base_url=settings.qwen_base_url)

    # The synthetic rows are public test data; approval below is a labeled test action.
    csv_bytes = b"score,group\n1,A\n2,A\n3,A\n4,A\n4,B\n5,B\n6,B\n7,B\n7,B\n"
    uploaded = client.post(f"{root}/experiment-plans/{plan_id}/datasets?expected_question_revision=1",
        files={"file": ("synthetic-m5.csv", csv_bytes, "text/csv")})
    assert uploaded.status_code == 201, uploaded.text
    raw = uploaded.json()["versions"][0]
    cleaning = client.post(f"{root}/dataset-versions/{raw['id']}/cleaning-plans/suggest",
        json={"expected_question_revision": 1})
    assert cleaning.status_code == 201, cleaning.text
    cleaning_plan = cleaning.json()["cleaning_plan"]
    assert cleaning_plan["review_status"] == "pending_review"
    assert client.patch(f"{root}/cleaning-plans/{cleaning_plan['id']}/review", json={
        "review_status": "accepted", "expected_question_revision": 1}).status_code == 200
    cleaned = client.post(f"{root}/cleaning-plans/{cleaning_plan['id']}/execute",
        json={"expected_question_revision": 1})
    assert cleaned.status_code == 201, cleaned.text
    version = cleaned.json()["output_version"]

    proposed = client.post(f"{root}/dataset-versions/{version['id']}/analysis-specs/suggest",
        json={"expected_question_revision": 1})
    assert proposed.status_code == 201, proposed.text
    spec = proposed.json()["analysis_spec"]
    assert spec["review_status"] == "pending_review"
    assert client.patch(f"{root}/analysis-specs/{spec['id']}/review", json={
        "review_status": "accepted", "expected_question_revision": 1}).status_code == 200
    analyzed = client.post(f"{root}/analysis-specs/{spec['id']}/execute",
        json={"expected_question_revision": 1})
    assert analyzed.status_code == 201, analyzed.text
    run = analyzed.json()
    assert run["result"] and run["result"]["values"]

    figure = client.post(f"{root}/dataset-versions/{version['id']}/figures", json={
        "expected_question_revision": 1, "chart_type": "histogram", "x_column": "score",
        "title": "Synthetic scores", "analysis_run_id": run["id"],
        "statistical_result_id": run["result"]["id"]})
    assert figure.status_code == 201, figure.text
    interpretation = client.post(f"{root}/analysis-runs/{run['id']}/interpretations/generate",
        json={"expected_question_revision": 1})
    assert interpretation.status_code == 201, interpretation.text
    draft = interpretation.json()["interpretation"]
    assert draft["review_status"] == "pending_review" and not draft["eligible_for_future_report"]
    assert draft["statistical_result_ids"] == [run["result"]["id"]]
    assert draft["figure_artifact_ids"] == [figure.json()["id"]]

    record = {"model": "qwen3.8-max", "synthetic_data": True,
        "test_actions": ["accepted_existing_plan_from_m4_fixture", "accepted_cleaning_proposal_for_test",
                         "accepted_analysis_proposal_for_test"],
        "cleaning_proposal": cleaning_plan, "analysis_proposal": spec,
        "deterministic_result": run["result"], "figure_id": figure.json()["id"],
        "advisory_interpretation": draft, "real_human_approval": None}
    target = Path(__file__).parents[2] / "data" / "m5-live-results.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
