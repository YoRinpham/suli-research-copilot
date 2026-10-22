import json

import pytest
from pydantic import ValidationError

from app.services.m4_schemas import GapBatch, GapProposal, HypothesisBatch, M4Assessment, PlanProposal
from app.prompts.m4 import GAP_PROMPT, GAP_STRUCTURED_REPAIR, HYPOTHESIS_PROMPT, M4_ASSESSMENT_REPAIR, PLAN_PROMPT
from app.prompts.scientific import STRUCTURED_REPAIR
from app.persistence.base import Base
import app.persistence.models  # noqa: F401 - register model tables
from app.services.m4 import M4Service


def test_gap_is_corpus_bounded_and_has_sources():
    gap = {"title": "Condition C", "description": "Within the supplied literature, C is underexplored.",
           "gap_type": "underexplored_variable", "why_it_matters": "The effect is uncertain.",
           "uncertainty": "Only three papers were supplied.", "assumptions": [],
           "novelty_scope": "supplied_literature", "landscape_item_ids": ["i1"],
           "claim_ids": ["c1"], "evidence_for_ids": ["e1"], "evidence_against_ids": []}
    assert GapBatch.model_validate({"gaps": [gap]}).gaps[0].novelty_scope == "supplied_literature"
    for bad in ({**gap, "novelty_scope": "global_field"}, {**gap, "landscape_item_ids": []},
                {**gap, "evidence_for_ids": [], "evidence_against_ids": ["e1"]},
                {**gap, "evidence_for_ids": [], "evidence_against_ids": []},
                {**gap, "selection_status": "selected"}, {**gap, "evidence_for_ids": ["e1"], "evidence_against_ids": ["e1"]}):
        with pytest.raises(ValidationError):
            GapBatch.model_validate({"gaps": [bad]})


def test_hypothesis_requires_falsification_and_distinct_evidence_roles():
    hypothesis = {"statement": "Under C, A will outperform B on M.", "rationale": "Gap G motivates testing.",
                  "independent_variable": "A versus B", "dependent_variable": "M", "control_variables": ["C"],
                  "falsification_condition": "A does not outperform B on M under C.", "assumptions": [],
                  "uncertainty": "Small corpus", "evidence_motivating_ids": ["e1"],
                  "evidence_challenging_ids": [], "claim_ids": ["c1"]}
    assert HypothesisBatch.model_validate({"hypotheses": [hypothesis]}).hypotheses[0].falsification_condition
    for bad in ({**hypothesis, "falsification_condition": " "},
                {**hypothesis, "evidence_challenging_ids": ["e1"]},
                {**hypothesis, "id": "invented"}):
        with pytest.raises(ValidationError):
            HypothesisBatch.model_validate({"hypotheses": [bad]})
    with pytest.raises(ValidationError, match="Evidence cannot occupy conflicting roles"):
        HypothesisBatch.model_validate({"hypotheses": [{**hypothesis, "evidence_challenging_ids": ["e1"]}]})
    assert HypothesisBatch.model_validate(
        {"hypotheses": [{**hypothesis, "claim_ids": ["e1"]}]}
    ).hypotheses[0].claim_ids == ["e1"]


def test_hypothesis_prompt_and_repair_state_exact_source_role_exclusion():
    prompt = HYPOTHESIS_PROMPT.text
    repair = HYPOTHESIS_PROMPT.bundle["structured_repair"]
    for instruction in (prompt, repair):
        assert "evidence_motivating_ids" in instruction
        assert "evidence_challenging_ids" in instruction
        assert "disjoint" in instruction
        assert "supplied" in instruction
    assert "hypothesis_source_role_conflict" in repair
    assert "preserve" in repair
    assert "do not invent" in repair.lower()


def test_plan_requires_test_design_without_completed_results():
    plan = {"objective": "Test H", "independent_variable": "A versus B", "dependent_variable": "M",
            "control_variables": ["C"], "experimental_groups": ["A", "B"], "baseline": "B",
            "dataset_requirements": "Dataset under C, access to be confirmed", "metrics": ["M"],
            "procedure": "Compare A and B under C", "analysis_approach": "Compare M between groups",
            "expected_observations": ["A may exceed B"], "falsification_criteria": "A does not exceed B on M",
            "confounders": ["dataset shift"], "limitations": ["resources unconfirmed"],
            "assumptions": ["dataset access requires confirmation"], "uncertainty": "Feasibility unknown"}
    assert PlanProposal.model_validate(plan).baseline == "B"
    for bad in ({**plan, "baseline": ""}, {**plan, "metrics": []}, {**plan, "metrics": [" "]},
                {**plan, "completed_result": "A won"}, {**plan, "review_status": "accepted"}):
        with pytest.raises(ValidationError):
            PlanProposal.model_validate(bad)


def test_m4_prompt_bundles_cover_assessments_and_repair():
    from app.prompts.m4 import GAP_ASSESSMENT_REPAIR
    for prompt in (GAP_PROMPT, HYPOTHESIS_PROMPT, PLAN_PROMPT):
        assert "generation" in prompt.bundle
        assert "assessment" in prompt.bundle
        if prompt is GAP_PROMPT:
            assert prompt.bundle["structured_repair"] == GAP_STRUCTURED_REPAIR
        elif prompt is PLAN_PROMPT:
            from app.prompts.m4 import PLAN_STRUCTURED_REPAIR
            assert prompt.bundle["structured_repair"] == PLAN_STRUCTURED_REPAIR
        from app.prompts.m4 import PLAN_ASSESSMENT_REPAIR
        assert prompt.bundle["assessment_repair"] == (GAP_ASSESSMENT_REPAIR if prompt is GAP_PROMPT else PLAN_ASSESSMENT_REPAIR if prompt is PLAN_PROMPT else M4_ASSESSMENT_REPAIR)
        assert "untrusted" in prompt.text


def test_m4_relational_lineage_tables_exist():
    tables = Base.metadata.tables
    for name in ("research_gaps", "gap_landscape_items", "gap_claims", "gap_evidence",
                 "hypotheses", "hypothesis_evidence", "hypothesis_claims", "experiment_plans"):
        assert name in tables
    assert "research_gap_id" in tables["hypotheses"].c
    assert "hypothesis_id" in tables["experiment_plans"].c
    assert "novelty_scope" in tables["research_gaps"].c
    assert "review_status" in tables["experiment_plans"].c
    assert "role" in tables["hypothesis_claims"].c
    assert "ck_gap_type" in {constraint.name for constraint in tables["research_gaps"].constraints}


def test_gap_source_ids_must_trace_through_selected_items_and_claims():
    proposal = GapBatch.model_validate({"gaps": [{"title": "C", "description": "Within supplied literature C is unclear",
        "gap_type": "underexplored_variable", "why_it_matters": "Test C", "uncertainty": "Small corpus",
        "assumptions": [], "novelty_scope": "supplied_literature", "landscape_item_ids": ["i1"],
        "claim_ids": ["c1"], "evidence_for_ids": ["e1"], "evidence_against_ids": []}]}).gaps[0]
    M4Service.validate_gap_sources(proposal, {"i1": {"claims": {"c1"}, "evidence": {"e1"}}},
                                   {"c1": {"e1"}})
    for bad in ({"i1": {"claims": {"c2"}, "evidence": {"e1"}}},
                {"i1": {"claims": {"c1"}, "evidence": {"e2"}}}):
        with pytest.raises(ValueError):
            M4Service.validate_gap_sources(proposal, bad, {"c1": {"e1"}})
    bypassed_schema = GapProposal.model_construct(**{
        **proposal.model_dump(), "evidence_for_ids": [], "evidence_against_ids": ["e1"]})
    with pytest.raises(ValueError):
        M4Service.validate_gap_sources(bypassed_schema,
            {"i1": {"claims": {"c1"}, "evidence": {"e1"}}}, {"c1": {"e1"}})


def test_hypothesis_source_whitelist_and_plan_required_structure():
    hypothesis = HypothesisBatch.model_validate({"hypotheses": [{"statement": "A will exceed B under C",
        "rationale": "Gap", "independent_variable": "A versus B", "dependent_variable": "M",
        "control_variables": ["C"], "falsification_condition": "A does not exceed B", "assumptions": [],
        "uncertainty": "small corpus", "evidence_motivating_ids": ["e1"],
        "evidence_challenging_ids": [], "claim_ids": ["c1"]}]}).hypotheses[0]
    M4Service.validate_hypothesis_sources(hypothesis, {"e1"}, {"c1"})
    with pytest.raises(ValueError):
        M4Service.validate_hypothesis_sources(hypothesis, {"e2"}, {"c1"})
    plan = PlanProposal.model_validate({"objective": "Test", "independent_variable": "A versus B",
        "dependent_variable": "M", "control_variables": ["C"], "experimental_groups": ["A", "B"],
        "baseline": "B", "dataset_requirements": "C data", "metrics": ["M"], "procedure": "Compare",
        "analysis_approach": "Contrast M", "expected_observations": [], "falsification_criteria": "No improvement",
        "confounders": [], "limitations": [], "assumptions": [], "uncertainty": "Small data"})
    assert plan.independent_variable and plan.dependent_variable and plan.metrics


def test_hypothesis_provider_context_projects_only_direct_gap_sources():
    info = {
        "gap_claim_ids": {"claim-allowed"},
        "gap_evidence_ids": {"evidence-for", "evidence-against"},
        "claim_sources": {
            "claim-allowed": {"evidence-for", "evidence-against", "evidence-excluded"},
            "claim-excluded": {"evidence-excluded"},
        },
        "context": {
            "gap": {"id": "gap", "landscape_item_ids": ["item-selected"],
                    "claim_ids": ["claim-allowed"], "evidence_for_ids": ["evidence-for"],
                    "evidence_against_ids": ["evidence-against"]},
            "landscape": {"id": "landscape", "summary": "UNAUTHORIZED_SUMMARY",
                          "uncertainty": "UNAUTHORIZED_UNCERTAINTY", "items": [
                              {"id": "item-selected", "category": "finding", "text": "UNAUTHORIZED_ITEM_TEXT",
                               "uncertainty": "UNAUTHORIZED_ITEM_UNCERTAINTY",
                               "claim_ids": ["claim-allowed", "claim-excluded"],
                               "evidence_ids": ["evidence-for", "evidence-against", "evidence-excluded"]},
                              {"id": "item-excluded", "category": "finding", "text": "EXCLUDED_ITEM_TEXT",
                               "uncertainty": "EXCLUDED_ITEM_UNCERTAINTY",
                               "claim_ids": ["claim-excluded"], "evidence_ids": ["evidence-excluded"]},
                          ]},
            "claims": [
                {"id": "claim-allowed", "text": "Allowed claim", "evidence_for_ids":
                 ["evidence-for", "evidence-excluded"], "evidence_against_ids": ["evidence-against"]},
                {"id": "claim-excluded", "text": "EXCLUDED_CLAIM_TEXT",
                 "evidence_for_ids": ["evidence-excluded"], "evidence_against_ids": []},
            ],
            "evidence": [{"id": evidence_id, "source_text": evidence_id} for evidence_id in
                         ("evidence-for", "evidence-against", "evidence-excluded")],
        },
    }

    context = M4Service._hypothesis_provider_context(info)

    assert {row["id"] for row in context["evidence"]} == info["gap_evidence_ids"]
    assert {row["id"] for row in context["claims"]} == info["gap_claim_ids"]
    assert context["claims"][0]["evidence_for_ids"] == ["evidence-for"]
    assert context["claims"][0]["evidence_against_ids"] == ["evidence-against"]
    assert context["landscape"] == {"id": "landscape", "items": [
        {"id": "item-selected", "category": "finding", "claim_ids": ["claim-allowed"],
         "evidence_ids": ["evidence-against", "evidence-for"]},
    ]}
    serialized = json.dumps(context)
    assert "evidence-excluded" not in serialized
    assert "claim-excluded" not in serialized
    assert "UNAUTHORIZED_" not in serialized and "EXCLUDED_" not in serialized
    visible_evidence = {row["id"] for row in context["evidence"]}
    visible_claims = {row["id"] for row in context["claims"]}
    for claim in context["claims"]:
        assert set(claim["evidence_for_ids"] + claim["evidence_against_ids"]) <= visible_evidence
    for item in context["landscape"]["items"]:
        assert set(item["claim_ids"]) <= visible_claims
        assert set(item["evidence_ids"]) <= visible_evidence


def test_m4_api_exposes_explicit_human_checkpoints():
    from app.main import create_app
    paths = create_app().openapi()["paths"]
    assert "/api/v1/projects/{project_id}/gaps/generate" in paths
    assert "patch" in paths["/api/v1/projects/{project_id}/gaps/{gap_id}/selection"]
    assert "patch" in paths["/api/v1/projects/{project_id}/hypotheses/{hypothesis_id}/selection"]
    assert "patch" in paths["/api/v1/projects/{project_id}/experiment-plans/{plan_id}/review"]
    count_schema = create_app().openapi()["components"]["schemas"]["HypothesisGenerate"]["properties"]["count"]
    assert "upper bound" in count_schema["description"]


def test_m4_evaluation_keeps_human_judgments_blank():
    from evaluation.m4_quality import evaluation_record, load_cases
    cases = load_cases()
    assert {"corpus_absence", "false_global_novelty", "unfalsifiable", "wrong_metric"} <= {item["id"] for item in cases}
    record = evaluation_record(cases[0], "qwen3.8-max")
    assert record["model"] == "qwen3.8-max"
    assert record["generator_proposal"] is None
    assert record["ai_assessment"] is None
    assert record["human_decision"] is None
    assert record["human_evaluation"]["label"] is None
    assert record["final_downstream_eligibility"] is None


def test_m4_assessment_can_cite_a_whitelisted_landscape():
    assessment = M4Assessment.model_validate({"verdict": "concerns", "risk_flags": [],
        "source_ids": ["landscape-1"], "justification": "The accepted landscape is limited."})
    M4Service._validate_assessment(assessment, {"landscape-1"})
    with pytest.raises(ValueError):
        M4Service._validate_assessment(assessment, {"other-landscape"})
