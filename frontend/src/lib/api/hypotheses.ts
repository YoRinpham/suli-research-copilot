import { API_BASE_URL, requestJson } from "./client.ts";
import type { M4Assessment } from "./gaps.ts";

export type Hypothesis = { id: string; project_id: string; research_gap_id: string; statement: string;
  rationale: string; independent_variable: string; dependent_variable: string; control_variables: string[];
  falsification_condition: string; assumptions: string[]; uncertainty: string;
  evidence_motivating_ids: string[]; evidence_challenging_ids: string[]; claim_ids: string[];
  ai_assessment: M4Assessment | null; selection_status: "candidate" | "selected" | "rejected";
  question_revision: number; origin: "model" | "human_revision"; supersedes_id: string | null;
  generation_run_id: string | null; eligible_for_generation: boolean; created_at: string };
export type HypothesisRevision = Pick<Hypothesis, "statement" | "rationale" | "independent_variable" | "dependent_variable" | "control_variables" | "falsification_condition" | "assumptions" | "uncertainty" | "evidence_motivating_ids" | "evidence_challenging_ids" | "claim_ids"> & { expected_question_revision: number };

function path(projectId: string): string { return `/projects/${encodeURIComponent(projectId)}/hypotheses`; }
export function listHypotheses(projectId: string, baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<Hypothesis[]> {
  return requestJson(path(projectId), {}, baseUrl, fetcher);
}
export function generateHypotheses(projectId: string, gapId: string, count: number, revision: number, baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<{ generation_run_id: string; hypotheses: Hypothesis[] }> {
  return requestJson(`/projects/${encodeURIComponent(projectId)}/gaps/${encodeURIComponent(gapId)}/hypotheses/generate`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ count, expected_question_revision: revision }) }, baseUrl, fetcher);
}
export function selectHypothesis(projectId: string, hypothesisId: string, status: "selected" | "rejected", revision: number, baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<Hypothesis> {
  return requestJson(`${path(projectId)}/${encodeURIComponent(hypothesisId)}/selection`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ selection_status: status, expected_question_revision: revision }) }, baseUrl, fetcher);
}
export function reviseHypothesis(projectId: string, hypothesisId: string, body: HypothesisRevision, baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<Hypothesis> {
  return requestJson(`${path(projectId)}/${encodeURIComponent(hypothesisId)}/revisions`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }, baseUrl, fetcher);
}
