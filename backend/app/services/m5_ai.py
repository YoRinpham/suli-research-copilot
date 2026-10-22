"""Bounded Qwen suggestions and advisory interpretation, with post-call authority checks."""

from collections.abc import Callable
from time import monotonic
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.persistence.models import (
    AnalysisRun, AnalysisSpec, CleaningPlan, Dataset, DatasetVersion, ExperimentPlan,
    FigureArtifact, GenerationRun, Hypothesis, Project, ResultInterpretation,
    StatisticalResult, now_utc,
)
from app.prompts.m5 import ANALYSIS_PROMPT, ASSESSMENT, CLEANING_PROMPT, INTERPRETATION_PROMPT
from app.prompts.scientific import Prompt, canonical_digest
from app.services.errors import ServiceError
from app.services.m5 import M5Service
from app.services.m5_ai_schemas import (
    AnalysisProposal, CleaningProposal, InterpretationAssessment, InterpretationProposal,
    validate_interpretation_language,
)
from app.services.m5_data import CSVError, validate_operations
from app.services.m5_stats import AnalysisError, validate_spec_profile


class M5AIService(M5Service):
    def _version_input(self, session: Session, project_id: str, revision: int,
                       version_id: str) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
        project = self._project(session, project_id, revision)
        dataset, version = self._version(session, project_id, version_id)
        plan = self._current_plan(session, project_id, dataset.experiment_plan_id, revision)
        hypothesis = session.get(Hypothesis, plan.hypothesis_id)
        plan_fields = {key: getattr(plan, key) for key in ("id", "objective", "independent_variable",
            "dependent_variable", "experimental_groups", "baseline", "dataset_requirements", "metrics",
            "analysis_approach", "limitations", "assumptions", "falsification_criteria")}
        hypothesis_fields = {key: getattr(hypothesis, key) for key in ("id", "statement", "falsification_condition")}
        version_context = {key: getattr(version, key) for key in ("id", "kind", "sha256", "row_count", "column_count", "profile")}
        context = {"research_question": project.research_question, "experiment_plan": plan_fields,
                   "hypothesis": hypothesis_fields, "dataset_version": version_context}
        manifest = [{"type": "experiment_plan", "id": plan.id, "review_status": plan.review_status,
                     "content_hash": canonical_digest(plan_fields)},
                    {"type": "hypothesis", "id": hypothesis.id, "content_hash": canonical_digest(hypothesis_fields)},
                    {"type": "dataset_version", "id": version.id, "sha256": version.sha256,
                     "profile_hash": canonical_digest(version.profile)}]
        extra = {"dataset": dataset, "version": version, "plan": plan, "hypothesis": hypothesis}
        self._check_size(context, self.settings)
        return context, manifest, extra

    def _interpretation_input(self, session: Session, project_id: str, revision: int,
                              run_id: str) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
        run = session.scalar(select(AnalysisRun).where(AnalysisRun.id == run_id,
            AnalysisRun.project_id == project_id, AnalysisRun.status == "succeeded"))
        if run is None:
            raise ServiceError(404, "not_found", "Successful AnalysisRun not found", "result_interpretation")
        context, manifest, extra = self._version_input(session, project_id, revision, run.dataset_version_id)
        if run.hypothesis_id != extra["hypothesis"].id:
            raise ServiceError(409, "invalid_state", "AnalysisRun hypothesis lineage changed", "result_interpretation")
        # Include all analyses for this Dataset and Hypothesis. Never select by p-value.
        all_runs = list(session.scalars(select(AnalysisRun).join(DatasetVersion,
            AnalysisRun.dataset_version_id == DatasetVersion.id).where(
            AnalysisRun.project_id == project_id, AnalysisRun.hypothesis_id == run.hypothesis_id,
            DatasetVersion.dataset_id == extra["dataset"].id).order_by(AnalysisRun.started_at, AnalysisRun.id)))
        if len(all_runs) > self.settings.llm_max_input_items:
            raise ServiceError(422, "context_too_large", "Too many analyses for one bounded interpretation", "result_interpretation")
        results: list[dict[str, Any]] = []
        run_summaries: list[dict[str, Any]] = []
        for item in all_runs:
            run_summaries.append({"id": item.id, "status": item.status, "method": item.method,
                "dataset_version_id": item.dataset_version_id})
            if item.status == "succeeded":
                for result in session.scalars(select(StatisticalResult).where(StatisticalResult.analysis_run_id == item.id)):
                    results.append({"id": result.id, "analysis_run_id": item.id,
                                    "method": result.method, "values": result.values})
        if not results:
            raise ServiceError(409, "invalid_state", "No deterministic results exist", "result_interpretation")
        figure_ids = [item.id for item in session.scalars(select(FigureArtifact).where(
            FigureArtifact.project_id == project_id,
            FigureArtifact.analysis_run_id.in_([item.id for item in all_runs])).order_by(FigureArtifact.created_at, FigureArtifact.id))]
        if len(figure_ids) > self.settings.llm_max_input_items:
            raise ServiceError(422, "context_too_large", "Too many figures for one bounded interpretation", "result_interpretation")
        analysis_spec = session.get(AnalysisSpec, run.analysis_spec_id)
        spec_context = {key: getattr(analysis_spec, key) for key in (
            "id", "method", "column_mapping", "parameters", "rationale", "hypothesis_id", "dataset_version_id")}
        context.update({"analysis_runs": run_summaries, "statistical_results": results,
                        "figure_artifact_ids": figure_ids,
                        "selected_analysis_run_id": run_id,
                        "analysis_spec": spec_context})
        manifest.extend([{"type": "analysis_run", **item} for item in run_summaries])
        manifest.extend({"type": "statistical_result", "id": item["id"],
                         "hash": canonical_digest(item["values"])} for item in results)
        manifest.extend({"type": "figure_artifact", "id": item} for item in figure_ids)
        extra.update({"run": run, "result_ids": sorted(item["id"] for item in results),
                      "figure_ids": sorted(figure_ids)})
        self._check_size(context, self.settings)
        return context, manifest, extra

    def _run_ai(self, project_id: str, revision: int, source_id: str, stage: str,
                prompt: Prompt, schema: type[BaseModel], loader: Callable,
                validator: Callable[[BaseModel, dict[str, Any]], None],
                persist: Callable[[Session, BaseModel, dict[str, Any], str, Any], dict[str, Any]],
                *, assess: bool = False, deadline_monotonic: float | None = None,
                start_guard: Callable[[Session, dict[str, Any], list[dict[str, Any]], dict[str, Any]], None] | None = None) -> dict[str, Any]:
        def check_deadline() -> None:
            if deadline_monotonic is not None and monotonic() >= deadline_monotonic:
                raise ServiceError(504, "report_deadline_expired", "Report generation deadline expired", stage)

        check_deadline()
        with self.session_factory() as session:
            context, manifest, extra = loader(session, project_id, revision, source_id)
            question = context["research_question"]
        check_deadline()
        with self.session_factory() as session:
            self._project(session, project_id, revision, lock=True)
            current, current_manifest, current_extra = loader(session, project_id, revision, source_id)
            if current_manifest != manifest or current["research_question"] != question:
                raise ServiceError(409, "invalid_state", "Scientific input changed before generation", stage)
            check_deadline()
            if start_guard is not None:
                start_guard(session, current, current_manifest, current_extra)
            check_deadline()
            run = GenerationRun(project_id=project_id, stage=stage, status="started", provider="qwen",
                model=self.settings.qwen_model, prompt_name=prompt.name, prompt_version=prompt.version,
                prompt_hash=prompt.digest, schema_version=f"m5-{stage}-v1",
                research_question_snapshot=question, question_revision=revision,
                input_manifest=manifest, input_digest=canonical_digest({"question": question, "inputs": manifest}),
                attempt_count=0)
            session.add(run)
            session.commit()
            generation_run_id = run.id
        attempts = 0
        try:
            check_deadline()
            output, attempts = self._generate(generation_run_id, prompt, schema, context,
                lambda proposed: validator(proposed, extra), repair_instruction=prompt.bundle["repair"],
                trace_stage=f"{stage}_generation" if stage in {"result_interpretation", "research_report"} else None)
            check_deadline()
        except ServiceError as exc:
            if exc.code == "report_deadline_expired":
                self._failed(generation_run_id, exc.code, attempts)
                exc.details["generation_run_id"] = generation_run_id
            raise
        assessment = None
        if assess:
            assess_context = {"scientific_input": context, "candidate_interpretation": output.model_dump()}
            self._check_run_context(generation_run_id, assess_context, attempts)
            assessment, attempts = self._generate(generation_run_id,
                Prompt("result_interpretation_assessment", prompt.version, ASSESSMENT, {}),
                InterpretationAssessment, assess_context, lambda _: None,
                prior_attempts=attempts, repair_instruction=prompt.bundle["repair"],
                trace_stage="result_interpretation_assessment" if stage == "result_interpretation" else None)
            if assessment.verdict != "cautious":
                self._failed(generation_run_id, "unsafe_interpretation", attempts)
                raise ServiceError(422, "unsafe_interpretation", "Interpretation failed independent review",
                    stage, {"generation_run_id": generation_run_id, "risk_flags": assessment.risk_flags})
        try:
            with self.session_factory() as session:
                self._project(session, project_id, revision, lock=True)
                run = session.scalar(select(GenerationRun).where(
                    GenerationRun.id == generation_run_id, GenerationRun.project_id == project_id,
                    GenerationRun.stage == stage).with_for_update())
                if run is None or run.status != "started":
                    raise ServiceError(409, "generation_superseded", "Generation Run is no longer active", stage)
                check_deadline()
                current, current_manifest, current_extra = loader(session, project_id, revision, source_id)
                if current_manifest != manifest or current["research_question"] != question:
                    raise ServiceError(409, "invalid_state", "Scientific input changed during generation", stage)
                validator(output, current_extra)
                result = persist(session, output, current_extra, generation_run_id, assessment)
                check_deadline()
                transitioned = session.execute(update(GenerationRun).where(
                    GenerationRun.id == generation_run_id, GenerationRun.status == "started").values(
                    status="succeeded", attempt_count=attempts, completed_at=now_utc()))
                if transitioned.rowcount != 1:
                    raise ServiceError(409, "generation_superseded", "Generation Run is no longer active", stage)
                check_deadline()
                session.commit()
                return {"generation_run_id": generation_run_id, **result}
        except ServiceError as exc:
            self._failed(generation_run_id, exc.code, attempts)
            exc.details["generation_run_id"] = generation_run_id
            raise
        except SQLAlchemyError as exc:
            self._failed(generation_run_id, "database_failed", attempts)
            raise ServiceError(500, "database_failed", "M5 proposal could not be saved", stage,
                               {"generation_run_id": generation_run_id}) from exc

    def suggest_cleaning(self, project_id: str, version_id: str, revision: int) -> dict[str, Any]:
        def validate(proposed: CleaningProposal, extra: dict[str, Any]) -> None:
            columns = [item["name"] for item in extra["version"].profile["columns"]]
            validate_operations(columns, proposed.operations)

        def persist(session, proposed, extra, run_id, assessment):
            plan = CleaningPlan(project_id=project_id, experiment_plan_id=extra["plan"].id,
                source_version_id=version_id, operations=proposed.operations, rationale=proposed.rationale,
                origin="model", review_status="pending_review", generation_run_id=run_id)
            session.add(plan)
            session.flush()
            return {"cleaning_plan": self._cleaning_read(plan)}

        return self._run_ai(project_id, revision, version_id, "cleaning_plan", CLEANING_PROMPT,
            CleaningProposal, self._version_input, validate, persist)

    def suggest_analysis(self, project_id: str, version_id: str, revision: int) -> dict[str, Any]:
        def validate(proposed: AnalysisProposal, extra: dict[str, Any]) -> None:
            validate_spec_profile(extra["version"].profile, proposed.method,
                                  proposed.column_mapping, proposed.parameters)

        def persist(session, proposed, extra, run_id, assessment):
            spec = AnalysisSpec(project_id=project_id, experiment_plan_id=extra["plan"].id,
                hypothesis_id=extra["hypothesis"].id, dataset_version_id=version_id,
                method=proposed.method, column_mapping=proposed.column_mapping,
                parameters={"missing_policy": "reject", **proposed.parameters}, rationale=proposed.rationale,
                origin="model", review_status="pending_review", generation_run_id=run_id)
            session.add(spec)
            session.flush()
            return {"analysis_spec": self._spec_read(spec)}

        return self._run_ai(project_id, revision, version_id, "analysis_spec", ANALYSIS_PROMPT,
            AnalysisProposal, self._version_input, validate, persist)

    def generate_interpretation(self, project_id: str, run_id: str, revision: int) -> dict[str, Any]:
        def validate(proposed: InterpretationProposal, extra: dict[str, Any]) -> None:
            validate_interpretation_language(proposed)
            if sorted(proposed.statistical_result_ids) != extra["result_ids"] or sorted(proposed.figure_artifact_ids) != extra["figure_ids"]:
                raise ValueError("Interpretation must reference the complete result and figure set")

        def persist(session, proposed, extra, generation_run_id, assessment):
            interpretation = ResultInterpretation(project_id=project_id, analysis_run_id=run_id,
                summary=proposed.summary, hypothesis_relationship=proposed.hypothesis_relationship,
                uncertainty=proposed.uncertainty, limitations=proposed.limitations,
                alternative_explanations=proposed.alternative_explanations,
                statistical_result_ids=proposed.statistical_result_ids,
                figure_artifact_ids=proposed.figure_artifact_ids,
                ai_assessment=assessment.model_dump() if assessment else None,
                review_status="pending_review", question_revision=revision,
                origin="model", generation_run_id=generation_run_id)
            session.add(interpretation)
            session.flush()
            return {"interpretation": self._interpretation_read(session, interpretation)}

        return self._run_ai(project_id, revision, run_id, "result_interpretation", INTERPRETATION_PROMPT,
            InterpretationProposal, self._interpretation_input, validate, persist, assess=True)

    def _interpretation_read(self, session: Session, interpretation: ResultInterpretation) -> dict[str, Any]:
        project = self._project(session, interpretation.project_id)
        superseded = session.scalar(select(ResultInterpretation.id).where(
            ResultInterpretation.supersedes_id == interpretation.id)) is not None
        run = session.get(AnalysisRun, interpretation.analysis_run_id)
        eligible = (not superseded and interpretation.review_status == "accepted" and
            interpretation.question_revision == project.question_revision and run.status == "succeeded" and
            bool(interpretation.statistical_result_ids))
        if eligible:
            try:
                self._assert_interpretation_current(session, interpretation, project.question_revision)
            except ServiceError:
                eligible = False
        fields = ("id", "project_id", "analysis_run_id", "summary", "hypothesis_relationship",
            "uncertainty", "limitations", "alternative_explanations", "statistical_result_ids",
            "figure_artifact_ids", "ai_assessment", "review_status", "question_revision", "origin",
            "supersedes_id", "generation_run_id", "created_at", "reviewed_at")
        return {**{key: getattr(interpretation, key) for key in fields},
                "eligible_for_future_report": eligible}

    def _assert_interpretation_current(self, session: Session, interpretation: ResultInterpretation,
                                       revision: int) -> None:
        _, _, current = self._interpretation_input(session, interpretation.project_id,
                                                    revision, interpretation.analysis_run_id)
        if (sorted(interpretation.statistical_result_ids) != current["result_ids"] or
                sorted(interpretation.figure_artifact_ids) != current["figure_ids"]):
            raise ServiceError(409, "stale_interpretation",
                "New results or figures require a new interpretation", "result_interpretation")

    def list_interpretations(self, project_id: str) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            self._project(session, project_id)
            rows = session.scalars(select(ResultInterpretation).where(ResultInterpretation.project_id == project_id)
                                   .order_by(ResultInterpretation.created_at, ResultInterpretation.id))
            return [self._interpretation_read(session, item) for item in rows]

    def get_interpretation(self, project_id: str, interpretation_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            self._project(session, project_id)
            interpretation = session.scalar(select(ResultInterpretation).where(
                ResultInterpretation.id == interpretation_id, ResultInterpretation.project_id == project_id))
            if interpretation is None:
                raise ServiceError(404, "not_found", "ResultInterpretation not found", "result_interpretation")
            return self._interpretation_read(session, interpretation)

    def review_interpretation(self, project_id: str, interpretation_id: str,
                              revision: int, status: str) -> dict[str, Any]:
        if status not in {"accepted", "rejected"}:
            raise ServiceError(422, "validation_error", "Invalid review decision", "interpretation_review")
        with self.session_factory() as session:
            self._project(session, project_id, revision, lock=True)
            interpretation = session.scalar(select(ResultInterpretation).where(
                ResultInterpretation.id == interpretation_id,
                ResultInterpretation.project_id == project_id).with_for_update())
            if interpretation is None:
                raise ServiceError(404, "not_found", "ResultInterpretation not found", "interpretation_review")
            if interpretation.review_status != "pending_review" or session.scalar(select(ResultInterpretation.id).where(
                    ResultInterpretation.supersedes_id == interpretation.id)) is not None:
                raise ServiceError(409, "invalid_state", "Interpretation is already reviewed or superseded", "interpretation_review")
            run = session.get(AnalysisRun, interpretation.analysis_run_id)
            spec = session.get(AnalysisSpec, run.analysis_spec_id)
            self._current_plan(session, project_id, spec.experiment_plan_id, revision)
            if status == "accepted":
                self._assert_interpretation_current(session, interpretation, revision)
            interpretation.review_status = status
            interpretation.reviewed_at = now_utc()
            session.commit()
            return self._interpretation_read(session, interpretation)

    def revise_interpretation(self, project_id: str, interpretation_id: str,
                              revision: int, proposal: InterpretationProposal) -> dict[str, Any]:
        try:
            validate_interpretation_language(proposal)
        except ValueError as exc:
            raise ServiceError(422, "invalid_interpretation", str(exc), "interpretation_revision") from exc
        with self.session_factory() as session:
            self._project(session, project_id, revision, lock=True)
            previous = session.scalar(select(ResultInterpretation).where(
                ResultInterpretation.id == interpretation_id,
                ResultInterpretation.project_id == project_id).with_for_update())
            if previous is None:
                raise ServiceError(404, "not_found", "ResultInterpretation not found", "interpretation_revision")
            if session.scalar(select(ResultInterpretation.id).where(ResultInterpretation.supersedes_id == previous.id)):
                raise ServiceError(409, "superseded_resource", "Interpretation already has a successor", "interpretation_revision")
            run = session.get(AnalysisRun, previous.analysis_run_id)
            spec = session.get(AnalysisSpec, run.analysis_spec_id)
            self._current_plan(session, project_id, spec.experiment_plan_id, revision)
            self._assert_interpretation_current(session, previous, revision)
            if sorted(proposal.statistical_result_ids) != sorted(previous.statistical_result_ids) or sorted(proposal.figure_artifact_ids) != sorted(previous.figure_artifact_ids):
                raise ServiceError(422, "invalid_reference", "Revision cannot change computed sources", "interpretation_revision")
            revised = ResultInterpretation(project_id=project_id, analysis_run_id=previous.analysis_run_id,
                summary=proposal.summary, hypothesis_relationship=proposal.hypothesis_relationship,
                uncertainty=proposal.uncertainty, limitations=proposal.limitations,
                alternative_explanations=proposal.alternative_explanations,
                statistical_result_ids=proposal.statistical_result_ids,
                figure_artifact_ids=proposal.figure_artifact_ids,
                ai_assessment=None, review_status="pending_review", question_revision=revision,
                origin="human_revision", supersedes_id=previous.id, generation_run_id=None)
            session.add(revised)
            session.flush()
            response = self._interpretation_read(session, revised)
            session.commit()
            return response
