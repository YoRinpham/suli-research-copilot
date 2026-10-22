"""Opt-in Qwen M4 smoke using synthetic context and deterministic test-stage inputs."""

import json
import os
from pathlib import Path

import pytest

from app.config.settings import get_settings
from app.prompts.m4 import (GAP_ASSESS, GAP_PROMPT, HYPOTHESIS_ASSESS, HYPOTHESIS_PROMPT,
                            M4_ASSESSMENT_REPAIR, PLAN_ASSESS, PLAN_PROMPT)
from app.prompts.scientific import Prompt, STRUCTURED_REPAIR
from app.providers.llm import DashScopeLLMProvider, LLMFormatError
from app.services.m4 import M4Service
from app.services.m4_schemas import GapBatch, HypothesisBatch, M4Assessment, PlanProposal
from app.persistence.database import get_engine
from app.persistence.models import GapEvidence, GenerationRun, ResearchGap
from sqlalchemy.orm import Session
from test_m4_database import m4_case


pytestmark = pytest.mark.skipif(os.getenv("RUN_REAL_QWEN") != "1", reason="Requires explicit Qwen smoke opt-in")


@pytest.mark.skipif(os.getenv("RUN_DB_INTEGRATION") != "1", reason="Requires migrated PostgreSQL")
def test_real_qwen_gap_persists_candidate_with_support(m4_case):
    settings = get_settings()
    if settings.qwen_api_key is None:
        pytest.skip("Qwen credential unavailable")
    client, _fake, (project_id, landscape_id, _item_id, _claim_id, evidence_id, *_rest) = m4_case
    client.app.state.llm_provider = DashScopeLLMProvider(
        settings.qwen_api_key.get_secret_value(), "qwen3.8-max", 120, 0,
        base_url=settings.qwen_base_url)
    response = client.post(f"/api/v1/projects/{project_id}/gaps/generate", json={
        "landscape_id": landscape_id, "expected_question_revision": 1})
    assert response.status_code == 201, response.text
    gap = response.json()["gaps"][0]
    assert gap["evidence_for_ids"] == [evidence_id]
    assert gap["selection_status"] == "candidate"
    assert not gap["eligible_for_generation"]
    with Session(get_engine()) as session:
        assert session.get(ResearchGap, gap["id"]) is not None
        assert session.get(GapEvidence, (gap["id"], evidence_id)).role == "supports"
        run = session.get(GenerationRun, gap["generation_run_id"])
        assert run.status == "succeeded" and run.stage == "gap" and run.model == "qwen3.8-max"


def test_real_qwen_m4_synthetic_smoke():
    settings = get_settings()
    if settings.qwen_api_key is None:
        pytest.skip("Qwen credential unavailable")
    provider = DashScopeLLMProvider(settings.qwen_api_key.get_secret_value(), "qwen3.8-max", 120, 0,
                                   base_url=settings.qwen_base_url)
    landscape = {"id": "landscape-synthetic", "summary": "One supplied paper evaluated A on Dataset D but not Condition C.",
                 "uncertainty": "Only one synthetic paper supplied.", "items": [{"id": "item-synthetic",
                 "category": "finding", "text": "Condition C was not evaluated.", "uncertainty": "Single source",
                 "claim_ids": ["claim-synthetic"], "evidence_ids": ["evidence-synthetic"]}]}
    evidence = [{"id": "evidence-synthetic", "source_text": "Method A was evaluated on Dataset D; Condition C was not evaluated.",
                 "paper_id": "paper-synthetic", "page_number": 1}]
    claims = [{"id": "claim-synthetic", "text": "Condition C was not evaluated in the supplied paper.",
               "claim_type": "limitation", "human_grounding_status": "supported", "uncertainty": "Single paper",
               "evidence_for_ids": ["evidence-synthetic"], "evidence_against_ids": []}]
    context = {"research_question": "Does A improve accuracy over B under Condition C?",
               "landscape": landscape, "claims": claims, "evidence": evidence}
    calls = []

    def call(prompt: Prompt, schema, payload, validate=lambda output: None):
        attempts = 0
        try:
            result = provider.generate_structured(prompt.text, schema, payload)
            attempts = result.attempt_count
            validate(result.output)
        except (LLMFormatError, ValueError) as exc:
            if isinstance(exc, LLMFormatError):
                attempts = exc.attempt_count
            instruction = M4_ASSESSMENT_REPAIR if schema is M4Assessment else STRUCTURED_REPAIR
            result = provider.generate_structured(prompt.text + "\n" + instruction, schema, payload)
            attempts += result.attempt_count
            validate(result.output)
        calls.append({"name": prompt.name, "attempts": attempts, "model": result.model_name})
        return result.output

    def check_gaps(batch):
        for item in batch.gaps:
            M4Service.validate_gap_sources(item,
                {"item-synthetic": {"claims": {"claim-synthetic"}, "evidence": {"evidence-synthetic"}}},
                {"claim-synthetic": {"evidence-synthetic"}})

    gap_batch = call(GAP_PROMPT, GapBatch, context, check_gaps)
    gap = gap_batch.gaps[0]
    gap_assessment = call(Prompt("gap_assessment", GAP_PROMPT.version, GAP_ASSESS, {}), M4Assessment,
                          {"scientific_input": context, "proposal": gap.model_dump()},
                          lambda value: M4Service._validate_assessment(value,
                              {"landscape-synthetic", "item-synthetic", "claim-synthetic", "evidence-synthetic"}))

    # The following input is a deterministic test action, not real researcher approval.
    selected_gap = {"id": "gap-test-selected", **gap.model_dump()}
    hypothesis_context = {**context, "gap": selected_gap, "requested_count": 1}
    hypotheses = call(HYPOTHESIS_PROMPT, HypothesisBatch, hypothesis_context,
        lambda batch: [M4Service.validate_hypothesis_sources(item,
            {"evidence-synthetic"}, {"claim-synthetic"}) for item in batch.hypotheses])
    hypothesis = hypotheses.hypotheses[0]
    hypothesis_assessment = call(Prompt("hypothesis_assessment", HYPOTHESIS_PROMPT.version, HYPOTHESIS_ASSESS, {}),
        M4Assessment, {"scientific_input": hypothesis_context, "proposal": hypothesis.model_dump()},
        lambda value: M4Service._validate_assessment(value,
            {"landscape-synthetic", "gap-test-selected", "claim-synthetic", "evidence-synthetic"}))

    # A second deterministic test action selects this proposal only for the smoke path.
    plan_context = {**hypothesis_context, "hypothesis": {"id": "hypothesis-test-selected", **hypothesis.model_dump()}}
    plan = call(PLAN_PROMPT, PlanProposal, plan_context)
    plan_assessment = call(Prompt("plan_assessment", PLAN_PROMPT.version, PLAN_ASSESS, {}),
        M4Assessment, {"scientific_input": plan_context, "proposal": plan.model_dump()},
        lambda value: M4Service._validate_assessment(value,
            {"landscape-synthetic", "hypothesis-test-selected", "gap-test-selected", "claim-synthetic", "evidence-synthetic"}))

    record = {"model": "qwen3.8-max", "calls": calls,
        "generator_proposal": {"gap": gap.model_dump(), "hypothesis": hypothesis.model_dump(), "plan": plan.model_dump()},
        "ai_assessment": {"gap": gap_assessment.model_dump(), "hypothesis": hypothesis_assessment.model_dump(),
                          "plan": plan_assessment.model_dump()},
        "human_state_before_test_actions": {"gap": "candidate", "hypothesis": "candidate", "plan": "pending_review"},
        "downstream_eligibility_before_test_actions": {"gap": False, "hypothesis": False, "plan": False},
        "test_actions": ["deterministic_gap_selection", "deterministic_hypothesis_selection"],
        "real_human_approval": None, "final_downstream_eligibility": False,
        "prompt_bundle_hashes": {"gap": GAP_PROMPT.digest, "hypothesis": HYPOTHESIS_PROMPT.digest,
                                 "experiment_plan": PLAN_PROMPT.digest}}
    target = Path(__file__).parents[2] / "data" / "m4-live-results.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    assert len(calls) == 6


def test_real_qwen_m4_plan_synthetic_smoke():
    settings = get_settings()
    if settings.qwen_api_key is None:
        pytest.skip("Qwen credential unavailable")
    provider = DashScopeLLMProvider(settings.qwen_api_key.get_secret_value(), "qwen3.8-max", 120, 0,
                                   base_url=settings.qwen_base_url)
    hypothesis = {"id": "hypothesis-test-selected", "statement": "Under Condition C, A will exceed B on accuracy.",
        "rationale": "Synthetic accepted landscape has not tested C.", "independent_variable": "A versus B",
        "dependent_variable": "accuracy", "control_variables": ["Condition C"],
        "falsification_condition": "A does not exceed B on accuracy under C.", "assumptions": [],
        "uncertainty": "Only one synthetic source", "evidence_motivating_ids": ["evidence-synthetic"],
        "evidence_challenging_ids": [], "claim_ids": ["claim-synthetic"]}
    context = {"research_question": "Does A improve accuracy over B under C?",
               "gap": {"id": "gap-test-selected", "description": "Within the supplied literature, C lacks a comparison."},
               "hypothesis": hypothesis,
               "evidence": [{"id": "evidence-synthetic", "source_text": "C was not evaluated in the supplied synthetic paper."}]}
    result = provider.generate_structured(PLAN_PROMPT.text, PlanProposal, context)
    plan = result.output
    assert result.model_name == "qwen3.8-max"
    record = {"model": result.model_name, "prompt_bundle_hash": PLAN_PROMPT.digest,
              "provider_attempt_count": result.attempt_count, "generator_proposal": plan.model_dump(),
              "ai_assessment": None, "human_review_status": "pending_review",
              "real_human_approval": None, "final_downstream_eligibility": False}
    target = Path(__file__).parents[2] / "data" / "m4-live-plan-results.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    assert plan.independent_variable and plan.dependent_variable and plan.metrics


def test_real_qwen_m4_trap_assessments():
    settings = get_settings()
    if settings.qwen_api_key is None:
        pytest.skip("Qwen credential unavailable")
    provider = DashScopeLLMProvider(settings.qwen_api_key.get_secret_value(), "qwen3.8-max", 120, 0,
                                   base_url=settings.qwen_base_url)
    source = {"landscape": {"id": "landscape-synthetic", "summary": "Only one paper is supplied; C is not evaluated."},
              "claims": [{"id": "claim-synthetic", "text": "C was not evaluated in the supplied paper."}],
              "evidence": [{"id": "evidence-synthetic", "source_text": "C was not evaluated."}]}
    cases = (
        ("false_global_novelty", GAP_ASSESS, {"description": "No researcher has ever studied C.",
            "novelty_scope": "supplied_literature", "landscape_item_ids": ["item-synthetic"],
            "claim_ids": ["claim-synthetic"], "evidence_for_ids": ["evidence-synthetic"]}),
        ("unfalsifiable", HYPOTHESIS_ASSESS, {"statement": "A may be useful in some circumstances.",
            "falsification_condition": "Unknown", "independent_variable": "A versus B",
            "dependent_variable": "accuracy", "control_variables": ["Condition C"]}),
        ("unsupported_precision", HYPOTHESIS_ASSESS, {"statement": "A will improve accuracy by exactly 17.3%.",
            "falsification_condition": "The exact predicted effect is not observed.",
            "independent_variable": "A versus B", "dependent_variable": "accuracy"}),
        ("wrong_metric", PLAN_ASSESS, {"hypothesis": "A will exceed B on accuracy under C.",
            "plan": "Measure only latency under C", "dependent_variable": "latency", "metrics": ["latency"]}),
    )
    target = Path(__file__).parents[2] / "data" / "m4-live-trap-results.json"
    target.parent.mkdir(exist_ok=True)
    records = []
    for case_id, prompt_text, proposal in cases:
        context = {"scientific_input": source, "proposal": proposal}
        try:
            result = provider.generate_structured(prompt_text, M4Assessment, context)
            calls = result.attempt_count
        except LLMFormatError as exc:
            calls = exc.attempt_count
            result = provider.generate_structured(prompt_text + "\n" + M4_ASSESSMENT_REPAIR,
                M4Assessment, context)
            calls += result.attempt_count
        M4Service._validate_assessment(result.output,
            {"landscape-synthetic", "item-synthetic", "claim-synthetic", "evidence-synthetic"})
        records.append({"scenario_id": case_id, "model": result.model_name,
            "provider_attempt_count": calls, "ai_assessment": result.output.model_dump(),
            "human_scientific_judgment": None, "final_downstream_eligibility": None})
        target.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    assert len(records) == 4
    expected_flags = {"false_global_novelty": "global_novelty_overclaim",
                      "unfalsifiable": "unfalsifiable", "unsupported_precision": "unsupported_precision",
                      "wrong_metric": "wrong_metric"}
    for record in records:
        assert expected_flags[record["scenario_id"]] in record["ai_assessment"]["risk_flags"]
