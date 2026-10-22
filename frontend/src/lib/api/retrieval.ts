import { API_BASE_URL, requestJson } from "./client.ts";

export type RetrievalResult = {
  chunk_id: string;
  paper_id: string;
  paper_title: string | null;
  page_number: number;
  section: string | null;
  text: string;
  similarity_score: number;
};

export type RetrievalResponse = { query: string; results: RetrievalResult[] };
export type RetrievalRequest = { query?: string; top_k?: number; expected_question_revision: number };

export function retrieve(
  projectId: string, body: RetrievalRequest, baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch,
): Promise<RetrievalResponse> {
  return requestJson(`/projects/${encodeURIComponent(projectId)}/retrievals`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }, baseUrl, fetcher);
}
