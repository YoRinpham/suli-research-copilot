from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from app.services.scientific_schemas import HumanClaimRevision, LandscapeOutput


class ProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    research_question: str = Field(min_length=1, max_length=10000)

    @field_validator("title", "research_question")
    @classmethod
    def require_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Field must not be blank")
        return value.strip()


class ProjectUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    research_question: str | None = Field(default=None, min_length=1, max_length=10000)
    expected_question_revision: int = Field(ge=1)

    @field_validator("title", "research_question")
    @classmethod
    def require_nonblank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Field must not be blank")
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def require_change(self) -> "ProjectUpdate":
        if self.title is None and self.research_question is None:
            raise ValueError("At least one field must be provided")
        return self


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    research_question: str
    question_revision: int
    status: str
    created_at: datetime
    updated_at: datetime


class PaperRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    title: str | None
    source_filename: str
    parse_status: str
    quality_note: str | None
    page_count: int | None
    created_at: datetime


EvidenceType = Literal["reported_result", "method_description", "dataset_detail", "limitation", "author_claim", "other"]
SelectionStatus = Literal["selected", "rejected"]


class EvidenceCreate(BaseModel):
    paper_chunk_id: str
    source_text: str = Field(min_length=1, max_length=10000)
    evidence_type: EvidenceType
    relevance_note: str | None = Field(default=None, max_length=2000)
    expected_question_revision: int = Field(ge=1)

    @field_validator("source_text")
    @classmethod
    def nonblank_source(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Source text must not be blank")
        return value


class EvidenceSelection(BaseModel):
    selection_status: SelectionStatus
    expected_question_revision: int = Field(ge=1)


class EvidenceRead(BaseModel):
    id: str
    project_id: str
    paper_chunk_id: str
    paper_id: str
    paper_title: str | None
    source_filename: str
    page_number: int
    section: str | None
    source_text: str
    evidence_type: EvidenceType
    relevance_note: str | None
    selection_status: SelectionStatus
    question_revision: int
    paper_file_url: str


class RetrievalRequest(BaseModel):
    query: str | None = Field(default=None, max_length=10000)
    top_k: int | None = Field(default=None, ge=1)
    expected_question_revision: int = Field(ge=1)


class RetrievalResult(BaseModel):
    chunk_id: str
    paper_id: str
    paper_title: str | None
    page_number: int
    section: str | None
    text: str
    similarity_score: float  # Cosine relevance, not scientific credibility.


class RetrievalResponse(BaseModel):
    query: str
    results: list[RetrievalResult]


class ClaimGenerate(BaseModel):
    evidence_ids: list[str]
    expected_question_revision: int = Field(ge=1)


class ClaimSelection(BaseModel):
    selection_status: Literal["selected", "rejected"]
    expected_question_revision: int = Field(ge=1)


class ClaimRevision(HumanClaimRevision):
    expected_question_revision: int = Field(ge=1)


class ClaimGroundingReview(BaseModel):
    human_grounding_status: Literal["supported", "inferred", "unsupported", "insufficient_evidence"]
    expected_question_revision: int = Field(ge=1)


class ClaimRead(BaseModel):
    id: str
    project_id: str
    text: str
    claim_type: str
    legacy_grounding_status: str | None
    ai_grounding_assessment: str | None
    ai_risk_flags: list[str] | None
    ai_justification: str | None
    ai_evidence_refs: list[str] | None
    ai_support_excerpts: list[dict[str, str]] | None
    human_grounding_status: str
    human_reviewed_at: datetime | None
    confidence: str
    uncertainty: str
    evidence_for_ids: list[str]
    evidence_against_ids: list[str]
    selection_status: str
    question_revision: int
    supersedes_id: str | None
    generation_run_id: str | None
    origin: str
    eligible_for_generation: bool
    created_at: datetime


class ClaimGenerateResponse(BaseModel):
    generation_run_id: str
    claims: list[ClaimRead]


class LandscapeGenerate(BaseModel):
    claim_ids: list[str]
    expected_question_revision: int = Field(ge=1)


class LandscapeItemInput(BaseModel):
    category: Literal["approach", "method", "dataset", "metric", "finding", "conflict"]
    text: str
    uncertainty: str
    claim_ids: list[str]
    evidence_ids: list[str]


class LandscapeRevision(LandscapeOutput):
    expected_question_revision: int = Field(ge=1)


class LandscapeReview(BaseModel):
    review_status: Literal["accepted", "rejected"]
    expected_question_revision: int = Field(ge=1)


class LandscapeItemRead(LandscapeItemInput):
    id: str
    ai_conflict_assessment: dict[str, object] | None


class LandscapeRead(BaseModel):
    id: str
    project_id: str
    summary: str
    uncertainty: str
    review_status: str
    reviewed_at: datetime | None
    ai_summary_assessments: list[dict[str, object]] | None
    items: list[LandscapeItemRead]
    question_revision: int
    supersedes_id: str | None
    generation_run_id: str | None
    origin: str
    eligible_for_downstream: bool
    created_at: datetime


class LandscapeGenerateResponse(BaseModel):
    generation_run_id: str
    landscape: LandscapeRead


class GenerationRunRead(BaseModel):
    id: str
    project_id: str
    stage: str
    status: str
    provider: str
    model: str
    prompt_name: str
    prompt_version: str
    prompt_hash: str
    schema_version: str
    research_question_snapshot: str
    question_revision: int
    input_manifest: list[dict[str, object]]
    input_digest: str
    attempt_count: int
    error_type: str | None
    created_at: datetime
    completed_at: datetime | None
