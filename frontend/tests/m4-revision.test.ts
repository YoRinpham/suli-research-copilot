import assert from "node:assert/strict";
import test from "node:test";

import { revisionDraft, gapRevisionPayload, hypothesisRevisionPayload, planRevisionPayload } from "../src/lib/api/m4-revision.ts";

test("Gap revision replaces assumptions while retaining exact source authority", () => {
  const sources = { gap_type: "methodological_limitation" as const, novelty_scope: "supplied_literature" as const,
    landscape_item_ids: ["item-1"], claim_ids: ["claim-1"], evidence_for_ids: ["e-1"], evidence_against_ids: [] };
  const body = gapRevisionPayload(sources, { title: "Synthetic comparison", description: "Finite file only",
    why_it_matters: "Demonstrate traceability", uncertainty: "No population inference",
    assumptions: "Exact supplied file\r\n\nNo randomization assumed" }, 3);
  assert.deepEqual(body.assumptions, ["Exact supplied file", "No randomization assumed"]);
  assert.deepEqual(body.evidence_for_ids, ["e-1"]);
  assert.deepEqual(body.claim_ids, ["claim-1"]);
  assert.equal(body.novelty_scope, "supplied_literature");
  assert.equal(body.expected_question_revision, 3);
  assert.equal("selection_status" in body, false);
});

test("Hypothesis revision can remove inherited controls without changing its evidence", () => {
  const body = hypothesisRevisionPayload({ evidence_motivating_ids: ["e-1"], evidence_challenging_ids: [], claim_ids: ["c-1"] },
    { statement: "Finite comparison", rationale: "Synthetic exercise", independent_variable: "group A/B",
      dependent_variable: "score", falsification_condition: "Opposite observed ordering", uncertainty: "Post hoc only",
      control_variables: "", assumptions: "No causal interpretation" }, 2);
  assert.deepEqual(body.control_variables, []);
  assert.deepEqual(body.assumptions, ["No causal interpretation"]);
  assert.deepEqual(body.evidence_motivating_ids, ["e-1"]);
  assert.equal("research_gap_id" in body, false);
  assert.equal("selection_status" in body, false);
});

test("Plan revision sends reviewed variables, baseline, data requirements and every scientific list", () => {
  const draft = revisionDraft({ objective: "Synthetic file exercise", independent_variable: "group A/B",
    dependent_variable: "score", control_variables: [], experimental_groups: ["A rows", "B rows"], baseline: "B rows",
    dataset_requirements: "Only group,score in the original CSV", metrics: ["Group count", "Group mean"],
    procedure: "Read the fixed file", analysis_approach: "Finite group summaries", expected_observations: ["Retain either outcome"],
    falsification_criteria: "Compare observed ordering", confounders: [], limitations: ["No causal inference"],
    assumptions: ["No independent sampling assertion"], uncertainty: "Synthetic, post hoc" });
  assert.equal(draft.experimental_groups, "A rows\nB rows");
  assert.equal(draft.control_variables, "");
  const body = planRevisionPayload(draft, 4);
  assert.equal(body.independent_variable, "group A/B");
  assert.equal(body.dependent_variable, "score");
  assert.equal(body.baseline, "B rows");
  assert.equal(body.dataset_requirements, "Only group,score in the original CSV");
  for (const field of ["control_variables", "experimental_groups", "metrics", "expected_observations", "confounders", "limitations", "assumptions"] as const) {
    assert.deepEqual(body[field], draft[field] ? draft[field].split("\n") : []);
  }
  assert.equal(body.expected_question_revision, 4);
  assert.equal("review_status" in body, false);
  assert.equal("hypothesis_id" in body, false);
});
