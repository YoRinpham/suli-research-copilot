"""Explicit M4 generation and human review endpoints."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from app.api.scientific import service as m3_service
from app.services.m4 import M4Service
from app.services.m4_schemas import GapProposal, HypothesisProposal, PlanProposal


router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["scientific-m4"])


def service(request: Request) -> M4Service:
    parent = m3_service(request)
    return M4Service(parent.settings, parent.provider)


class RevisionBody(BaseModel):
    expected_question_revision: int = Field(ge=1)


class GapGenerate(RevisionBody):
    landscape_id: str


class Selection(RevisionBody):
    selection_status: Literal["selected", "rejected"]


class Review(RevisionBody):
    review_status: Literal["accepted", "rejected"]


class GapRevision(GapProposal, RevisionBody):
    pass


class HypothesisRevision(HypothesisProposal, RevisionBody):
    pass


class PlanRevision(PlanProposal, RevisionBody):
    pass


class GapRead(BaseModel):
    id: str
    project_id: str
    landscape_id: str
    title: str
    description: str
    gap_type: str
    why_it_matters: str
    uncertainty: str
    assumptions: list[str]
    novelty_scope: str
    landscape_item_ids: list[str]
    claim_ids: list[str]
    evidence_for_ids: list[str]
    evidence_against_ids: list[str]
    ai_assessment: dict[str, object] | None
    selection_status: str
    question_revision: int
    origin: str
    supersedes_id: str | None
    generation_run_id: str | None
    eligible_for_generation: bool
    created_at: datetime


class HypothesisRead(BaseModel):
    id: str
    project_id: str
    research_gap_id: str
    statement: str
    rationale: str
    independent_variable: str
    dependent_variable: str
    control_variables: list[str]
    falsification_condition: str
    assumptions: list[str]
    uncertainty: str
    ai_assessment: dict[str, object] | None
    evidence_motivating_ids: list[str]
    evidence_challenging_ids: list[str]
    claim_ids: list[str]
    selection_status: str
    question_revision: int
    origin: str
    supersedes_id: str | None
    generation_run_id: str | None
    eligible_for_generation: bool
    created_at: datetime


class PlanRead(BaseModel):
    id: str
    hypothesis_id: str
    objective: str
    independent_variable: str
    dependent_variable: str
    control_variables: list[str]
    experimental_groups: list[str]
    baseline: str
    dataset_requirements: str
    metrics: list[str]
    procedure: str
    analysis_approach: str
    expected_observations: list[str]
    falsification_criteria: str
    confounders: list[str]
    limitations: list[str]
    assumptions: list[str]
    uncertainty: str
    ai_alignment_assessment: dict[str, object] | None
    review_status: str
    reviewed_at: datetime | None
    question_revision: int
    origin: str
    supersedes_id: str | None
    generation_run_id: str | None
    eligible_for_downstream: bool
    created_at: datetime


class GapGenerateResponse(BaseModel):
    generation_run_id: str
    gaps: list[GapRead]


class HypothesisGenerateResponse(BaseModel):
    generation_run_id: str
    hypotheses: list[HypothesisRead]


class PlanGenerateResponse(BaseModel):
    generation_run_id: str
    plan: PlanRead


class HypothesisGenerate(RevisionBody):
    count: int = Field(default=3, ge=1, le=5, description=(
        "Requested target and upper bound for valid Hypothesis candidates, not a guaranteed count. "
        "The service may return fewer than requested but never zero or more than count."))


@router.post("/gaps/generate", response_model=GapGenerateResponse, status_code=201)
def generate_gaps(project_id: str, body: GapGenerate, request: Request):
    return service(request).generate_gaps(project_id, body.landscape_id, body.expected_question_revision)


@router.get("/gaps", response_model=list[GapRead])
def list_gaps(project_id: str, request: Request):
    return service(request).list_gaps(project_id)


@router.get("/gaps/{gap_id}", response_model=GapRead)
def get_gap(project_id: str, gap_id: str, request: Request):
    return service(request).get_gap(project_id, gap_id)


@router.patch("/gaps/{gap_id}/selection", response_model=GapRead)
def select_gap(project_id: str, gap_id: str, body: Selection, request: Request):
    return service(request).select_gap(project_id, gap_id, body.selection_status, body.expected_question_revision)


@router.post("/gaps/{gap_id}/revisions", response_model=GapRead, status_code=201)
def revise_gap(project_id: str, gap_id: str, body: GapRevision, request: Request):
    proposal = GapProposal.model_validate(body.model_dump(exclude={"expected_question_revision"}))
    return service(request).revise_gap(project_id, gap_id, body.expected_question_revision, proposal)


@router.post("/gaps/{gap_id}/hypotheses/generate", response_model=HypothesisGenerateResponse, status_code=201)
def generate_hypotheses(project_id: str, gap_id: str, body: HypothesisGenerate, request: Request):
    return service(request).generate_hypotheses(project_id, gap_id, body.expected_question_revision, body.count)


@router.get("/hypotheses", response_model=list[HypothesisRead])
def list_hypotheses(project_id: str, request: Request, gap_id: str | None = Query(default=None)):
    return service(request).list_hypotheses(project_id, gap_id)


@router.get("/hypotheses/{hypothesis_id}", response_model=HypothesisRead)
def get_hypothesis(project_id: str, hypothesis_id: str, request: Request):
    return service(request).get_hypothesis(project_id, hypothesis_id)


@router.patch("/hypotheses/{hypothesis_id}/selection", response_model=HypothesisRead)
def select_hypothesis(project_id: str, hypothesis_id: str, body: Selection, request: Request):
    return service(request).select_hypothesis(project_id, hypothesis_id, body.selection_status, body.expected_question_revision)


@router.post("/hypotheses/{hypothesis_id}/revisions", response_model=HypothesisRead, status_code=201)
def revise_hypothesis(project_id: str, hypothesis_id: str, body: HypothesisRevision, request: Request):
    proposal = HypothesisProposal.model_validate(body.model_dump(exclude={"expected_question_revision"}))
    return service(request).revise_hypothesis(project_id, hypothesis_id, body.expected_question_revision, proposal)


@router.post("/hypotheses/{hypothesis_id}/experiment-plans/generate", response_model=PlanGenerateResponse, status_code=201)
def generate_plan(project_id: str, hypothesis_id: str, body: RevisionBody, request: Request):
    return service(request).generate_plan(project_id, hypothesis_id, body.expected_question_revision)


@router.get("/experiment-plans", response_model=list[PlanRead])
def list_plans(project_id: str, request: Request, hypothesis_id: str | None = Query(default=None)):
    return service(request).list_plans(project_id, hypothesis_id)


@router.get("/experiment-plans/{plan_id}", response_model=PlanRead)
def get_plan(project_id: str, plan_id: str, request: Request):
    return service(request).get_plan(project_id, plan_id)


@router.patch("/experiment-plans/{plan_id}/review", response_model=PlanRead)
def review_plan(project_id: str, plan_id: str, body: Review, request: Request):
    return service(request).review_plan(project_id, plan_id, body.review_status, body.expected_question_revision)


@router.post("/experiment-plans/{plan_id}/revisions", response_model=PlanRead, status_code=201)
def revise_plan(project_id: str, plan_id: str, body: PlanRevision, request: Request):
    proposal = PlanProposal.model_validate(body.model_dump(exclude={"expected_question_revision"}))
    return service(request).revise_plan(project_id, plan_id, body.expected_question_revision, proposal)
