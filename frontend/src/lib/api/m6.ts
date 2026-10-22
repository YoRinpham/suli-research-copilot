import { API_BASE_URL, requestJson } from "./client.ts";

// Default server commit deadline: 2 structured-output calls × 3 hard provider attempts × 60 s + 30 s application margin = 390 s.
// This 420 s deadline stays below the report active-run stale guard (450 s).
export const REPORT_REQUEST_TIMEOUT_MS = 420_000;

export type ReportBlock = { section: "executive_summary" | "evidence_landscape" | "limitations" | "conclusion";
  text: string; source_refs: string[] };
export type ReportCitation = { token: string; evidence_id: string; claim_ids: string[]; paper_id: string;
  paper_title: string | null; source_filename: string; paper_chunk_id: string;
  page_number: number; source_text: string };
export type ResearchReport = { id: string; project_id: string; title: string;
  question_revision: number; origin: "model" | "human_revision";
  review_status: "pending_review" | "accepted" | "rejected";
  source_bundle_hash: string; current_source_bundle: boolean; final_export_eligible: boolean;
  supersedes_id: string | null; narrative_blocks: ReportBlock[];
  source_bundle: { research_question: string; gap: { title: string; description: string };
    hypothesis: { statement: string; falsification_condition: string };
    interpretation: { id: string; summary: string; uncertainty: string };
    results: { id: string; method: string; values: Record<string, unknown>; analysis_run_id: string }[];
    figures: { id: string; title: string; sha256: string; visualization_spec_id: string }[];
    citations: ReportCitation[];
    source_map: Record<string, { type: string; id: string }> } };

const base = (projectId: string) => `/projects/${encodeURIComponent(projectId)}/reports`;
const json = (body: unknown, method = "POST"): RequestInit => ({ method,
  headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

export function listReports(projectId: string, baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<ResearchReport[]> {
  return requestJson(base(projectId), {}, baseUrl, fetcher);
}
export function generateReport(projectId: string, interpretationId: string, revision: number,
  baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<{ generation_run_id: string; report: ResearchReport }> {
  return requestJson(`${base(projectId)}/generate`, json({ interpretation_id: interpretationId,
    expected_question_revision: revision }), baseUrl, fetcher, REPORT_REQUEST_TIMEOUT_MS);
}
export function previewReport(projectId: string, interpretationId: string, revision: number,
  baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<{ source_bundle_hash: string; markdown: string }> {
  return requestJson(`${base(projectId)}/preview?interpretation_id=${encodeURIComponent(interpretationId)}&expected_question_revision=${revision}`,
    {}, baseUrl, fetcher);
}
export function reviewReport(projectId: string, reportId: string, status: "accepted" | "rejected", revision: number,
  baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<ResearchReport> {
  return requestJson(`${base(projectId)}/${encodeURIComponent(reportId)}/review`,
    json({ review_status: status, expected_question_revision: revision }, "PATCH"), baseUrl, fetcher);
}
export function reviseReport(projectId: string, reportId: string, revision: number,
  title: string, blocks: ReportBlock[], baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<ResearchReport> {
  return requestJson(`${base(projectId)}/${encodeURIComponent(reportId)}/revisions`,
    json({ title, blocks, expected_question_revision: revision }), baseUrl, fetcher);
}
export function reportExportUrl(projectId: string, reportId: string, format: "markdown" | "html", draft: boolean,
  baseUrl = API_BASE_URL): string {
  return `${baseUrl}/api/v1${base(projectId)}/${encodeURIComponent(reportId)}/export?format=${format}&draft=${draft}`;
}

export type BenchmarkSummary = { comparison: "MEASURED" | "NOT MEASURED";
  time_saved_seconds: number | null; percentage_reduction: number | null;
  manual: BenchmarkSessionSummary | null;
  research_copilot: (BenchmarkSessionSummary & { copilot_metrics: Record<string, number> | null }) | null };

export type BenchmarkSessionSummary = { scenario_id: string; duration_seconds: number;
  human_decisions: number; measured_at: string; complete: boolean;
  missing_stages: string[]; invalid_stages: string[] };

export function getBenchmark(baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<BenchmarkSummary> {
  return requestJson("/benchmark", {}, baseUrl, fetcher);
}
