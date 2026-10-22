import { API_BASE_URL, requestJson } from "./client.ts";

export type ColumnProfile = { name: string; inferred_type: "numeric" | "categorical";
  missing_count: number; missing_fraction: number; unique_count: number;
  min?: number; max?: number; mean?: number; median?: number; standard_deviation?: number | null;
  categories?: { value: string; count: number }[] };
export type DataProfile = { row_count: number; column_count: number; columns: ColumnProfile[]; engine_version: string };
export type DatasetVersion = { id: string; dataset_id: string; parent_version_id: string | null;
  cleaning_run_id: string | null; kind: "raw" | "cleaned"; sha256: string;
  row_count: number; column_count: number; profile: DataProfile; created_at: string };
export type Dataset = { id: string; project_id: string; experiment_plan_id: string;
  original_filename: string; byte_size: number; sha256: string; parser_version: string;
  created_at: string; versions: DatasetVersion[] };
export type CleaningOperation =
  | { op: "drop_exact_duplicates" }
  | { op: "trim_whitespace"; columns: string[] }
  | { op: "normalize_missing"; columns: string[]; tokens: string[] }
  | { op: "drop_missing"; columns: string[] }
  | { op: "cast_numeric"; column: string }
  | { op: "cast_categorical"; column: string };
export type CleaningPlan = { id: string; source_version_id: string; operations: unknown;
  rationale: string; origin: "human" | "model"; review_status: "pending_review" | "accepted" | "rejected";
  reviewed_at: string | null };
export type CleaningRun = { id: string; cleaning_plan_id: string; source_version_id: string;
  status: "started" | "succeeded" | "failed"; rows_before: number | null; rows_after: number | null;
  columns_before: number | null; columns_after: number | null;
  effects: { operation: CleaningOperation; rows_removed: number }[] | null;
  error_type: string | null; output_version: DatasetVersion | null };
export type AnalysisSpec = { id: string; experiment_plan_id: string; hypothesis_id: string;
  dataset_version_id: string; method: string; column_mapping: Record<string, string>;
  parameters: Record<string, unknown>; rationale: string; origin: "human" | "model";
  review_status: "pending_review" | "accepted" | "rejected" };
export type StatisticalResult = { id: string; method: string; values: Record<string, unknown> };
export type AnalysisRun = { id: string; analysis_spec_id: string; dataset_version_id: string;
  hypothesis_id: string; status: "started" | "succeeded" | "failed"; method: string;
  engine_version: string; started_at: string; completed_at: string | null; error_type?: string | null;
  result: StatisticalResult | null };
export type FigureArtifact = { id: string; visualization_spec_id: string; dataset_version_id: string;
  analysis_run_id: string | null; chart_type: string; x_column: string | null;
  y_column: string | null; group_column: string | null; title: string; sha256: string; format: string;
  renderer_version: string };
export type ResultInterpretation = { id: string; analysis_run_id: string; summary: string;
  hypothesis_relationship: string; uncertainty: string; limitations: string[];
  alternative_explanations: string[]; statistical_result_ids: string[]; figure_artifact_ids: string[];
  ai_assessment: { verdict: string; risk_flags: string[]; justification: string } | null;
  review_status: "pending_review" | "accepted" | "rejected"; origin: "model" | "human_revision";
  supersedes_id: string | null; eligible_for_future_report: boolean };

const path = (projectId: string) => `/projects/${encodeURIComponent(projectId)}`;
const body = (value: unknown): RequestInit => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) });
const reviewBody = (status: "accepted" | "rejected", revision: number): RequestInit => ({ ...body({ review_status: status, expected_question_revision: revision }), method: "PATCH" });

export function uploadDataset(projectId: string, planId: string, revision: number, file: File,
  baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<Dataset> {
  const form = new FormData(); form.append("file", file);
  return requestJson(`${path(projectId)}/experiment-plans/${encodeURIComponent(planId)}/datasets?expected_question_revision=${revision}`,
    { method: "POST", body: form }, baseUrl, fetcher);
}
export function listDatasets(projectId: string, baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<Dataset[]> {
  return requestJson(`${path(projectId)}/datasets`, {}, baseUrl, fetcher);
}
export function datasetFileUrl(projectId: string, versionId: string, baseUrl = API_BASE_URL): string {
  return `${baseUrl}/api/v1${path(projectId)}/dataset-versions/${encodeURIComponent(versionId)}/file`;
}
export function createCleaning(projectId: string, versionId: string, revision: number,
  operations: CleaningOperation[], rationale: string, baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<CleaningPlan> {
  return requestJson(`${path(projectId)}/dataset-versions/${encodeURIComponent(versionId)}/cleaning-plans`,
    body({ expected_question_revision: revision, operations, rationale }), baseUrl, fetcher);
}
export function suggestCleaning(projectId: string, versionId: string, revision: number,
  baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<{ cleaning_plan: CleaningPlan }> {
  return requestJson(`${path(projectId)}/dataset-versions/${encodeURIComponent(versionId)}/cleaning-plans/suggest`,
    body({ expected_question_revision: revision }), baseUrl, fetcher);
}
export function listCleaning(projectId: string, baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<CleaningPlan[]> {
  return requestJson(`${path(projectId)}/cleaning-plans`, {}, baseUrl, fetcher);
}
export function listCleaningRuns(projectId: string, baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<CleaningRun[]> {
  return requestJson(`${path(projectId)}/cleaning-runs`, {}, baseUrl, fetcher);
}
export function reviewCleaning(projectId: string, planId: string, status: "accepted" | "rejected", revision: number,
  baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<CleaningPlan> {
  return requestJson(`${path(projectId)}/cleaning-plans/${encodeURIComponent(planId)}/review`, reviewBody(status, revision), baseUrl, fetcher);
}
export function executeCleaning(projectId: string, planId: string, revision: number,
  baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<{ id: string; status: string; effects: { rows_removed: number }[]; output_version: DatasetVersion }> {
  return requestJson(`${path(projectId)}/cleaning-plans/${encodeURIComponent(planId)}/execute`,
    body({ expected_question_revision: revision }), baseUrl, fetcher);
}
export function createAnalysis(projectId: string, versionId: string, revision: number, method: string,
  columnMapping: Record<string, string>, parameters: Record<string, unknown>, rationale: string,
  baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<AnalysisSpec> {
  return requestJson(`${path(projectId)}/dataset-versions/${encodeURIComponent(versionId)}/analysis-specs`,
    body({ expected_question_revision: revision, method, column_mapping: columnMapping, parameters, rationale }), baseUrl, fetcher);
}
export function suggestAnalysis(projectId: string, versionId: string, revision: number,
  baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<{ analysis_spec: AnalysisSpec }> {
  return requestJson(`${path(projectId)}/dataset-versions/${encodeURIComponent(versionId)}/analysis-specs/suggest`,
    body({ expected_question_revision: revision }), baseUrl, fetcher);
}
export function listAnalysisSpecs(projectId: string, baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<AnalysisSpec[]> {
  return requestJson(`${path(projectId)}/analysis-specs`, {}, baseUrl, fetcher);
}
export function reviewAnalysis(projectId: string, specId: string, status: "accepted" | "rejected", revision: number,
  baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<AnalysisSpec> {
  return requestJson(`${path(projectId)}/analysis-specs/${encodeURIComponent(specId)}/review`, reviewBody(status, revision), baseUrl, fetcher);
}
export function executeAnalysis(projectId: string, specId: string, revision: number,
  baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<AnalysisRun> {
  return requestJson(`${path(projectId)}/analysis-specs/${encodeURIComponent(specId)}/execute`,
    body({ expected_question_revision: revision }), baseUrl, fetcher);
}
export function listAnalysisRuns(projectId: string, baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<AnalysisRun[]> {
  return requestJson(`${path(projectId)}/analysis-runs`, {}, baseUrl, fetcher);
}
export function createFigure(projectId: string, versionId: string, revision: number,
  value: { chart_type: "histogram" | "box_plot" | "scatter" | "group_summary"; x_column?: string | null;
    y_column?: string | null; group_column?: string | null; title: string; analysis_run_id?: string | null;
    statistical_result_id?: string | null; options?: Record<string, unknown> },
  baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<FigureArtifact> {
  return requestJson(`${path(projectId)}/dataset-versions/${encodeURIComponent(versionId)}/figures`,
    body({ expected_question_revision: revision, ...value }), baseUrl, fetcher);
}
export function listFigures(projectId: string, baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<FigureArtifact[]> {
  return requestJson(`${path(projectId)}/figures`, {}, baseUrl, fetcher);
}
export function figureFileUrl(projectId: string, figureId: string, baseUrl = API_BASE_URL): string {
  return `${baseUrl}/api/v1${path(projectId)}/figures/${encodeURIComponent(figureId)}/file`;
}
export function generateInterpretation(projectId: string, runId: string, revision: number,
  baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<{ interpretation: ResultInterpretation }> {
  return requestJson(`${path(projectId)}/analysis-runs/${encodeURIComponent(runId)}/interpretations/generate`,
    body({ expected_question_revision: revision }), baseUrl, fetcher);
}

export function deleteFigure(projectId: string, figureId: string, revision: number,
  baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<{ deleted_figure_id: string }> {
  return requestJson(`${path(projectId)}/figures/${encodeURIComponent(figureId)}/delete`,
    body({ expected_question_revision: revision }), baseUrl, fetcher);
}
export function listInterpretations(projectId: string, baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<ResultInterpretation[]> {
  return requestJson(`${path(projectId)}/result-interpretations`, {}, baseUrl, fetcher);
}
export function reviewInterpretation(projectId: string, id: string, status: "accepted" | "rejected", revision: number,
  baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<ResultInterpretation> {
  return requestJson(`${path(projectId)}/result-interpretations/${encodeURIComponent(id)}/review`,
    reviewBody(status, revision), baseUrl, fetcher);
}
export function reviseInterpretation(projectId: string, id: string, revision: number,
  fields: Pick<ResultInterpretation, "summary" | "hypothesis_relationship" | "uncertainty" | "limitations" | "alternative_explanations" | "statistical_result_ids" | "figure_artifact_ids">,
  baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<ResultInterpretation> {
  return requestJson(`${path(projectId)}/result-interpretations/${encodeURIComponent(id)}/revisions`,
    body({ expected_question_revision: revision, ...fields }), baseUrl, fetcher);
}
