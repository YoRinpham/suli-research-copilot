"""Thin M5 project-scoped data, analysis and interpretation routes."""

from typing import Any, Literal

from fastapi import APIRouter, File, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.scientific import service as scientific_service
from app.services.m5_ai import M5AIService
from app.services.m5_ai_schemas import InterpretationProposal


router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["scientific-m5"])


def service(request: Request) -> M5AIService:
    parent = scientific_service(request)
    return M5AIService(parent.settings, parent.provider)


class Revision(BaseModel):
    expected_question_revision: int = Field(ge=1)


class CleaningBody(Revision):
    operations: list[dict[str, Any]] = Field(max_length=20)
    rationale: str = Field(min_length=1, max_length=2000)


class AnalysisBody(Revision):
    method: str
    column_mapping: dict[str, str]
    parameters: dict[str, Any] = Field(default_factory=dict)
    rationale: str = Field(min_length=1, max_length=2000)


class ReviewBody(Revision):
    review_status: Literal["accepted", "rejected"]


class FigureBody(Revision):
    chart_type: Literal["histogram", "box_plot", "scatter", "group_summary"]
    x_column: str | None = None
    y_column: str | None = None
    group_column: str | None = None
    title: str = Field(min_length=1, max_length=255)
    options: dict[str, Any] = Field(default_factory=dict)
    analysis_run_id: str | None = None
    statistical_result_id: str | None = None


class InterpretationRevision(InterpretationProposal, Revision):
    pass


@router.post("/experiment-plans/{plan_id}/datasets", status_code=201)
def upload_dataset(project_id: str, plan_id: str, request: Request,
                   expected_question_revision: int = Query(ge=1), file: UploadFile = File(...)):
    manager = service(request)
    limit = manager.settings.csv_size_limit_mb * 1024 * 1024
    payload = file.file.read(limit + 1)
    return manager.upload_csv(project_id, plan_id, expected_question_revision, file.filename or "", payload)


@router.get("/datasets")
def list_datasets(project_id: str, request: Request):
    return service(request).list_datasets(project_id)


@router.get("/datasets/{dataset_id}")
def get_dataset(project_id: str, dataset_id: str, request: Request):
    return service(request).get_dataset(project_id, dataset_id)


@router.get("/dataset-versions/{version_id}")
def get_dataset_version(project_id: str, version_id: str, request: Request):
    return service(request).get_version(project_id, version_id)


@router.get("/dataset-versions/{version_id}/file")
def download_dataset_version(project_id: str, version_id: str, request: Request):
    return Response(service(request).dataset_file(project_id, version_id), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=dataset.csv"})


@router.post("/dataset-versions/{version_id}/cleaning-plans", status_code=201)
def create_cleaning(project_id: str, version_id: str, body: CleaningBody, request: Request):
    return service(request).propose_cleaning(project_id, version_id, body.expected_question_revision,
                                             body.operations, body.rationale)


@router.post("/dataset-versions/{version_id}/cleaning-plans/suggest", status_code=201)
def suggest_cleaning(project_id: str, version_id: str, body: Revision, request: Request):
    return service(request).suggest_cleaning(project_id, version_id, body.expected_question_revision)


@router.get("/cleaning-plans")
def list_cleaning(project_id: str, request: Request, version_id: str | None = None):
    return service(request).list_cleaning(project_id, version_id)


@router.patch("/cleaning-plans/{plan_id}/review")
def review_cleaning(project_id: str, plan_id: str, body: ReviewBody, request: Request):
    return service(request).review_cleaning(project_id, plan_id, body.expected_question_revision, body.review_status)


@router.post("/cleaning-plans/{plan_id}/execute", status_code=201)
def execute_cleaning(project_id: str, plan_id: str, body: Revision, request: Request):
    return service(request).execute_cleaning(project_id, plan_id, body.expected_question_revision)


@router.get("/cleaning-runs")
def list_cleaning_runs(project_id: str, request: Request):
    return service(request).list_cleaning_runs(project_id)


@router.post("/dataset-versions/{version_id}/analysis-specs", status_code=201)
def create_analysis_spec(project_id: str, version_id: str, body: AnalysisBody, request: Request):
    return service(request).propose_analysis(project_id, version_id, body.expected_question_revision,
        body.method, body.column_mapping, body.parameters, body.rationale)


@router.post("/dataset-versions/{version_id}/analysis-specs/suggest", status_code=201)
def suggest_analysis_spec(project_id: str, version_id: str, body: Revision, request: Request):
    return service(request).suggest_analysis(project_id, version_id, body.expected_question_revision)


@router.get("/analysis-specs")
def list_analysis_specs(project_id: str, request: Request, version_id: str | None = None):
    return service(request).list_specs(project_id, version_id)


@router.patch("/analysis-specs/{spec_id}/review")
def review_analysis_spec(project_id: str, spec_id: str, body: ReviewBody, request: Request):
    return service(request).review_spec(project_id, spec_id, body.expected_question_revision, body.review_status)


@router.post("/analysis-specs/{spec_id}/execute", status_code=201)
def execute_analysis(project_id: str, spec_id: str, body: Revision, request: Request):
    return service(request).execute_analysis(project_id, spec_id, body.expected_question_revision)


@router.get("/analysis-runs")
def list_analysis_runs(project_id: str, request: Request, version_id: str | None = None):
    return service(request).list_runs(project_id, version_id)


@router.get("/analysis-runs/{run_id}")
def get_analysis_run(project_id: str, run_id: str, request: Request):
    return service(request).get_run(project_id, run_id)


@router.get("/statistical-results/{result_id}")
def get_statistical_result(project_id: str, result_id: str, request: Request):
    return service(request).get_statistical_result(project_id, result_id)


@router.post("/dataset-versions/{version_id}/figures", status_code=201)
def create_figure(project_id: str, version_id: str, body: FigureBody, request: Request):
    return service(request).create_figure(project_id, version_id, body.expected_question_revision,
        body.chart_type, body.x_column, body.y_column, body.group_column, body.title, body.options,
        body.analysis_run_id, body.statistical_result_id)


@router.get("/figures")
def list_figures(project_id: str, request: Request, version_id: str | None = None):
    return service(request).list_figures(project_id, version_id)


@router.get("/figures/{figure_id}")
def get_figure(project_id: str, figure_id: str, request: Request):
    return service(request).get_figure(project_id, figure_id)


@router.get("/figures/{figure_id}/file")
def figure_file(project_id: str, figure_id: str, request: Request):
    return Response(service(request).figure_file(project_id, figure_id), media_type="image/png")


@router.post("/figures/{figure_id}/delete")
def delete_figure(project_id: str, figure_id: str, body: Revision, request: Request):
    return service(request).delete_figure(project_id, figure_id, body.expected_question_revision)


@router.post("/analysis-runs/{run_id}/interpretations/generate", status_code=201)
def generate_interpretation(project_id: str, run_id: str, body: Revision, request: Request):
    return service(request).generate_interpretation(project_id, run_id, body.expected_question_revision)


@router.get("/result-interpretations")
def list_interpretations(project_id: str, request: Request):
    return service(request).list_interpretations(project_id)


@router.get("/result-interpretations/{interpretation_id}")
def get_interpretation(project_id: str, interpretation_id: str, request: Request):
    return service(request).get_interpretation(project_id, interpretation_id)


@router.patch("/result-interpretations/{interpretation_id}/review")
def review_interpretation(project_id: str, interpretation_id: str, body: ReviewBody, request: Request):
    return service(request).review_interpretation(project_id, interpretation_id,
        body.expected_question_revision, body.review_status)


@router.post("/result-interpretations/{interpretation_id}/revisions", status_code=201)
def revise_interpretation(project_id: str, interpretation_id: str,
                          body: InterpretationRevision, request: Request):
    proposal = InterpretationProposal.model_validate(body.model_dump(exclude={"expected_question_revision"}))
    return service(request).revise_interpretation(project_id, interpretation_id,
        body.expected_question_revision, proposal)
