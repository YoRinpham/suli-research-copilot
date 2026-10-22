import { API_BASE_URL, requestJson } from "./client.ts";

export type Project = {
  id: string;
  title: string;
  research_question: string;
  question_revision: number;
  status: string;
  created_at: string;
  updated_at: string;
};

export type NewProject = Pick<Project, "title" | "research_question">;
export type ProjectChanges = Partial<NewProject> & { expected_question_revision: number };

export function listProjects(baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<Project[]> {
  return requestJson("/projects", {}, baseUrl, fetcher);
}

export function getProject(id: string, baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<Project> {
  return requestJson(`/projects/${encodeURIComponent(id)}`, {}, baseUrl, fetcher);
}

export function createProject(data: NewProject, baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<Project> {
  return requestJson("/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  }, baseUrl, fetcher);
}

export function updateProject(id: string, changes: ProjectChanges, baseUrl = API_BASE_URL, fetcher: typeof fetch = fetch): Promise<Project> {
  return requestJson(`/projects/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(changes),
  }, baseUrl, fetcher);
}
