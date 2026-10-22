import assert from "node:assert/strict";
import test from "node:test";

import { generateGaps, listGaps, selectGap, reviseGap } from "../src/lib/api/gaps.ts";
import { generateHypotheses, listHypotheses, selectHypothesis, reviseHypothesis } from "../src/lib/api/hypotheses.ts";
import { generatePlan, listPlans, reviewPlan, revisePlan } from "../src/lib/api/experiment-plans.ts";

test("M4 API clients require separate human transitions", async () => {
  const calls: Array<{ url: string; method: string; body?: Record<string, unknown> }> = [];
  const fetcher: typeof fetch = async (input, init) => {
    calls.push({ url: String(input), method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : undefined });
    return new Response(JSON.stringify({ id: "one", gaps: [], hypotheses: [], plan: {} }), { status: 200 });
  };
  await generateGaps("p", "l", 2, "http://api", fetcher);
  await listGaps("p", "http://api", fetcher);
  await selectGap("p", "g", "selected", 2, "http://api", fetcher);
  await reviseGap("p", "g", { title: "Gap", description: "Within supplied literature", gap_type: "underexplored_variable", why_it_matters: "Test", uncertainty: "Small corpus", assumptions: [], novelty_scope: "supplied_literature", landscape_item_ids: ["i"], claim_ids: ["c"], evidence_for_ids: ["e"], evidence_against_ids: [], expected_question_revision: 2 }, "http://api", fetcher);
  await generateHypotheses("p", "g", 1, 2, "http://api", fetcher);
  await listHypotheses("p", "http://api", fetcher);
  await selectHypothesis("p", "h", "selected", 2, "http://api", fetcher);
  await reviseHypothesis("p", "h", { statement: "A will exceed B", rationale: "Gap", independent_variable: "A vs B", dependent_variable: "M", control_variables: [], falsification_condition: "A does not exceed B", assumptions: [], uncertainty: "Small corpus", evidence_motivating_ids: ["e"], evidence_challenging_ids: [], claim_ids: ["c"], expected_question_revision: 2 }, "http://api", fetcher);
  await generatePlan("p", "h", 2, "http://api", fetcher);
  await listPlans("p", "http://api", fetcher);
  await reviewPlan("p", "x", "accepted", 2, "http://api", fetcher);
  await revisePlan("p", "x", { objective: "Test", independent_variable: "A vs B", dependent_variable: "M", control_variables: [], experimental_groups: ["A", "B"], baseline: "B", dataset_requirements: "D", metrics: ["M"], procedure: "Compare", analysis_approach: "Contrast", expected_observations: [], falsification_criteria: "No effect", confounders: [], limitations: [], assumptions: [], uncertainty: "Unknown", expected_question_revision: 2 }, "http://api", fetcher);
  assert.deepEqual(calls.map(c => c.method), ["POST", "GET", "PATCH", "POST", "POST", "GET", "PATCH", "POST", "POST", "GET", "PATCH", "POST"]);
  assert.equal(calls[0].body?.landscape_id, "l");
  assert.equal(calls[2].body?.selection_status, "selected");
  assert.equal(calls[6].body?.selection_status, "selected");
  assert.equal(calls[10].body?.review_status, "accepted");
});
