"""Strict M4 scientific proposals and advisory assessments."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


GapType = Literal[
    "contradictory_findings", "underexplored_variable", "missing_comparison", "limited_dataset",
    "insufficient_replication", "methodological_limitation", "untested_interaction",
    "unexplored_domain", "unresolved_cause", "evaluation_inconsistency",
]


class M4Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("title", "description", "why_it_matters", "uncertainty", "statement", "rationale",
                     "independent_variable", "dependent_variable", "falsification_condition", "objective",
                     "baseline", "dataset_requirements", "procedure", "analysis_approach",
                     "falsification_criteria", "justification", check_fields=False)
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Scientific text cannot be blank")
        return value.strip()

    @field_validator("assumptions", "landscape_item_ids", "claim_ids", "evidence_for_ids", "evidence_against_ids",
                     "control_variables", "evidence_motivating_ids", "evidence_challenging_ids",
                     "experimental_groups", "metrics", "expected_observations", "confounders", "limitations",
                     "risk_flags", "source_ids", check_fields=False)
    @classmethod
    def nonblank_list_entries(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("List entries cannot be blank")
        return value


class GapProposal(M4Model):
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1, max_length=4000)
    gap_type: GapType
    why_it_matters: str = Field(min_length=1, max_length=4000)
    uncertainty: str = Field(min_length=1, max_length=2000)
    assumptions: list[str]
    novelty_scope: Literal["supplied_literature"]
    landscape_item_ids: list[str] = Field(min_length=1)
    claim_ids: list[str] = Field(min_length=1)
    evidence_for_ids: list[str] = Field(min_length=1)
    evidence_against_ids: list[str]

    @model_validator(mode="after")
    def source_roles(self) -> "GapProposal":
        for ids in (self.landscape_item_ids, self.claim_ids, self.evidence_for_ids, self.evidence_against_ids):
            if len(ids) != len(set(ids)):
                raise ValueError("Duplicate source ID")
        if set(self.evidence_for_ids) & set(self.evidence_against_ids):
            raise ValueError("Evidence cannot occupy conflicting roles")
        return self


class GapBatch(M4Model):
    gaps: list[GapProposal] = Field(min_length=1, max_length=10)


class HypothesisProposal(M4Model):
    statement: str = Field(min_length=1, max_length=4000)
    rationale: str = Field(min_length=1, max_length=4000)
    independent_variable: str = Field(min_length=1, max_length=1000)
    dependent_variable: str = Field(min_length=1, max_length=1000)
    control_variables: list[str]
    falsification_condition: str = Field(min_length=1, max_length=2000)
    assumptions: list[str]
    uncertainty: str = Field(min_length=1, max_length=2000)
    evidence_motivating_ids: list[str] = Field(min_length=1)
    evidence_challenging_ids: list[str]
    claim_ids: list[str]

    @model_validator(mode="after")
    def source_roles(self) -> "HypothesisProposal":
        for ids in (self.evidence_motivating_ids, self.evidence_challenging_ids, self.claim_ids):
            if len(ids) != len(set(ids)):
                raise ValueError("Duplicate source ID")
        if set(self.evidence_motivating_ids) & set(self.evidence_challenging_ids):
            raise ValueError("Evidence cannot occupy conflicting roles")
        return self


class HypothesisBatch(M4Model):
    hypotheses: list[HypothesisProposal] = Field(min_length=1, max_length=5)


class PlanProposal(M4Model):
    objective: str = Field(min_length=1, max_length=4000)
    independent_variable: str = Field(min_length=1, max_length=1000)
    dependent_variable: str = Field(min_length=1, max_length=1000)
    control_variables: list[str]
    experimental_groups: list[str] = Field(min_length=1)
    baseline: str = Field(min_length=1, max_length=1000)
    dataset_requirements: str = Field(min_length=1, max_length=4000)
    metrics: list[str] = Field(min_length=1)
    procedure: str = Field(min_length=1, max_length=4000)
    analysis_approach: str = Field(min_length=1, max_length=4000)
    expected_observations: list[str]
    falsification_criteria: str = Field(min_length=1, max_length=2000)
    confounders: list[str]
    limitations: list[str]
    assumptions: list[str]
    uncertainty: str = Field(min_length=1, max_length=2000)


class M4Assessment(M4Model):
    verdict: Literal["aligned", "concerns", "misaligned"]
    risk_flags: list[Literal[
        "global_novelty_overclaim", "weak_source_link", "restated_finding", "unfalsifiable",
        "gap_misalignment", "condition_loss", "unsupported_precision", "unsupported_causality",
        "wrong_variable", "wrong_metric", "missing_comparator", "context_mismatch",
        "cannot_falsify", "unsupported_assumption", "feasibility_unknown", "other",
    ]]
    source_ids: list[str]
    justification: str = Field(min_length=1, max_length=2000)
