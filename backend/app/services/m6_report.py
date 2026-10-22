"""Immutable report delivery over current accepted scientific and numerical sources."""

from typing import Any
from copy import deepcopy
from dataclasses import dataclass
from time import monotonic

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.persistence.models import (AnalysisRun, AnalysisSpec, CleaningRun, Dataset, DatasetVersion,
    Evidence, FigureArtifact, GenerationRun, Paper, Project, ResearchReport,
    ResultInterpretation, StatisticalResult, VisualizationSpec, now_utc)
from app.prompts.m6 import REPORT_PROMPT
from app.prompts.scientific import canonical_digest
from app.services.errors import ServiceError
from app.services.m5_ai import M5AIService
from app.services.m6_render import render_html, render_markdown
from app.services.m6_schemas import ReportNarrative, validate_narrative
from app.services.scientific import STRUCTURED_OUTPUT_MAX_CALLS


REPORT_APPLICATION_MARGIN_SECONDS = 30
REPORT_STALE_GUARD_MARGIN_SECONDS = 60


@dataclass(frozen=True)
class ReportTimeoutBudget:
    max_provider_attempts: int
    max_provider_wait_seconds: int
    max_valid_request_seconds: int
    active_stale_after_seconds: int


def report_timeout_budget_seconds(settings: Settings) -> ReportTimeoutBudget:
    """Report uses two structured calls, each with the configured provider retry budget."""
    attempts = STRUCTURED_OUTPUT_MAX_CALLS * (settings.retry_count + 1)
    provider_wait = attempts * settings.provider_timeout_seconds
    valid_request = provider_wait + REPORT_APPLICATION_MARGIN_SECONDS
    return ReportTimeoutBudget(attempts, provider_wait, valid_request,
                               valid_request + REPORT_STALE_GUARD_MARGIN_SECONDS)


def canonicalize_source_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    """Sort unordered source collections without changing the accepted plan's display order."""
    canonical = deepcopy(bundle)
    for key in ("claims", "evidence", "results", "figures", "analyses"):
        if key in canonical:
            canonical[key].sort(key=lambda row: row["id"])
    if "datasets" in canonical:
        canonical["datasets"].sort(key=lambda row: row["version_id"])
    if "citations" in canonical:
        canonical["citations"].sort(key=lambda row: row["evidence_id"])
    landscape = canonical.get("landscape") or {}
    if "items" in landscape:
        landscape["items"].sort(key=lambda row: row["id"])
        for item in landscape["items"]:
            for key in ("claim_ids", "evidence_ids"):
                if key in item:
                    item[key].sort()
    for key in ("gap", "hypothesis"):
        item = canonical.get(key) or {}
        for refs in ("landscape_item_ids", "claim_ids", "evidence_for_ids", "evidence_against_ids",
                     "evidence_motivating_ids", "evidence_challenging_ids"):
            if refs in item:
                item[refs].sort()
    for claim in canonical.get("claims", []):
        for key in ("evidence_for_ids", "evidence_against_ids"):
            if key in claim:
                claim[key].sort()
    return canonical


class M6ReportService(M5AIService):
    def _report_input(self, session: Session, project_id: str, revision: int,
                      interpretation_id: str) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
        project = self._project(session, project_id, revision)
        interpretation = session.scalar(select(ResultInterpretation).where(
            ResultInterpretation.id == interpretation_id, ResultInterpretation.project_id == project_id))
        if interpretation is None:
            raise ServiceError(404, "not_found", "Interpretation not found", "research_report")
        if not self._interpretation_read(session, interpretation)["eligible_for_future_report"]:
            raise ServiceError(409, "invalid_state", "Interpretation is not accepted and current", "research_report")
        run = session.get(AnalysisRun, interpretation.analysis_run_id)
        spec = session.get(AnalysisSpec, run.analysis_spec_id)
        plan = self._current_plan(session, project_id, spec.experiment_plan_id, revision)
        lineage = self._hypothesis_context(session, project_id, revision, plan.hypothesis_id)
        if run.hypothesis_id != plan.hypothesis_id:
            raise ServiceError(409, "invalid_provenance", "Analysis hypothesis differs from plan", "research_report")
        source = lineage["context"]
        result_rows = list(session.scalars(select(StatisticalResult).where(
            StatisticalResult.id.in_(interpretation.statistical_result_ids),
            StatisticalResult.project_id == project_id).order_by(StatisticalResult.id)))
        figure_rows = list(session.scalars(select(FigureArtifact).where(
            FigureArtifact.id.in_(interpretation.figure_artifact_ids),
            FigureArtifact.project_id == project_id).order_by(FigureArtifact.id)))
        if len(result_rows) != len(interpretation.statistical_result_ids) or len(figure_rows) != len(interpretation.figure_artifact_ids):
            raise ServiceError(409, "invalid_provenance", "Interpretation references foreign or missing sources", "research_report")
        analyses = []
        datasets = []
        for result in result_rows:
            analysis_run = session.get(AnalysisRun, result.analysis_run_id)
            analysis_spec = session.get(AnalysisSpec, analysis_run.analysis_spec_id)
            version = session.get(DatasetVersion, analysis_run.dataset_version_id)
            dataset = session.get(Dataset, version.dataset_id)
            if (analysis_run.project_id != project_id or analysis_run.status != "succeeded" or
                analysis_spec.review_status != "accepted" or dataset.project_id != project_id or
                dataset.experiment_plan_id != plan.id or analysis_run.hypothesis_id != plan.hypothesis_id):
                raise ServiceError(409, "invalid_provenance", "StatisticalResult lineage is invalid", "research_report")
            cleaning = session.get(CleaningRun, version.cleaning_run_id) if version.cleaning_run_id else None
            if version.kind == "cleaned" and (cleaning is None or cleaning.project_id != project_id or
                cleaning.status != "succeeded" or cleaning.source_version_id != version.parent_version_id):
                raise ServiceError(409, "invalid_provenance", "Cleaning lineage is invalid", "research_report")
            analyses.append({"id": analysis_run.id, "spec_id": analysis_spec.id,
                             "method": analysis_run.method, "column_mapping": analysis_run.column_mapping,
                             "parameters": analysis_run.parameters, "engine_version": analysis_run.engine_version})
            datasets.append({"id": dataset.id, "original_filename": dataset.original_filename,
                             "sha256": dataset.sha256, "version_id": version.id,
                             "version_sha256": version.sha256, "kind": version.kind,
                             "cleaning_run_id": version.cleaning_run_id,
                             "cleaning_summary": {"rows_before": cleaning.rows_before,
                                 "rows_after": cleaning.rows_after, "effects": cleaning.effects,
                                 "engine_version": cleaning.engine_version} if cleaning else None,
                             "row_count": version.row_count, "column_count": version.column_count})
        figures = []
        for figure in figure_rows:
            visualization = session.get(VisualizationSpec, figure.visualization_spec_id)
            if (visualization is None or visualization.project_id != project_id or
                visualization.status != "succeeded" or visualization.dataset_version_id != figure.dataset_version_id or
                visualization.analysis_run_id != figure.analysis_run_id or
                (visualization.statistical_result_id is not None and
                 visualization.statistical_result_id not in interpretation.statistical_result_ids)):
                raise ServiceError(409, "invalid_provenance", "Figure lineage is invalid", "research_report")
            try:
                self.storage.read(figure.storage_key, figure.sha256)
            except (OSError, ValueError) as exc:
                raise ServiceError(409, "artifact_unavailable", "Figure artifact is missing or changed", "research_report") from exc
            figures.append({"id": figure.id, "visualization_spec_id": visualization.id,
                            "dataset_version_id": figure.dataset_version_id,
                            "analysis_run_id": figure.analysis_run_id, "sha256": figure.sha256,
                            "title": visualization.title, "chart_type": visualization.chart_type,
                            "format": figure.format, "renderer_version": figure.renderer_version})
        citations = []
        for index, item in enumerate(sorted(source["evidence"], key=lambda row: row["id"]), 1):
            evidence = session.get(Evidence, item["id"])
            paper = session.get(Paper, item["paper_id"])
            if evidence is None or paper is None or paper.project_id != project_id:
                raise ServiceError(409, "invalid_provenance", "Evidence lineage is invalid", "research_report")
            citations.append({"token": f"EVIDENCE_{index}", "evidence_id": evidence.id,
                              "claim_ids": sorted(c["id"] for c in source["claims"] if evidence.id in
                                  c["evidence_for_ids"] + c["evidence_against_ids"]),
                              "paper_id": paper.id, "paper_title": paper.title,
                              "source_filename": paper.source_filename,
                              "paper_chunk_id": evidence.paper_chunk_id,
                              "page_number": item["page_number"], "source_text": evidence.source_text})
        source_map = {f"CLAIM_{index}": {"type": "claim", "id": item["id"]}
                      for index, item in enumerate(sorted(source["claims"], key=lambda row: row["id"]), 1)}
        source_map.update({row["token"]: {"type": "evidence", "id": row["evidence_id"]} for row in citations})
        source_map.update({f"RESULT_{index}": {"type": "statistical_result", "id": row.id}
                           for index, row in enumerate(result_rows, 1)})
        source_map.update({f"FIGURE_{index}": {"type": "figure_artifact", "id": row["id"]}
                           for index, row in enumerate(figures, 1)})
        source_map["INTERPRETATION_1"] = {"type": "result_interpretation", "id": interpretation.id}
        plan_fields = ("id", "objective", "independent_variable", "dependent_variable",
                       "control_variables", "experimental_groups", "baseline", "dataset_requirements",
                       "metrics", "procedure", "analysis_approach", "expected_observations",
                       "falsification_criteria", "confounders", "limitations", "assumptions", "uncertainty")
        bundle = {"project_id": project_id, "question_revision": revision,
                  "research_question": project.research_question, "landscape": source["landscape"],
                  "claims": source["claims"], "evidence": source["evidence"],
                  "gap": source["gap"], "hypothesis": source["hypothesis"],
                  "plan": {key: getattr(plan, key) for key in plan_fields},
                  "datasets": sorted({row["version_id"]: row for row in datasets}.values(), key=lambda row: row["version_id"]),
                  "analyses": sorted({row["id"]: row for row in analyses}.values(), key=lambda row: row["id"]),
                  "results": [{"id": row.id, "analysis_run_id": row.analysis_run_id,
                               "method": row.method, "values": row.values} for row in result_rows],
                  "figures": figures,
                  "interpretation": {"id": interpretation.id, "summary": interpretation.summary,
                      "hypothesis_relationship": interpretation.hypothesis_relationship,
                      "uncertainty": interpretation.uncertainty, "limitations": interpretation.limitations,
                      "alternative_explanations": interpretation.alternative_explanations,
                      "reviewed_at": interpretation.reviewed_at.isoformat() if interpretation.reviewed_at else None},
                  "citations": citations, "source_map": source_map}
        bundle = canonicalize_source_bundle(bundle)
        digest = canonical_digest(bundle)
        context = {"research_question": project.research_question,
                   "source_bundle": bundle, "source_tokens": source_map}
        self._check_size(context, self.settings)
        return context, [{"type": "report_source_bundle", "id": interpretation.id, "hash": digest}], {
            "bundle": bundle, "hash": digest, "source_tokens": set(source_map)}

    def preview_report(self, project_id: str, interpretation_id: str, revision: int) -> dict[str, Any]:
        with self.session_factory() as session:
            _, _, extra = self._report_input(session, project_id, revision, interpretation_id)
            title = "Research Report — " + self._project(session, project_id).title[:237]
            preview = {"title": title, "review_status": "pending_review",
                "source_bundle": extra["bundle"], "source_bundle_hash": extra["hash"],
                "narrative_blocks": [], "current_source_bundle": True}
            return {"source_bundle_hash": extra["hash"], "markdown": render_markdown(preview)}

    def generate_report(self, project_id: str, interpretation_id: str, revision: int) -> dict[str, Any]:
        deadline = monotonic() + report_timeout_budget_seconds(self.settings).max_valid_request_seconds

        def guard_start(session: Session, context: dict[str, Any], manifest: list[dict[str, Any]],
                        extra: dict[str, Any]) -> None:
            existing = session.scalar(select(ResearchReport.id).where(
                ResearchReport.project_id == project_id,
                ResearchReport.source_bundle_hash == extra["hash"],
                ResearchReport.review_status.in_(("pending_review", "accepted")),
                ~ResearchReport.id.in_(select(ResearchReport.supersedes_id).where(
                    ResearchReport.supersedes_id.is_not(None)))))
            if existing:
                raise ServiceError(409, "report_already_exists", "Current source already has a reviewable report", "research_report")
            digest = canonical_digest({"question": context["research_question"], "inputs": manifest})
            active = session.scalar(select(GenerationRun).where(
                GenerationRun.project_id == project_id, GenerationRun.stage == "research_report",
                GenerationRun.input_digest == digest, GenerationRun.status == "started").with_for_update())
            if active:
                max_duration = report_timeout_budget_seconds(self.settings).active_stale_after_seconds
                if (now_utc() - active.created_at).total_seconds() <= max_duration:
                    raise ServiceError(409, "generation_in_progress", "Report generation is already running", "research_report")
                active.status = "failed"
                active.error_type = "stale_generation"
                active.completed_at = now_utc()
                session.flush()

        def validate(output: ReportNarrative, extra: dict[str, Any]) -> None:
            validate_narrative(output, extra["source_tokens"], extra["bundle"]["interpretation"])

        def persist(session: Session, output: ReportNarrative, extra: dict[str, Any],
                    generation_run_id: str, assessment: Any) -> dict[str, Any]:
            prefix = "Research Report — "
            report = ResearchReport(project_id=project_id, question_revision=revision,
                title=prefix + self._project(session, project_id).title[:255 - len(prefix)],
                origin="model", generation_run_id=generation_run_id, review_status="pending_review",
                source_bundle=extra["bundle"], source_bundle_hash=extra["hash"],
                narrative_blocks=output.model_dump()["blocks"])
            session.add(report)
            session.flush()
            return {"report": self._report_read(session, report)}

        try:
            return self._run_ai(project_id, revision, interpretation_id, "research_report", REPORT_PROMPT,
                ReportNarrative, self._report_input, validate, persist,
                deadline_monotonic=deadline, start_guard=guard_start)
        except IntegrityError as exc:
            raise ServiceError(409, "generation_in_progress", "Report generation is already running", "research_report") from exc

    def _report_read(self, session: Session, report: ResearchReport) -> dict[str, Any]:
        superseded = session.scalar(select(ResearchReport.id).where(ResearchReport.supersedes_id == report.id)) is not None
        current = False
        if not superseded:
            try:
                _, _, extra = self._report_input(session, report.project_id, report.question_revision,
                                                  report.source_bundle["interpretation"]["id"])
                current = extra["hash"] == report.source_bundle_hash
            except ServiceError:
                current = False
        fields = ("id", "project_id", "question_revision", "title", "origin", "generation_run_id",
                  "review_status", "source_bundle", "source_bundle_hash", "narrative_blocks",
                  "supersedes_id", "created_at", "reviewed_at")
        return {**{key: getattr(report, key) for key in fields}, "current_source_bundle": current,
                "final_export_eligible": current and report.review_status == "accepted"}

    def list_reports(self, project_id: str) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            self._project(session, project_id)
            return [self._report_read(session, row) for row in session.scalars(select(ResearchReport).where(
                ResearchReport.project_id == project_id).order_by(ResearchReport.created_at, ResearchReport.id))]

    def get_report(self, project_id: str, report_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            self._project(session, project_id)
            report = session.scalar(select(ResearchReport).where(
                ResearchReport.id == report_id, ResearchReport.project_id == project_id))
            if report is None:
                raise ServiceError(404, "not_found", "ResearchReport not found", "research_report")
            return self._report_read(session, report)

    def review_report(self, project_id: str, report_id: str, revision: int, status: str) -> dict[str, Any]:
        if status not in {"accepted", "rejected"}:
            raise ServiceError(422, "invalid_review", "Invalid report decision", "research_report")
        with self.session_factory() as session:
            self._project(session, project_id, revision, lock=True)
            report = session.scalar(select(ResearchReport).where(ResearchReport.id == report_id,
                ResearchReport.project_id == project_id).with_for_update())
            if report is None:
                raise ServiceError(404, "not_found", "ResearchReport not found", "research_report")
            if report.review_status != "pending_review" or not self._report_read(session, report)["current_source_bundle"]:
                raise ServiceError(409, "invalid_state", "Report is reviewed or source is stale", "research_report")
            report.review_status = status
            report.reviewed_at = now_utc()
            session.commit()
            return self._report_read(session, report)

    def revise_report(self, project_id: str, report_id: str, revision: int,
                      title: str, narrative: ReportNarrative) -> dict[str, Any]:
        if not title.strip():
            raise ServiceError(422, "invalid_report", "Report title is empty", "research_report")
        with self.session_factory() as session:
            self._project(session, project_id, revision, lock=True)
            previous = session.scalar(select(ResearchReport).where(ResearchReport.id == report_id,
                ResearchReport.project_id == project_id).with_for_update())
            if previous is None:
                raise ServiceError(404, "not_found", "ResearchReport not found", "research_report")
            if not self._report_read(session, previous)["current_source_bundle"]:
                raise ServiceError(409, "invalid_state", "Report source is stale or superseded", "research_report")
            try:
                validate_narrative(narrative, set(previous.source_bundle["source_map"]),
                                   previous.source_bundle["interpretation"])
            except ValueError as exc:
                raise ServiceError(422, "invalid_report", str(exc), "research_report") from exc
            revised = ResearchReport(project_id=project_id, question_revision=revision,
                title=title.strip(), origin="human_revision", generation_run_id=None,
                review_status="pending_review", source_bundle=previous.source_bundle,
                source_bundle_hash=previous.source_bundle_hash,
                narrative_blocks=narrative.model_dump()["blocks"], supersedes_id=previous.id)
            session.add(revised)
            session.flush()
            session.commit()
            return self._report_read(session, revised)

    def export_report(self, project_id: str, report_id: str, fmt: str, draft: bool = False,
                      *, resource_base_url: str = "") -> str:
        report = self.get_report(project_id, report_id)
        if not draft and not report["final_export_eligible"]:
            raise ServiceError(409, "invalid_state", "Only a current human-accepted report can be final exported", "report_export")
        if fmt == "markdown":
            return render_markdown(report, resource_base_url=resource_base_url)
        if fmt == "html":
            return render_html(report, resource_base_url=resource_base_url)
        raise ServiceError(422, "invalid_format", "Unsupported report format", "report_export")
