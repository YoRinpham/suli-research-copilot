import assert from "node:assert/strict";
import test from "node:test";

import { createProject, getProject, listProjects, updateProject } from "../src/lib/api/projects.ts";
import { getPaperFileUrl, listPapers, uploadPaper } from "../src/lib/api/papers.ts";

const project = {
  id: "p1",
  title: "材料研究",
  research_question: "哪些变量影响强度？",
  question_revision: 1,
  status: "active",
  created_at: "2026-09-28T00:00:00Z",
  updated_at: "2026-09-28T00:00:00Z",
};

test("project client uses typed project endpoints", async () => {
  const requests: Array<{ url: string; method: string; body?: string }> = [];
  const fetcher: typeof fetch = async (input, init) => {
    requests.push({ url: String(input), method: init?.method ?? "GET", body: init?.body as string | undefined });
    return new Response(JSON.stringify(!init?.method && String(input).endsWith("/projects") ? [project] : project), { status: 200 });
  };
  assert.deepEqual(await listProjects("http://api", fetcher), [project]);
  assert.deepEqual(await getProject("p1", "http://api", fetcher), project);
  assert.deepEqual(await createProject({ title: "材料研究", research_question: "哪些变量影响强度？" }, "http://api", fetcher), project);
  await updateProject("p1", { title: "新标题", expected_question_revision: 1 }, "http://api", fetcher);
  assert.deepEqual(requests.map(({ method }) => method), ["GET", "GET", "POST", "PATCH"]);
  assert.equal(JSON.parse(requests[3].body ?? "{}").expected_question_revision, 1);
});

test("paper client uploads multipart and keeps original file URL", async () => {
  const file = new File(["%PDF-test"], "paper.pdf", { type: "application/pdf" });
  const fetcher: typeof fetch = async (input, init) => {
    assert.equal(input, "http://api/api/v1/projects/p1/papers");
    if (init?.method === "POST") {
      assert.ok(init.body instanceof FormData);
      assert.equal((init.body.get("file") as File).name, "paper.pdf");
      return new Response(JSON.stringify({ id: "paper1", parse_status: "uploaded" }), { status: 201 });
    }
    return new Response(JSON.stringify([]), { status: 200 });
  };
  assert.deepEqual(await listPapers("p1", "http://api", fetcher), []);
  assert.equal((await uploadPaper("p1", file, "http://api", fetcher)).parse_status, "uploaded");
  assert.equal(getPaperFileUrl("p1", "paper1", "http://api"), "http://api/api/v1/projects/p1/papers/paper1/file");
});

test("client exposes documented backend validation errors", async () => {
  const fetcher: typeof fetch = async () => new Response(JSON.stringify({
    error: { code: "stale_revision", message: "Research question revision is stale" },
  }), { status: 409 });
  await assert.rejects(
    updateProject("p1", { title: "x", expected_question_revision: 1 }, "http://api", fetcher),
    /Research question revision is stale/,
  );
});
