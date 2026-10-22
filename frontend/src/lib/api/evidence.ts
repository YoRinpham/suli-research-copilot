import { API_BASE_URL, requestJson } from "./client.ts";

export type EvidenceType = "reported_result" | "method_description" | "dataset_detail" | "limitation" | "author_claim" | "other";
export type EvidenceStatus = "selected" | "rejected";
export type Evidence = {
  id: string;
  project_id: string;
  paper_chunk_id: string;
  paper_id: string;
  paper_title: string | null;
  source_filename: string;
  page_number: number;
  section: string | null;
  source_text: string;
  evidence_type: EvidenceType;
  relevance_note: string | null;
  selection_status: EvidenceStatus;
  question_revision: number;
  paper_file_url: string;
};
export type EvidenceCreate = {
  paper_chunk_id: string;
  source_text: string;
  evidence_type: EvidenceType;
  relevance_note?: string;
  expected_question_revision: number;
};

function path(projectId: string): string {
  return `/projects/${encodeURIComponent(projectId)}/evidence`;
}

export function listEvidence(projectId: string, baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<Evidence[]> {
  return requestJson(path(projectId), {}, baseUrl, fetcher);
}

export function getEvidence(projectId: string, evidenceId: string, baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<Evidence> {
  return requestJson(`${path(projectId)}/${encodeURIComponent(evidenceId)}`, {}, baseUrl, fetcher);
}

export function createEvidence(projectId: string, body: EvidenceCreate, baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<Evidence> {
  return requestJson(path(projectId), {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }, baseUrl, fetcher);
}

export function selectEvidence(
  projectId: string, evidenceId: string, selectionStatus: EvidenceStatus, revision: number,
  baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch,
): Promise<Evidence> {
  return requestJson(`${path(projectId)}/${encodeURIComponent(evidenceId)}/selection`, {
    method: "PATCH", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ selection_status: selectionStatus, expected_question_revision: revision }),
  }, baseUrl, fetcher);
}
