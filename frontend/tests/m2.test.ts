import assert from "node:assert/strict";
import test from "node:test";

import { indexPaper } from "../src/lib/api/papers.ts";
import { createEvidence, getEvidence, listEvidence, selectEvidence } from "../src/lib/api/evidence.ts";
import { retrieve } from "../src/lib/api/retrieval.ts";


test("M2 client uses typed index, retrieval and explicit evidence endpoints", async () => {
  const requests: Array<{ url: string; method: string; body?: string }> = [];
  const fetcher: typeof fetch = async (input, init) => {
    requests.push({ url: String(input), method: init?.method ?? "GET", body: init?.body as string | undefined });
    return new Response(JSON.stringify({ id: "one", results: [] }), { status: 200 });
  };
  await indexPaper("project", "paper", "http://api", fetcher);
  await retrieve("project", { query: "test", top_k: 2, expected_question_revision: 3 }, "http://api", fetcher);
  await createEvidence("project", {
    paper_chunk_id: "chunk", source_text: "source", evidence_type: "other", expected_question_revision: 3,
  }, "http://api", fetcher);
  await listEvidence("project", "http://api", fetcher);
  await getEvidence("project", "evidence", "http://api", fetcher);
  await selectEvidence("project", "evidence", "rejected", 3, "http://api", fetcher);
  assert.deepEqual(requests.map(({ method }) => method), ["POST", "POST", "POST", "GET", "GET", "PATCH"]);
  assert.equal(requests[0].url, "http://api/api/v1/projects/project/papers/paper/index");
  assert.equal(requests[1].url, "http://api/api/v1/projects/project/retrievals");
  assert.equal(JSON.parse(requests[2].body ?? "{}").source_text, "source");
  assert.equal(requests[5].url, "http://api/api/v1/projects/project/evidence/evidence/selection");
});
