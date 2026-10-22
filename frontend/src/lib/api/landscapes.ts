import { API_BASE_URL, requestJson } from "./client.ts";

export type LandscapeCategory = "approach" | "method" | "dataset" | "metric" | "finding" | "conflict";
export type LandscapeItem = { id: string; category: LandscapeCategory; text: string; uncertainty: string; claim_ids: string[]; evidence_ids: string[]; ai_conflict_assessment: Record<string, unknown> | null };
export type Landscape = { id: string; project_id: string; summary: string; uncertainty: string; items: LandscapeItem[];
  question_revision: number; supersedes_id: string | null; generation_run_id: string | null;
  origin: "model" | "human_revision"; review_status: "pending_review" | "accepted" | "rejected";
  reviewed_at: string | null; ai_summary_assessments: Record<string, unknown>[] | null;
  eligible_for_downstream: boolean; created_at: string };
export type LandscapeRevision = { summary: string; uncertainty: string; items: Pick<LandscapeItem, "category" | "text" | "uncertainty" | "claim_ids" | "evidence_ids">[]; expected_question_revision: number };

function path(projectId: string): string { return `/projects/${encodeURIComponent(projectId)}/landscapes`; }

export function listLandscapes(projectId: string, baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<Landscape[]> {
  return requestJson(path(projectId), {}, baseUrl, fetcher);
}

export function generateLandscape(projectId: string, claimIds: string[], revision: number, baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<{ generation_run_id: string; landscape: Landscape }> {
  return requestJson(`${path(projectId)}/generate`, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ claim_ids: claimIds, expected_question_revision: revision }) }, baseUrl, fetcher);
}

export function reviseLandscape(projectId: string, landscapeId: string, body: LandscapeRevision, baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<Landscape> {
  return requestJson(`${path(projectId)}/${encodeURIComponent(landscapeId)}/revisions`, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body) }, baseUrl, fetcher);
}

export function reviewLandscape(projectId: string, landscapeId: string, status: "accepted" | "rejected", revision: number, baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<Landscape> {
  return requestJson(`${path(projectId)}/${encodeURIComponent(landscapeId)}/review`, { method: "PATCH", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ review_status: status, expected_question_revision: revision }) }, baseUrl, fetcher);
}
