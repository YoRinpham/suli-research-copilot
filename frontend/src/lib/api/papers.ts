import { API_BASE_URL, requestJson } from "./client.ts";

export type ParseStatus = "uploaded" | "parsing" | "parsed" | "embedding" | "parse_failed" | "low_quality" | "embedding_failed" | "indexed";

export type Paper = {
  id: string;
  project_id: string;
  title: string | null;
  source_filename: string;
  parse_status: ParseStatus;
  quality_note: string | null;
  page_count: number | null;
  created_at: string;
};

export function listPapers(projectId: string, baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<Paper[]> {
  return requestJson(`/projects/${encodeURIComponent(projectId)}/papers`, {}, baseUrl, fetcher);
}

export function uploadPaper(projectId: string, file: File, baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<Paper> {
  const body = new FormData();
  body.append("file", file);
  return requestJson(`/projects/${encodeURIComponent(projectId)}/papers`, { method: "POST", body }, baseUrl, fetcher);
}

export function indexPaper(projectId: string, paperId: string, baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<Paper> {
  return requestJson(`/projects/${encodeURIComponent(projectId)}/papers/${encodeURIComponent(paperId)}/index`, { method: "POST" }, baseUrl, fetcher);
}

export function getPaperFileUrl(projectId: string, paperId: string, baseUrl = API_BASE_URL): string {
  return `${baseUrl.replace(/\/$/, "")}/api/v1/projects/${encodeURIComponent(projectId)}/papers/${encodeURIComponent(paperId)}/file`;
}
