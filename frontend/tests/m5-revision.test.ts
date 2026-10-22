import assert from "node:assert/strict";
import test from "node:test";
import { interpretationRevisionFields } from "../src/lib/api/m5-revision.ts";
import { reviseInterpretation } from "../src/lib/api/m5.ts";

test("interpretation revision replaces both scientific lists and preserves authoritative sources", async () => {
  const source = { statistical_result_ids: ["result-original"], figure_artifact_ids: ["figure-kept"] };
  const edited = {
    summary: "Fixed file, post hoc", hypothesis_relationship: "Compatible within file",
    uncertainty: "Construction process unknown", limitations: " File only\r\n\nPost hoc ",
    alternative_explanations: "Construction choices may contribute\n\nGeneration process unavailable",
    statistical_result_ids: ["forged"], figure_artifact_ids: ["deleted"], review_status: "accepted",
  };
  const fields = interpretationRevisionFields(source, edited);
  let submitted: Record<string, unknown> = {};
  const fetcher = (async (_url: unknown, init: RequestInit) => {
    submitted = JSON.parse(String(init.body));
    return new Response(JSON.stringify({ id: "new-revision" }), { status: 201 });
  }) as typeof fetch;
  await reviseInterpretation("p", "parent", 1, fields, "http://api", fetcher);
  assert.deepEqual(submitted.limitations, ["File only", "Post hoc"]);
  assert.deepEqual(submitted.alternative_explanations, ["Construction choices may contribute", "Generation process unavailable"]);
  assert.deepEqual(submitted.statistical_result_ids, ["result-original"]);
  assert.deepEqual(submitted.figure_artifact_ids, ["figure-kept"]);
  assert.equal(submitted.summary, "Fixed file, post hoc");
  assert.equal(submitted.expected_question_revision, 1);
  assert.equal("review_status" in submitted, false);
  assert.equal("analysis_run_id" in submitted, false);
});

test("clearing scientific lists submits empty lists rather than restoring stale assertions", () => {
  const fields = interpretationRevisionFields({ statistical_result_ids: ["r"], figure_artifact_ids: ["f"] }, {
    summary: "File only", hypothesis_relationship: "Compatible", uncertainty: "Unknown process",
    limitations: "\n ", alternative_explanations: "",
  });
  assert.deepEqual(fields.limitations, []);
  assert.deepEqual(fields.alternative_explanations, []);
});
