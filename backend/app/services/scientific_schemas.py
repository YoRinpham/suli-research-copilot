"""Untrusted model output and human revision schemas."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ClaimType = Literal["finding", "method", "comparison", "limitation", "other"]
GroundingStatus = Literal["supported", "inferred", "unsupported", "insufficient_evidence"]
Confidence = Literal["low", "medium", "high", "unknown"]
Category = Literal["approach", "method", "dataset", "metric", "finding", "conflict"]
ClaimRiskFlag = Literal[
    "causal_amplification", "statistical_significance_amplification", "unsupported_comparison",
    "unsupported_generalization", "condition_population_loss", "metric_mismatch",
    "other_qualification_loss", "methodological_limitation", "statistical_evidence_limitation",
    "generalizability_limitation",
]


class StrictScientificModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("justification", check_fields=False)
    @classmethod
    def nonblank_justification(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Verifier justification cannot be blank")
        return value.strip()


class ClaimProposal(StrictScientificModel):
    text: str = Field(min_length=1, max_length=4000)
    claim_type: ClaimType
    grounding_status: GroundingStatus
    confidence: Confidence
    uncertainty: str = Field(min_length=1, max_length=2000)
    evidence_for_ids: list[str]
    evidence_against_ids: list[str]

    @field_validator("text", "uncertainty")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Scientific text cannot be blank")
        return value.strip()

    @model_validator(mode="after")
    def grounding_invariants(self) -> "ClaimProposal":
        if len(set(self.evidence_for_ids)) != len(self.evidence_for_ids) or len(set(self.evidence_against_ids)) != len(self.evidence_against_ids):
            raise ValueError("Duplicate Evidence reference")
        if set(self.evidence_for_ids) & set(self.evidence_against_ids):
            raise ValueError("Evidence cannot both support and challenge a Claim")
        if self.grounding_status == "supported" and not self.evidence_for_ids:
            raise ValueError("A supported Claim requires supporting Evidence")
        if self.grounding_status == "inferred":
            if not self.evidence_for_ids and not self.evidence_against_ids:
                raise ValueError("An inferred Claim requires source Evidence")
            if self.uncertainty == "none_identified":
                raise ValueError("An inferred Claim must explain the inference")
        return self


class ClaimBatch(StrictScientificModel):
    claims: list[ClaimProposal] = Field(min_length=1, max_length=20)


class HumanClaimRevision(StrictScientificModel):
    text: str = Field(min_length=1, max_length=4000)
    claim_type: ClaimType
    confidence: Confidence
    uncertainty: str = Field(min_length=1, max_length=2000)
    evidence_for_ids: list[str]
    evidence_against_ids: list[str]

    @model_validator(mode="after")
    def source_invariants(self) -> "HumanClaimRevision":
        if not self.text.strip() or not self.uncertainty.strip():
            raise ValueError("Scientific text cannot be blank")
        if not self.evidence_for_ids and not self.evidence_against_ids:
            raise ValueError("A human revision requires source Evidence")
        if len(set(self.evidence_for_ids)) != len(self.evidence_for_ids) or len(set(self.evidence_against_ids)) != len(self.evidence_against_ids):
            raise ValueError("Duplicate Evidence reference")
        if set(self.evidence_for_ids) & set(self.evidence_against_ids):
            raise ValueError("Evidence cannot both support and challenge a Claim")
        return self


class LandscapeItemProposal(StrictScientificModel):
    category: Category
    text: str = Field(min_length=1, max_length=4000)
    uncertainty: str = Field(min_length=1, max_length=2000)
    claim_ids: list[str] = Field(min_length=1)
    evidence_ids: list[str]

    @field_validator("text", "uncertainty")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Scientific text cannot be blank")
        return value.strip()

    @model_validator(mode="after")
    def source_invariants(self) -> "LandscapeItemProposal":
        if len(set(self.claim_ids)) != len(self.claim_ids) or len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("Duplicate source reference")
        if self.category == "conflict" and len(self.claim_ids) < 2:
            raise ValueError("A conflict requires at least two Claims")
        return self


class LandscapeOutput(StrictScientificModel):
    summary: str = Field(min_length=1, max_length=8000)
    uncertainty: str = Field(min_length=1, max_length=2000)
    items: list[LandscapeItemProposal] = Field(min_length=1, max_length=50)

    @field_validator("summary", "uncertainty")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Scientific text cannot be blank")
        return value.strip()


class SourceExcerpt(StrictScientificModel):
    evidence_id: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)

    @field_validator("excerpt")
    @classmethod
    def nonblank_excerpt(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Source excerpt cannot be blank")
        return value


class ClaimVerification(StrictScientificModel):
    verdict: Literal["directly_supported", "inferentially_supported", "unsupported", "contradicted", "insufficient_evidence"]
    evidence_refs: list[str]
    risk_flags: list[ClaimRiskFlag]
    justification: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def direct_support_has_reference(self) -> "ClaimVerification":
        if self.verdict == "directly_supported" and not self.evidence_refs:
            raise ValueError("Direct support requires a supporting Evidence reference")
        return self


Comparability = Literal["comparable", "different", "unknown"]


class ComparisonDimensions(StrictScientificModel):
    dataset: Comparability
    population: Comparability
    task: Comparability
    metric: Comparability
    experimental_condition: Comparability
    baseline: Comparability


class ConflictVerification(StrictScientificModel):
    verdict: Literal["direct_conflict", "conditional_difference", "incomparable", "not_conflict", "unclear"]
    asserts_direct_conflict: bool
    dimensions: ComparisonDimensions
    evidence_refs: list[str]
    support_excerpts: list[SourceExcerpt]
    justification: str = Field(min_length=1, max_length=1000)


class SummaryVerification(StrictScientificModel):
    verdict: Literal["supported", "unsupported_major_statement"]
    flagged_statements: list[str]
    evidence_refs: list[str]
    support_excerpts: list[SourceExcerpt]
    justification: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def unsupported_has_flag(self) -> "SummaryVerification":
        if self.verdict == "unsupported_major_statement" and not self.flagged_statements:
            raise ValueError("Unsupported summary requires a flagged statement")
        return self


class LandscapeItemVerification(StrictScientificModel):
    """New provider protocol: scientific judgments and references, never quotations."""

    verdict: Literal["direct_conflict", "conditional_difference", "incomparable", "not_conflict", "unclear"]
    asserts_direct_conflict: bool
    dimensions: ComparisonDimensions
    support_verdict: Literal["supported", "unsupported", "insufficient_evidence"]
    flagged_statements: list[str]
    qualifications: list[str]
    evidence_refs: list[str]
    justification: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def support_invariants(self) -> "LandscapeItemVerification":
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("Duplicate Evidence reference")
        if self.support_verdict == "supported" and (not self.evidence_refs or self.flagged_statements):
            raise ValueError("Supported item requires references and no unresolved flagged statements")
        return self


class LandscapeSummaryVerification(StrictScientificModel):
    """Summary critique is independent of item conflict/comparability judgments."""

    verdict: Literal["supported", "unsupported_major_statement"]
    flagged_statements: list[str]
    qualifications: list[str]
    evidence_refs: list[str]
    justification: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def support_invariants(self) -> "LandscapeSummaryVerification":
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("Duplicate Evidence reference")
        if self.verdict == "supported" and (not self.evidence_refs or self.flagged_statements):
            raise ValueError("Supported summary requires references and no unresolved flagged statements")
        if self.verdict == "unsupported_major_statement" and not self.flagged_statements:
            raise ValueError("Unsupported summary requires a flagged statement")
        return self


class SummaryRepair(StrictScientificModel):
    summary: str = Field(min_length=1, max_length=8000)
    uncertainty: str = Field(min_length=1, max_length=2000)

    @field_validator("summary", "uncertainty")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Scientific text cannot be blank")
        return value.strip()
