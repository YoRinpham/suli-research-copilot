"""Thin M3 API routes."""

from typing import Literal

from fastapi import APIRouter, Query, Request

from app.api.schemas import (
    ClaimGenerate, ClaimGenerateResponse, ClaimGroundingReview, ClaimRead, ClaimRevision, ClaimSelection,
    GenerationRunRead, LandscapeGenerate, LandscapeGenerateResponse, LandscapeRead, LandscapeRevision, LandscapeReview,
)
from app.providers.llm import DashScopeLLMProvider
from app.services.scientific import ScientificService
from app.services.scientific_schemas import HumanClaimRevision, LandscapeOutput


router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["scientific"])


def service(request: Request) -> ScientificService:
    settings = request.app.state.settings
    provider = getattr(request.app.state, "llm_provider", None)
    if provider is None:
        provider = DashScopeLLMProvider(
            settings.qwen_api_key.get_secret_value() if settings.qwen_api_key else "",
            settings.qwen_model, settings.provider_timeout_seconds, settings.retry_count,
            base_url=settings.qwen_base_url, reasoning_effort=settings.qwen_reasoning_effort,
        )
        request.app.state.llm_provider = provider
    return ScientificService(settings, provider)


@router.post("/claims/generate", response_model=ClaimGenerateResponse, status_code=201)
def generate_claims(project_id: str, body: ClaimGenerate, request: Request):
    return service(request).generate_claims(project_id, body.evidence_ids, body.expected_question_revision)


@router.get("/claims", response_model=list[ClaimRead])
def list_claims(project_id: str, request: Request, selection_status: Literal["candidate", "selected", "rejected"] | None = Query(default=None)):
    return service(request).list_claims(project_id, selection_status)


@router.get("/claims/{claim_id}", response_model=ClaimRead)
def get_claim(project_id: str, claim_id: str, request: Request):
    return service(request).get_claim(project_id, claim_id)


@router.patch("/claims/{claim_id}/selection", response_model=ClaimRead)
def select_claim(project_id: str, claim_id: str, body: ClaimSelection, request: Request):
    return service(request).select_claim(project_id, claim_id, body.selection_status, body.expected_question_revision)


@router.patch("/claims/{claim_id}/grounding-review", response_model=ClaimRead)
def review_claim_grounding(project_id: str, claim_id: str, body: ClaimGroundingReview, request: Request):
    return service(request).review_claim_grounding(project_id, claim_id, body.human_grounding_status,
                                                    body.expected_question_revision)


@router.post("/claims/{claim_id}/revisions", response_model=ClaimRead, status_code=201)
def revise_claim(project_id: str, claim_id: str, body: ClaimRevision, request: Request):
    proposal = HumanClaimRevision.model_validate(body.model_dump(exclude={"expected_question_revision"}))
    return service(request).revise_claim(project_id, claim_id, body.expected_question_revision, proposal)


@router.post("/landscapes/generate", response_model=LandscapeGenerateResponse, status_code=201)
def generate_landscape(project_id: str, body: LandscapeGenerate, request: Request):
    return service(request).generate_landscape(project_id, body.claim_ids, body.expected_question_revision)


@router.get("/landscapes", response_model=list[LandscapeRead])
def list_landscapes(project_id: str, request: Request):
    return service(request).list_landscapes(project_id)


@router.get("/landscapes/{landscape_id}", response_model=LandscapeRead)
def get_landscape(project_id: str, landscape_id: str, request: Request):
    return service(request).get_landscape(project_id, landscape_id)


@router.patch("/landscapes/{landscape_id}/review", response_model=LandscapeRead)
def review_landscape(project_id: str, landscape_id: str, body: LandscapeReview, request: Request):
    return service(request).review_landscape(project_id, landscape_id, body.review_status,
                                              body.expected_question_revision)


@router.post("/landscapes/{landscape_id}/revisions", response_model=LandscapeRead, status_code=201)
def revise_landscape(project_id: str, landscape_id: str, body: LandscapeRevision, request: Request):
    output = LandscapeOutput.model_validate(body.model_dump(exclude={"expected_question_revision"}))
    return service(request).revise_landscape(project_id, landscape_id, body.expected_question_revision, output)


@router.get("/generation-runs/{run_id}", response_model=GenerationRunRead)
def get_run(project_id: str, run_id: str, request: Request):
    return service(request).get_run(project_id, run_id)
