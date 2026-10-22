"""M5 project-scoped lifecycle for immutable CSV data and deterministic results."""

from hashlib import sha256
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.persistence.models import (
    AnalysisRun, AnalysisSpec, CleaningPlan, CleaningRun, Dataset, DatasetVersion,
    ExperimentPlan, FigureArtifact, Hypothesis, Project, StatisticalResult,
    VisualizationSpec, now_utc,
)
from app.services.errors import ServiceError
from app.services.m4 import M4Service
from app.services.m5_data import CSVError, DATA_ENGINE_VERSION, clean_rows, parse_csv, profile_rows, serialize_csv, validate_operations
from app.services.m5_figures import CHARTS, FigureError, RENDERER_VERSION, render_png
from app.services.m5_stats import ANALYSIS_ENGINE_VERSION, METHODS, AnalysisError, run_analysis, validate_spec_profile
from app.services.m5_storage import M5Storage


class M5Service(M4Service):
    def __init__(self, settings, provider, session_factory=None):
        super().__init__(settings, provider, session_factory)
        self.storage = M5Storage(settings.m5_storage_root)

    def _current_plan(self, session: Session, project_id: str, plan_id: str,
                      revision: int | None = None) -> ExperimentPlan:
        self._project(session, project_id, revision)
        plan = session.scalar(select(ExperimentPlan).join(Hypothesis).where(
            ExperimentPlan.id == plan_id, Hypothesis.project_id == project_id))
        if plan is None:
            raise ServiceError(404, "not_found", "ExperimentPlan not found", "m5")
        if not self._plan_read(session, plan)["eligible_for_downstream"]:
            raise ServiceError(409, "invalid_state", "ExperimentPlan is not accepted and current", "m5")
        return plan

    def _dataset(self, session: Session, project_id: str, dataset_id: str) -> Dataset:
        self._project(session, project_id)
        dataset = session.scalar(select(Dataset).where(Dataset.id == dataset_id, Dataset.project_id == project_id))
        if dataset is None:
            raise ServiceError(404, "not_found", "Dataset not found", "m5")
        return dataset

    def _version(self, session: Session, project_id: str, version_id: str) -> tuple[Dataset, DatasetVersion]:
        version = session.scalar(select(DatasetVersion).join(Dataset).where(
            DatasetVersion.id == version_id, Dataset.project_id == project_id))
        if version is None:
            raise ServiceError(404, "not_found", "DatasetVersion not found", "m5")
        return self._dataset(session, project_id, version.dataset_id), version

    @staticmethod
    def _version_read(version: DatasetVersion) -> dict[str, Any]:
        return {key: getattr(version, key) for key in ("id", "dataset_id", "parent_version_id",
            "cleaning_run_id", "kind", "sha256", "row_count", "column_count", "profile", "created_at")}

    def _dataset_read(self, session: Session, dataset: Dataset) -> dict[str, Any]:
        versions = list(session.scalars(select(DatasetVersion).where(DatasetVersion.dataset_id == dataset.id)
                                        .order_by(DatasetVersion.created_at, DatasetVersion.id)))
        return {"id": dataset.id, "project_id": dataset.project_id,
                "experiment_plan_id": dataset.experiment_plan_id,
                "original_filename": dataset.original_filename, "byte_size": dataset.byte_size,
                "sha256": dataset.sha256, "parser_version": dataset.parser_version,
                "created_at": dataset.created_at, "versions": [self._version_read(row) for row in versions]}

    def upload_csv(self, project_id: str, plan_id: str, revision: int,
                   filename: str, payload: bytes) -> dict[str, Any]:
        if not filename.lower().endswith(".csv") or not filename or len(filename) > 255:
            raise ServiceError(422, "invalid_file", "Upload a CSV file", "dataset_upload")
        if not payload or len(payload) > self.settings.csv_size_limit_mb * 1024 * 1024:
            raise ServiceError(413, "file_too_large", "CSV exceeds configured size or is empty", "dataset_upload")
        with self.session_factory() as session:
            self._current_plan(session, project_id, plan_id, revision)
        try:
            rows, columns = parse_csv(payload, self.settings.csv_row_limit, self.settings.csv_column_limit)
            profile = profile_rows(rows, columns)
        except CSVError as exc:
            raise ServiceError(422, "invalid_csv", str(exc), "dataset_upload") from exc
        # The user filename is metadata only. Storage keys are UUIDs, never filename-derived.
        key, digest = self.storage.save("datasets", payload, "csv")
        try:
            with self.session_factory() as session:
                self._project(session, project_id, revision, lock=True)
                self._current_plan(session, project_id, plan_id, revision)
                dataset = Dataset(project_id=project_id, experiment_plan_id=plan_id,
                    original_filename=Path(filename).name, storage_key=key, byte_size=len(payload),
                    sha256=digest, parser_version=DATA_ENGINE_VERSION)
                session.add(dataset)
                session.flush()
                version = DatasetVersion(dataset_id=dataset.id, kind="raw", storage_key=key,
                    sha256=digest, row_count=len(rows), column_count=len(columns), profile=profile)
                session.add(version)
                session.flush()
                response = self._dataset_read(session, dataset)
                session.commit()
                return response
        except Exception:
            self.storage.discard(key)
            raise

    def list_datasets(self, project_id: str) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            self._project(session, project_id)
            rows = session.scalars(select(Dataset).where(Dataset.project_id == project_id)
                                   .order_by(Dataset.created_at, Dataset.id))
            return [self._dataset_read(session, row) for row in rows]

    def get_dataset(self, project_id: str, dataset_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            return self._dataset_read(session, self._dataset(session, project_id, dataset_id))

    def get_version(self, project_id: str, version_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            _, version = self._version(session, project_id, version_id)
            return self._version_read(version)

    def dataset_file(self, project_id: str, version_id: str) -> bytes:
        with self.session_factory() as session:
            _, version = self._version(session, project_id, version_id)
            key, digest = version.storage_key, version.sha256
        return self.storage.read(key, digest)

    def _load_rows(self, storage_key: str, digest: str) -> tuple[list[dict[str, str | None]], list[str]]:
        payload = self.storage.read(storage_key, digest)
        return parse_csv(payload, self.settings.csv_row_limit, self.settings.csv_column_limit)

    def propose_cleaning(self, project_id: str, version_id: str, revision: int,
                         operations: list[dict[str, Any]], rationale: str) -> dict[str, Any]:
        if not rationale.strip():
            raise ServiceError(422, "validation_error", "Cleaning rationale is required", "cleaning_plan")
        with self.session_factory() as session:
            dataset, version = self._version(session, project_id, version_id)
            self._current_plan(session, project_id, dataset.experiment_plan_id, revision)
            columns = [item["name"] for item in version.profile["columns"]]
        try:
            validate_operations(columns, operations)
        except CSVError as exc:
            raise ServiceError(422, "invalid_cleaning_plan", str(exc), "cleaning_plan") from exc
        with self.session_factory() as session:
            self._project(session, project_id, revision, lock=True)
            dataset, _ = self._version(session, project_id, version_id)
            self._current_plan(session, project_id, dataset.experiment_plan_id, revision)
            plan = CleaningPlan(project_id=project_id, experiment_plan_id=dataset.experiment_plan_id,
                source_version_id=version_id, operations=operations, rationale=rationale,
                origin="human", review_status="pending_review")
            session.add(plan)
            session.flush()
            response = self._cleaning_read(plan)
            session.commit()
            return response

    @staticmethod
    def _cleaning_read(plan: CleaningPlan) -> dict[str, Any]:
        return {key: getattr(plan, key) for key in ("id", "project_id", "experiment_plan_id",
            "source_version_id", "operations", "rationale", "origin", "review_status",
            "generation_run_id", "created_at", "reviewed_at")}

    def list_cleaning(self, project_id: str, version_id: str | None = None) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            self._project(session, project_id)
            query = select(CleaningPlan).where(CleaningPlan.project_id == project_id)
            if version_id:
                self._version(session, project_id, version_id)
                query = query.where(CleaningPlan.source_version_id == version_id)
            return [self._cleaning_read(row) for row in session.scalars(query.order_by(CleaningPlan.created_at, CleaningPlan.id))]

    def review_cleaning(self, project_id: str, plan_id: str, revision: int, status: str) -> dict[str, Any]:
        if status not in {"accepted", "rejected"}:
            raise ServiceError(422, "validation_error", "Invalid review decision", "cleaning_review")
        with self.session_factory() as session:
            self._project(session, project_id, revision, lock=True)
            plan = session.scalar(select(CleaningPlan).where(CleaningPlan.id == plan_id,
                CleaningPlan.project_id == project_id).with_for_update())
            if plan is None:
                raise ServiceError(404, "not_found", "CleaningPlan not found", "cleaning_review")
            self._current_plan(session, project_id, plan.experiment_plan_id, revision)
            if plan.review_status != "pending_review":
                raise ServiceError(409, "invalid_state", "CleaningPlan has already been reviewed", "cleaning_review")
            plan.review_status = status
            plan.reviewed_at = now_utc()
            session.commit()
            return self._cleaning_read(plan)

    def execute_cleaning(self, project_id: str, plan_id: str, revision: int) -> dict[str, Any]:
        with self.session_factory() as session:
            self._project(session, project_id, revision, lock=True)
            plan = session.scalar(select(CleaningPlan).where(CleaningPlan.id == plan_id,
                CleaningPlan.project_id == project_id).with_for_update())
            if plan is None:
                raise ServiceError(404, "not_found", "CleaningPlan not found", "cleaning_run")
            self._current_plan(session, project_id, plan.experiment_plan_id, revision)
            if plan.review_status != "accepted" or session.scalar(select(CleaningRun.id).where(
                    CleaningRun.cleaning_plan_id == plan.id,
                    CleaningRun.status.in_(("started", "succeeded")))):
                raise ServiceError(409, "invalid_state", "CleaningPlan is not executable", "cleaning_run")
            run = CleaningRun(project_id=project_id, cleaning_plan_id=plan.id,
                source_version_id=plan.source_version_id, status="started", engine_version=DATA_ENGINE_VERSION)
            session.add(run)
            session.commit()
            run_id = run.id
        key = None
        try:
            with self.session_factory() as session:
                dataset, version = self._version(session, project_id, plan.source_version_id)
                storage_key, digest, dataset_id = version.storage_key, version.sha256, dataset.id
            rows, columns = self._load_rows(storage_key, digest)
            cleaned, effects = clean_rows(rows, columns, plan.operations)
            payload = serialize_csv(cleaned, columns)
            profile = profile_rows(cleaned, columns)
            key, digest = self.storage.save("datasets", payload, "csv")
            with self.session_factory() as session:
                self._project(session, project_id, revision, lock=True)
                current = session.get(CleaningPlan, plan_id)
                self._current_plan(session, project_id, current.experiment_plan_id, revision)
                if current.review_status != "accepted" or current.operations != plan.operations:
                    raise ServiceError(409, "invalid_state", "CleaningPlan changed during execution", "cleaning_run")
                run = session.get(CleaningRun, run_id)
                version = DatasetVersion(dataset_id=dataset_id, parent_version_id=plan.source_version_id,
                    cleaning_run_id=run_id, kind="cleaned", storage_key=key, sha256=digest,
                    row_count=len(cleaned), column_count=len(columns), profile=profile)
                session.add(version)
                run.status = "succeeded"
                run.rows_before = len(rows)
                run.rows_after = len(cleaned)
                run.columns_before = len(columns)
                run.columns_after = len(columns)
                run.effects = effects
                run.completed_at = now_utc()
                session.flush()
                response = {"id": run.id, "status": run.status, "source_version_id": run.source_version_id,
                            "rows_before": run.rows_before, "rows_after": run.rows_after,
                            "columns_before": run.columns_before, "columns_after": run.columns_after,
                            "effects": effects, "output_version": self._version_read(version)}
                session.commit()
                return response
        except Exception as exc:
            if key:
                self.storage.discard(key)
            self._fail_deterministic(CleaningRun, run_id, type(exc).__name__)
            if isinstance(exc, ServiceError):
                raise
            raise ServiceError(422 if isinstance(exc, CSVError) else 500, "cleaning_failed",
                str(exc) if isinstance(exc, CSVError) else "Cleaning failed", "cleaning_run", {"run_id": run_id}) from exc

    def _fail_deterministic(self, model: type, run_id: str, error_type: str) -> None:
        with self.session_factory() as session:
            run = session.get(model, run_id)
            if run is not None and run.status == "started":
                run.status = "failed"
                run.error_type = error_type[:64]
                run.completed_at = now_utc()
                session.commit()

    def list_cleaning_runs(self, project_id: str) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            self._project(session, project_id)
            output = []
            for run in session.scalars(select(CleaningRun).where(CleaningRun.project_id == project_id)
                                       .order_by(CleaningRun.started_at, CleaningRun.id)):
                version = session.scalar(select(DatasetVersion).where(DatasetVersion.cleaning_run_id == run.id))
                output.append({"id": run.id, "cleaning_plan_id": run.cleaning_plan_id,
                    "source_version_id": run.source_version_id, "status": run.status,
                    "engine_version": run.engine_version, "rows_before": run.rows_before,
                    "rows_after": run.rows_after, "columns_before": run.columns_before,
                    "columns_after": run.columns_after, "effects": run.effects,
                    "error_type": run.error_type, "started_at": run.started_at,
                    "completed_at": run.completed_at,
                    "output_version": self._version_read(version) if version else None})
            return output

    @staticmethod
    def _spec_read(spec: AnalysisSpec) -> dict[str, Any]:
        return {key: getattr(spec, key) for key in ("id", "project_id", "experiment_plan_id",
            "hypothesis_id", "dataset_version_id", "method", "column_mapping", "parameters",
            "rationale", "origin", "review_status", "generation_run_id", "created_at", "reviewed_at")}

    def propose_analysis(self, project_id: str, version_id: str, revision: int, method: str,
                         mapping: dict[str, str], parameters: dict[str, Any], rationale: str) -> dict[str, Any]:
        if method not in METHODS or not rationale.strip():
            raise ServiceError(422, "invalid_analysis_spec", "Unknown method or empty rationale", "analysis_spec")
        parameters = {"missing_policy": "reject", **parameters}
        with self.session_factory() as session:
            dataset, version = self._version(session, project_id, version_id)
            plan = self._current_plan(session, project_id, dataset.experiment_plan_id, revision)
            try:
                validate_spec_profile(version.profile, method, mapping, parameters)
            except AnalysisError as exc:
                raise ServiceError(422, "invalid_analysis_spec", str(exc), "analysis_spec") from exc
            hypothesis_id = plan.hypothesis_id
        with self.session_factory() as session:
            self._project(session, project_id, revision, lock=True)
            self._current_plan(session, project_id, dataset.experiment_plan_id, revision)
            spec = AnalysisSpec(project_id=project_id, experiment_plan_id=dataset.experiment_plan_id,
                hypothesis_id=hypothesis_id, dataset_version_id=version_id, method=method,
                column_mapping=mapping, parameters=parameters, rationale=rationale,
                origin="human", review_status="pending_review")
            session.add(spec)
            session.flush()
            response = self._spec_read(spec)
            session.commit()
            return response

    def list_specs(self, project_id: str, version_id: str | None = None) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            self._project(session, project_id)
            query = select(AnalysisSpec).where(AnalysisSpec.project_id == project_id)
            if version_id:
                self._version(session, project_id, version_id)
                query = query.where(AnalysisSpec.dataset_version_id == version_id)
            return [self._spec_read(row) for row in session.scalars(query.order_by(AnalysisSpec.created_at, AnalysisSpec.id))]

    def review_spec(self, project_id: str, spec_id: str, revision: int, status: str) -> dict[str, Any]:
        if status not in {"accepted", "rejected"}:
            raise ServiceError(422, "validation_error", "Invalid review decision", "analysis_review")
        with self.session_factory() as session:
            self._project(session, project_id, revision, lock=True)
            spec = session.scalar(select(AnalysisSpec).where(AnalysisSpec.id == spec_id,
                AnalysisSpec.project_id == project_id).with_for_update())
            if spec is None:
                raise ServiceError(404, "not_found", "AnalysisSpec not found", "analysis_review")
            self._current_plan(session, project_id, spec.experiment_plan_id, revision)
            if spec.review_status != "pending_review":
                raise ServiceError(409, "invalid_state", "AnalysisSpec has already been reviewed", "analysis_review")
            spec.review_status = status
            spec.reviewed_at = now_utc()
            session.commit()
            return self._spec_read(spec)

    def execute_analysis(self, project_id: str, spec_id: str, revision: int) -> dict[str, Any]:
        with self.session_factory() as session:
            self._project(session, project_id, revision, lock=True)
            spec = session.scalar(select(AnalysisSpec).where(AnalysisSpec.id == spec_id,
                AnalysisSpec.project_id == project_id).with_for_update())
            if spec is None:
                raise ServiceError(404, "not_found", "AnalysisSpec not found", "analysis_run")
            self._current_plan(session, project_id, spec.experiment_plan_id, revision)
            if spec.review_status != "accepted":
                raise ServiceError(409, "invalid_state", "AnalysisSpec needs human approval", "analysis_run")
            self._version(session, project_id, spec.dataset_version_id)
            run = AnalysisRun(project_id=project_id, analysis_spec_id=spec.id,
                dataset_version_id=spec.dataset_version_id, hypothesis_id=spec.hypothesis_id,
                method=spec.method, column_mapping=spec.column_mapping, parameters=spec.parameters,
                status="started", engine_version=ANALYSIS_ENGINE_VERSION)
            session.add(run)
            session.commit()
            run_id = run.id
        try:
            with self.session_factory() as session:
                _, version = self._version(session, project_id, spec.dataset_version_id)
                storage_key, digest = version.storage_key, version.sha256
            rows, columns = self._load_rows(storage_key, digest)
            values = run_analysis(rows, columns, spec.method, spec.column_mapping, spec.parameters)
            with self.session_factory() as session:
                self._project(session, project_id, revision, lock=True)
                current = session.get(AnalysisSpec, spec_id)
                self._current_plan(session, project_id, current.experiment_plan_id, revision)
                if current.review_status != "accepted" or (current.method, current.column_mapping, current.parameters) != (
                        spec.method, spec.column_mapping, spec.parameters):
                    raise ServiceError(409, "invalid_state", "AnalysisSpec changed during execution", "analysis_run")
                result = StatisticalResult(project_id=project_id, analysis_run_id=run_id,
                    method=values["method"], values=values)
                session.add(result)
                run = session.get(AnalysisRun, run_id)
                run.status = "succeeded"
                run.completed_at = now_utc()
                session.flush()
                response = {"id": run.id, "analysis_spec_id": spec_id,
                    "dataset_version_id": run.dataset_version_id, "hypothesis_id": run.hypothesis_id,
                    "status": run.status, "method": run.method, "engine_version": run.engine_version,
                    "started_at": run.started_at, "completed_at": run.completed_at,
                    "result": {"id": result.id, "method": result.method, "values": result.values}}
                session.commit()
                return response
        except Exception as exc:
            self._fail_deterministic(AnalysisRun, run_id, type(exc).__name__)
            if isinstance(exc, ServiceError):
                raise
            raise ServiceError(422 if isinstance(exc, (CSVError, AnalysisError)) else 500,
                "analysis_failed", str(exc) if isinstance(exc, (CSVError, AnalysisError)) else "Analysis failed",
                "analysis_run", {"run_id": run_id}) from exc

    def list_runs(self, project_id: str, version_id: str | None = None) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            self._project(session, project_id)
            query = select(AnalysisRun).where(AnalysisRun.project_id == project_id)
            if version_id:
                self._version(session, project_id, version_id)
                query = query.where(AnalysisRun.dataset_version_id == version_id)
            output = []
            for run in session.scalars(query.order_by(AnalysisRun.started_at, AnalysisRun.id)):
                result = session.scalar(select(StatisticalResult).where(StatisticalResult.analysis_run_id == run.id))
                output.append({"id": run.id, "analysis_spec_id": run.analysis_spec_id,
                    "dataset_version_id": run.dataset_version_id, "hypothesis_id": run.hypothesis_id,
                    "status": run.status, "method": run.method, "engine_version": run.engine_version,
                    "started_at": run.started_at, "completed_at": run.completed_at,
                    "error_type": run.error_type,
                    "result": {"id": result.id, "method": result.method, "values": result.values} if result else None})
            return output

    def get_run(self, project_id: str, run_id: str) -> dict[str, Any]:
        for run in self.list_runs(project_id):
            if run["id"] == run_id:
                return run
        raise ServiceError(404, "not_found", "AnalysisRun not found", "analysis_run")

    def get_statistical_result(self, project_id: str, result_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            self._project(session, project_id)
            result = session.scalar(select(StatisticalResult).where(StatisticalResult.id == result_id,
                StatisticalResult.project_id == project_id))
            if result is None:
                raise ServiceError(404, "not_found", "StatisticalResult not found", "statistical_result")
            run = session.get(AnalysisRun, result.analysis_run_id)
            return {"id": result.id, "analysis_run_id": run.id, "analysis_spec_id": run.analysis_spec_id,
                    "dataset_version_id": run.dataset_version_id, "hypothesis_id": run.hypothesis_id,
                    "method": result.method, "values": result.values, "created_at": result.created_at}

    def create_figure(self, project_id: str, version_id: str, revision: int, chart_type: str,
                      x_column: str | None, y_column: str | None, group_column: str | None,
                      title: str, options: dict[str, Any], analysis_run_id: str | None = None,
                      statistical_result_id: str | None = None) -> dict[str, Any]:
        if chart_type not in CHARTS:
            raise ServiceError(422, "invalid_figure", "Unknown chart type", "figure")
        with self.session_factory() as session:
            self._project(session, project_id, revision, lock=True)
            dataset, version = self._version(session, project_id, version_id)
            self._current_plan(session, project_id, dataset.experiment_plan_id, revision)
            if analysis_run_id:
                run = session.scalar(select(AnalysisRun).where(AnalysisRun.id == analysis_run_id,
                    AnalysisRun.project_id == project_id, AnalysisRun.dataset_version_id == version_id,
                    AnalysisRun.status == "succeeded"))
                if run is None:
                    raise ServiceError(404, "not_found", "AnalysisRun not found", "figure")
            if statistical_result_id:
                result = session.scalar(select(StatisticalResult).where(StatisticalResult.id == statistical_result_id,
                    StatisticalResult.project_id == project_id,
                    StatisticalResult.analysis_run_id == analysis_run_id))
                if result is None:
                    raise ServiceError(404, "not_found", "StatisticalResult not found", "figure")
            spec = VisualizationSpec(project_id=project_id, dataset_version_id=version_id,
                analysis_run_id=analysis_run_id, statistical_result_id=statistical_result_id,
                chart_type=chart_type, x_column=x_column, y_column=y_column, group_column=group_column,
                title=title, options=options, origin="human", status="started")
            session.add(spec)
            session.commit()
            spec_id = spec.id
        key = None
        try:
            with self.session_factory() as session:
                _, version = self._version(session, project_id, version_id)
                storage_key, digest = version.storage_key, version.sha256
            rows, columns = self._load_rows(storage_key, digest)
            image = render_png(rows, columns, chart_type, x_column, y_column, group_column, title, options)
            key, digest = self.storage.save("figures", image, "png")
            with self.session_factory() as session:
                self._project(session, project_id, revision, lock=True)
                dataset, _ = self._version(session, project_id, version_id)
                self._current_plan(session, project_id, dataset.experiment_plan_id, revision)
                spec = session.get(VisualizationSpec, spec_id)
                artifact = FigureArtifact(project_id=project_id, visualization_spec_id=spec_id,
                    dataset_version_id=version_id, analysis_run_id=analysis_run_id,
                    storage_key=key, sha256=digest, format="png", renderer_version=RENDERER_VERSION)
                session.add(artifact)
                spec.status = "succeeded"
                spec.completed_at = now_utc()
                session.flush()
                response = self._figure_read(spec, artifact)
                session.commit()
                return response
        except Exception as exc:
            if key:
                self.storage.discard(key)
            self._fail_deterministic(VisualizationSpec, spec_id, type(exc).__name__)
            if isinstance(exc, ServiceError):
                raise
            raise ServiceError(422 if isinstance(exc, (CSVError, FigureError)) else 500,
                "figure_failed", str(exc) if isinstance(exc, (CSVError, FigureError)) else "Figure rendering failed",
                "figure", {"visualization_spec_id": spec_id}) from exc

    @staticmethod
    def _figure_read(spec: VisualizationSpec, artifact: FigureArtifact) -> dict[str, Any]:
        return {"id": artifact.id, "visualization_spec_id": spec.id,
            "dataset_version_id": artifact.dataset_version_id, "analysis_run_id": artifact.analysis_run_id,
            "statistical_result_id": spec.statistical_result_id, "chart_type": spec.chart_type,
            "x_column": spec.x_column, "y_column": spec.y_column, "group_column": spec.group_column,
            "title": spec.title, "sha256": artifact.sha256, "format": artifact.format,
            "renderer_version": artifact.renderer_version, "created_at": artifact.created_at}

    def list_figures(self, project_id: str, version_id: str | None = None) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            self._project(session, project_id)
            query = select(FigureArtifact).where(FigureArtifact.project_id == project_id)
            if version_id:
                self._version(session, project_id, version_id)
                query = query.where(FigureArtifact.dataset_version_id == version_id)
            return [self._figure_read(session.get(VisualizationSpec, item.visualization_spec_id), item)
                    for item in session.scalars(query.order_by(FigureArtifact.created_at, FigureArtifact.id))]

    def get_figure(self, project_id: str, figure_id: str) -> dict[str, Any]:
        for figure in self.list_figures(project_id):
            if figure["id"] == figure_id:
                return figure
        raise ServiceError(404, "not_found", "FigureArtifact not found", "figure")

    def figure_file(self, project_id: str, figure_id: str) -> bytes:
        with self.session_factory() as session:
            self._project(session, project_id)
            artifact = session.scalar(select(FigureArtifact).where(FigureArtifact.id == figure_id,
                FigureArtifact.project_id == project_id))
            if artifact is None:
                raise ServiceError(404, "not_found", "FigureArtifact not found", "figure")
            key, digest = artifact.storage_key, artifact.sha256
        return self.storage.read(key, digest)

    def delete_figure(self, project_id: str, figure_id: str, revision: int) -> dict[str, str]:
        with self.session_factory() as session:
            self._project(session, project_id, revision, lock=True)
            artifact = session.scalar(select(FigureArtifact).where(
                FigureArtifact.id == figure_id, FigureArtifact.project_id == project_id).with_for_update())
            if artifact is None:
                raise ServiceError(404, "not_found", "FigureArtifact not found", "figure_delete")
            try:
                self.storage.discard_figure(artifact.storage_key)
            except (OSError, ValueError) as exc:
                raise ServiceError(500, "figure_delete_failed",
                    "Figure file could not be deleted; no record was removed", "figure_delete") from exc
            try:
                session.delete(artifact)
                session.commit()
            except SQLAlchemyError as exc:
                raise ServiceError(500, "figure_delete_failed",
                    "Figure file was removed but metadata cleanup failed; retry deletion", "figure_delete") from exc
        return {"deleted_figure_id": figure_id}
