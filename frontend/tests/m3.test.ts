import assert from "node:assert/strict";
import test from "node:test";

import { generateClaims, listClaims, reviewClaimGrounding, reviseClaim, selectClaim } from "../src/lib/api/claims.ts";
import { generateLandscape, listLandscapes, reviewLandscape, reviseLandscape } from "../src/lib/api/landscapes.ts";

test("M3 client keeps Claim and Landscape transitions explicit", async () => {
  const requests: Array<{ url: string; method: string; body?: string }> = [];
  const fetcher: typeof fetch = async (input, init) => {
    requests.push({ url: String(input), method: init?.method ?? "GET", body: init?.body as string | undefined });
    return new Response(JSON.stringify({ id: "one", claims: [], landscape: { items: [] } }), { status: 200 });
  };
  await generateClaims("project", ["e1"], 2, "http://api", fetcher);
  await listClaims("project", "http://api", fetcher);
  await reviewClaimGrounding("project", "c1", "supported", 2, "http://api", fetcher);
  await selectClaim("project", "c1", "selected", 2, "http://api", fetcher);
  await reviseClaim("project", "c1", { text: "Revised", claim_type: "finding", confidence: "low", uncertainty: "small sample", evidence_for_ids: ["e1"], evidence_against_ids: [], expected_question_revision: 2 }, "http://api", fetcher);
  await generateLandscape("project", ["c1"], 2, "http://api", fetcher);
  await listLandscapes("project", "http://api", fetcher);
  await reviewLandscape("project", "l1", "accepted", 2, "http://api", fetcher);
  await reviseLandscape("project", "l1", { summary: "Revised", uncertainty: "small corpus", items: [{ category: "finding", text: "A", uncertainty: "small sample", claim_ids: ["c1"], evidence_ids: ["e1"] }], expected_question_revision: 2 }, "http://api", fetcher);
  assert.deepEqual(requests.map((item) => item.method), ["POST", "GET", "PATCH", "PATCH", "POST", "POST", "GET", "PATCH", "POST"]);
  assert.equal(requests[0].url, "http://api/api/v1/projects/project/claims/generate");
  assert.deepEqual(JSON.parse(requests[5].body ?? "{}").claim_ids, ["c1"]);
  assert.equal(requests[2].url, "http://api/api/v1/projects/project/claims/c1/grounding-review");
  assert.equal(requests[7].url, "http://api/api/v1/projects/project/landscapes/l1/review");
  assert.equal(requests[8].url, "http://api/api/v1/projects/project/landscapes/l1/revisions");
});
