import { API_BASE_URL, requestJson } from "./client.ts";

export type GapType = "contradictory_findings" | "underexplored_variable" | "missing_comparison" | "limited_dataset" | "insufficient_replication" | "methodological_limitation" | "untested_interaction" | "unexplored_domain" | "unresolved_cause" | "evaluation_inconsistency";
export type M4Assessment = { verdict: "aligned" | "concerns" | "misaligned"; risk_flags: string[]; source_ids: string[]; justification: string };
export type Gap = { id: string; project_id: string; landscape_id: string; title: string; description: string;
  gap_type: GapType; why_it_matters: string; uncertainty: string; assumptions: string[];
  novelty_scope: "supplied_literature"; landscape_item_ids: string[]; claim_ids: string[];
  evidence_for_ids: string[]; evidence_against_ids: string[]; ai_assessment: M4Assessment | null;
  selection_status: "candidate" | "selected" | "rejected"; question_revision: number;
  origin: "model" | "human_revision"; supersedes_id: string | null; generation_run_id: string | null;
  eligible_for_generation: boolean; created_at: string };
export type GapRevision = Pick<Gap, "title" | "description" | "gap_type" | "why_it_matters" | "uncertainty" | "assumptions" | "novelty_scope" | "landscape_item_ids" | "claim_ids" | "evidence_for_ids" | "evidence_against_ids"> & { expected_question_revision: number };

function path(projectId: string): string { return `/projects/${encodeURIComponent(projectId)}/gaps`; }

export function listGaps(projectId: string, baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<Gap[]> {
  return requestJson(path(projectId), {}, baseUrl, fetcher);
}
export function generateGaps(projectId: string, landscapeId: string, revision: number, baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<{ generation_run_id: string; gaps: Gap[] }> {
  return requestJson(`${path(projectId)}/generate`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ landscape_id: landscapeId, expected_question_revision: revision }) }, baseUrl, fetcher);
}
export function selectGap(projectId: string, gapId: string, status: "selected" | "rejected", revision: number, baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<Gap> {
  return requestJson(`${path(projectId)}/${encodeURIComponent(gapId)}/selection`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ selection_status: status, expected_question_revision: revision }) }, baseUrl, fetcher);
}
export function reviseGap(projectId: string, gapId: string, body: GapRevision, baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<Gap> {
  return requestJson(`${path(projectId)}/${encodeURIComponent(gapId)}/revisions`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }, baseUrl, fetcher);
}
