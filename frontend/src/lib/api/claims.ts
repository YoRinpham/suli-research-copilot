import { API_BASE_URL, requestJson } from "./client.ts";

export type ClaimType = "finding" | "method" | "comparison" | "limitation" | "other";
export type GroundingStatus = "supported" | "inferred" | "unsupported" | "insufficient_evidence";
export type HumanGroundingStatus = GroundingStatus | "pending_review";
export type AiGroundingAssessment = "directly_supported" | "inferentially_supported" | "unsupported" | "contradicted" | "insufficient_evidence";
export type Confidence = "low" | "medium" | "high" | "unknown";
export type ClaimSelection = "candidate" | "selected" | "rejected";
export type Claim = {
  id: string; project_id: string; text: string; claim_type: ClaimType;
  legacy_grounding_status: GroundingStatus | null; ai_grounding_assessment: AiGroundingAssessment | null;
  ai_risk_flags: string[] | null; ai_justification: string | null; ai_evidence_refs: string[] | null;
  ai_support_excerpts: { evidence_id: string; excerpt: string }[] | null;
  human_grounding_status: HumanGroundingStatus; human_reviewed_at: string | null;
  confidence: Confidence; uncertainty: string; evidence_for_ids: string[]; evidence_against_ids: string[];
  selection_status: ClaimSelection; question_revision: number; supersedes_id: string | null;
  generation_run_id: string | null; origin: "model" | "human_revision"; eligible_for_generation: boolean;
  created_at: string;
};
export type ClaimRevision = Pick<Claim, "text" | "claim_type" | "confidence" | "uncertainty" | "evidence_for_ids" | "evidence_against_ids"> & { expected_question_revision: number };

function path(projectId: string): string { return `/projects/${encodeURIComponent(projectId)}/claims`; }

export function listClaims(projectId: string, baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<Claim[]> {
  return requestJson(path(projectId), {}, baseUrl, fetcher);
}

export function generateClaims(projectId: string, evidenceIds: string[], revision: number, baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<{ generation_run_id: string; claims: Claim[] }> {
  return requestJson(`${path(projectId)}/generate`, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ evidence_ids: evidenceIds, expected_question_revision: revision }) }, baseUrl, fetcher);
}

export function selectClaim(projectId: string, claimId: string, status: "selected" | "rejected", revision: number, baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<Claim> {
  return requestJson(`${path(projectId)}/${encodeURIComponent(claimId)}/selection`, { method: "PATCH", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ selection_status: status, expected_question_revision: revision }) }, baseUrl, fetcher);
}

export function reviewClaimGrounding(projectId: string, claimId: string, status: GroundingStatus, revision: number, baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<Claim> {
  return requestJson(`${path(projectId)}/${encodeURIComponent(claimId)}/grounding-review`, { method: "PATCH", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ human_grounding_status: status, expected_question_revision: revision }) }, baseUrl, fetcher);
}

export function reviseClaim(projectId: string, claimId: string, body: ClaimRevision, baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<Claim> {
  return requestJson(`${path(projectId)}/${encodeURIComponent(claimId)}/revisions`, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body) }, baseUrl, fetcher);
}
