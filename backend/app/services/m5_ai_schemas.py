"""Strict M5 model-output contracts; no server authority comes from these schemas."""

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CleaningProposal(StrictOutput):
    operations: list[dict[str, Any]] = Field(max_length=20)
    rationale: str = Field(min_length=1, max_length=2000)


class AnalysisProposal(StrictOutput):
    method: Literal["descriptive", "grouped_descriptive", "welch_t", "paired_t", "pearson", "spearman"]
    column_mapping: dict[str, str]
    parameters: dict[str, Any]
    rationale: str = Field(min_length=1, max_length=2000)


class InterpretationProposal(StrictOutput):
    summary: str = Field(min_length=1, max_length=3000)
    hypothesis_relationship: str = Field(min_length=1, max_length=2000)
    uncertainty: str = Field(min_length=1, max_length=2000)
    limitations: list[str] = Field(max_length=20)
    alternative_explanations: list[str] = Field(max_length=20)
    statistical_result_ids: list[str] = Field(min_length=1)
    figure_artifact_ids: list[str]

    @field_validator("limitations", "alternative_explanations")
    @classmethod
    def nonempty_entries(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("Interpretation entries must be nonempty")
        return values


class InterpretationAssessment(StrictOutput):
    verdict: Literal["cautious", "risky"]
    risk_flags: list[str]
    justification: str = Field(min_length=1)


OVERCLAIM = re.compile(
    r"\b(proves?|proven|confirmed|refuted|caus(?:es|ed)|no effect exists|there is no effect|"
    r"null hypothesis is true|scientifically superior|definitely|always|statistically significant)\b|"
    r"证明|证实|已确认|不存在效应|必然|一定导致|科学上更优|统计学显著",
    re.IGNORECASE,
)


def validate_interpretation_language(proposal: InterpretationProposal) -> None:
    # Numeric values are rendered from StatisticalResult; IDs belong in reference arrays, not prose.
    texts = [proposal.summary, proposal.hypothesis_relationship, proposal.uncertainty,
             *proposal.limitations, *proposal.alternative_explanations]
    if any(re.search(r"\d", value) for value in texts):
        raise ValueError("AI prose must not reproduce numerical results")
    if any(OVERCLAIM.search(value) for value in texts):
        raise ValueError("AI prose overstates the statistical evidence")
