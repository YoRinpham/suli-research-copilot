"""Project-scoped report delivery and safe demo diagnostics."""

from typing import Literal
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.scientific import service as scientific_service
from app.services.m6_report import M6ReportService
from app.services.m6_schemas import ReportNarrative
from app.services.m6_benchmark import report_from_files
from app.services.errors import ServiceError


router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["delivery-m6"])
benchmark_router = APIRouter(prefix="/api/v1", tags=["benchmark-m6"])
MEASUREMENTS = Path(__file__).resolve().parents[2] / "evaluation"


def service(request: Request) -> M6ReportService:
    parent = scientific_service(request)
    return M6ReportService(parent.settings, parent.provider)


class GenerateBody(BaseModel):
    interpretation_id: str
    expected_question_revision: int = Field(ge=1)


class ReviewBody(BaseModel):
    review_status: Literal["accepted", "rejected"]
    expected_question_revision: int = Field(ge=1)


class RevisionBody(ReportNarrative):
    title: str = Field(min_length=1, max_length=255)
    expected_question_revision: int = Field(ge=1)


@router.post("/reports/generate", status_code=201)
def generate_report(project_id: str, body: GenerateBody, request: Request):
    return service(request).generate_report(project_id, body.interpretation_id, body.expected_question_revision)


@router.get("/reports/preview")
def preview_report(project_id: str, request: Request, interpretation_id: str,
                   expected_question_revision: int = Query(ge=1)):
    return service(request).preview_report(project_id, interpretation_id, expected_question_revision)


@router.get("/reports")
def list_reports(project_id: str, request: Request):
    return service(request).list_reports(project_id)


@router.get("/reports/{report_id}")
def get_report(project_id: str, report_id: str, request: Request):
    return service(request).get_report(project_id, report_id)


@router.patch("/reports/{report_id}/review")
def review_report(project_id: str, report_id: str, body: ReviewBody, request: Request):
    return service(request).review_report(project_id, report_id, body.expected_question_revision, body.review_status)


@router.post("/reports/{report_id}/revisions", status_code=201)
def revise_report(project_id: str, report_id: str, body: RevisionBody, request: Request):
    return service(request).revise_report(project_id, report_id, body.expected_question_revision,
        body.title, ReportNarrative(blocks=body.blocks))


@router.get("/reports/{report_id}/export")
def export_report(project_id: str, report_id: str, request: Request,
                  format: Literal["markdown", "html"] = Query("markdown"), draft: bool = Query(False)):
    output = service(request).export_report(project_id, report_id, format, draft,
        resource_base_url=str(request.base_url))
    suffix, media_type = ("md", "text/markdown") if format == "markdown" else ("html", "text/html")
    return Response(output, media_type=media_type,
        headers={"Content-Disposition": f"{'attachment' if format == 'markdown' else 'inline'}; filename=research-report.{suffix}"})


@benchmark_router.get("/benchmark")
def benchmark_summary():
    manual = MEASUREMENTS / "manual-session.json"
    copilot = MEASUREMENTS / "copilot-session.json"
    try:
        return report_from_files(manual if manual.is_file() else None,
            copilot if copilot.is_file() else None)
    except (ValueError, OSError) as exc:
        raise ServiceError(422, "benchmark_invalid", "Recorded benchmark sessions are invalid or incompatible", "benchmark") from exc
