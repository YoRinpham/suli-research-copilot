import { API_BASE_URL, requestJson } from "./client.ts";
import type { M4Assessment } from "./gaps.ts";

export type ExperimentPlan = { id: string; hypothesis_id: string; objective: string;
  independent_variable: string; dependent_variable: string; control_variables: string[];
  experimental_groups: string[]; baseline: string; dataset_requirements: string; metrics: string[];
  procedure: string; analysis_approach: string; expected_observations: string[];
  falsification_criteria: string; confounders: string[]; limitations: string[]; assumptions: string[];
  uncertainty: string; ai_alignment_assessment: M4Assessment | null;
  review_status: "pending_review" | "accepted" | "rejected"; reviewed_at: string | null;
  question_revision: number; origin: "model" | "human_revision"; supersedes_id: string | null;
  generation_run_id: string | null; eligible_for_downstream: boolean; created_at: string };
export type PlanRevision = Pick<ExperimentPlan, "objective" | "independent_variable" | "dependent_variable" | "control_variables" | "experimental_groups" | "baseline" | "dataset_requirements" | "metrics" | "procedure" | "analysis_approach" | "expected_observations" | "falsification_criteria" | "confounders" | "limitations" | "assumptions" | "uncertainty"> & { expected_question_revision: number };

function path(projectId: string): string { return `/projects/${encodeURIComponent(projectId)}/experiment-plans`; }
export function listPlans(projectId: string, baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<ExperimentPlan[]> {
  return requestJson(path(projectId), {}, baseUrl, fetcher);
}
export function generatePlan(projectId: string, hypothesisId: string, revision: number, baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<{ generation_run_id: string; plan: ExperimentPlan }> {
  return requestJson(`/projects/${encodeURIComponent(projectId)}/hypotheses/${encodeURIComponent(hypothesisId)}/experiment-plans/generate`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ expected_question_revision: revision }) }, baseUrl, fetcher);
}
export function reviewPlan(projectId: string, planId: string, status: "accepted" | "rejected", revision: number, baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<ExperimentPlan> {
  return requestJson(`${path(projectId)}/${encodeURIComponent(planId)}/review`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ review_status: status, expected_question_revision: revision }) }, baseUrl, fetcher);
}
export function revisePlan(projectId: string, planId: string, body: PlanRevision, baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<ExperimentPlan> {
  return requestJson(`${path(projectId)}/${encodeURIComponent(planId)}/revisions`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }, baseUrl, fetcher);
}
