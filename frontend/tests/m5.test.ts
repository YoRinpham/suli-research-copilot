import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { uploadDataset, suggestCleaning, reviewCleaning, executeCleaning,
  suggestAnalysis, reviewAnalysis, executeAnalysis, generateInterpretation,
  reviewInterpretation, deleteFigure, type CleaningOperation } from "../src/lib/api/m5.ts";
import { CleaningPlanReview } from "../src/app/projects/[id]/cleaning-plan-review.ts";

test("figure deletion submits only the requested project, artifact and current revision", async () => {
  const calls: unknown[] = [];
  const fetcher: typeof fetch = async (input, init) => {
    calls.push([String(input), init?.method, JSON.parse(String(init?.body))]);
    return new Response(JSON.stringify({ deleted_figure_id: "f/id" }), { status: 200 });
  };
  assert.deepEqual(await deleteFigure("p/id", "f/id", 3, "http://api", fetcher), { deleted_figure_id: "f/id" });
  assert.deepEqual(calls, [["http://api/api/v1/projects/p%2Fid/figures/f%2Fid/delete", "POST", { expected_question_revision: 3 }]]);
});

test("M5 client keeps upload, model proposal, human review, and execution separate", async () => {
  const calls: { path: string; method: string; body?: Record<string, unknown> }[] = [];
  const fetcher: typeof fetch = async (input, init) => {
    calls.push({ path: String(input), method: init?.method ?? "GET",
      body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined });
    return new Response(JSON.stringify({ id: "x" }), { status: 200 });
  };
  await uploadDataset("p", "plan", 2, new File(["x\n1\n"], "test.csv"), "http://api", fetcher);
  await suggestCleaning("p", "v", 2, "http://api", fetcher);
  await reviewCleaning("p", "cp", "accepted", 2, "http://api", fetcher);
  await executeCleaning("p", "cp", 2, "http://api", fetcher);
  await suggestAnalysis("p", "v", 2, "http://api", fetcher);
  await reviewAnalysis("p", "as", "accepted", 2, "http://api", fetcher);
  await executeAnalysis("p", "as", 2, "http://api", fetcher);
  await generateInterpretation("p", "ar", 2, "http://api", fetcher);
  await reviewInterpretation("p", "ri", "accepted", 2, "http://api", fetcher);
  assert.deepEqual(calls.map(call => call.method), ["POST", "POST", "PATCH", "POST", "POST", "PATCH", "POST", "POST", "PATCH"]);
  assert.equal(calls[0].path, "http://api/api/v1/projects/p/experiment-plans/plan/datasets?expected_question_revision=2");
  assert.equal(calls[2].body?.review_status, "accepted");
  assert.equal(calls[5].body?.review_status, "accepted");
  assert.equal(calls[8].body?.review_status, "accepted");
});

const renderPlanReview = (operations: unknown, status: "pending_review" | "accepted" | "rejected" = "pending_review") =>
  renderToStaticMarkup(createElement(CleaningPlanReview, { operations, status, busy: false, executed: false,
    onApprove: () => {}, onReject: () => {}, onExecute: () => {} }));

const renderOperations = (operations: CleaningOperation[]) => renderPlanReview(operations, "rejected");

test("CleaningPlan review shows exact missing tokens before approval", () => {
  const html = renderOperations([{ op: "normalize_missing", columns: ["score"], tokens: ["0", "NA", ""] }]);
  assert.match(html, /score/);
  assert.match(html, /&quot;0&quot;/);
  assert.match(html, /&quot;NA&quot;/);
  assert.match(html, /&quot;&quot;/);
  assert.match(html, /视为缺失/);
  assert.match(html, /本步骤不删除行/);
});

test("CleaningPlan review preserves the order and row-loss meaning of a destructive chain", () => {
  const html = renderOperations([
    { op: "normalize_missing", columns: ["score"], tokens: ["0"] },
    { op: "drop_missing", columns: ["score"] },
  ]);
  assert.ok(html.indexOf("normalize_missing") < html.indexOf("drop_missing"));
  assert.ok(html.indexOf("&quot;0&quot;") < html.indexOf("drop_missing"));
  assert.match(html, /缺失.*删除/);
  assert.match(html, /<ol/);
});

test("every Cleaning DSL operation has its relevant review detail", () => {
  const cases: { operation: CleaningOperation; expected: string[] }[] = [
    { operation: { op: "drop_exact_duplicates" }, expected: ["所有列", "保留首次", "删除"] },
    { operation: { op: "trim_whitespace", columns: ["label"] }, expected: ["label", "首尾空白", "缺失"] },
    { operation: { op: "normalize_missing", columns: ["score"], tokens: ["None"] }, expected: ["score", "&quot;None&quot;", "视为缺失"] },
    { operation: { op: "drop_missing", columns: ["score"] }, expected: ["score", "删除"] },
    { operation: { op: "cast_numeric", column: "score" }, expected: ["score", "数值", "失败"] },
    { operation: { op: "cast_categorical", column: "group" }, expected: ["group", "分类文本"] },
  ];
  for (const { operation, expected } of cases) {
    const html = renderOperations([operation]);
    assert.match(html, new RegExp(operation.op));
    for (const text of expected) assert.ok(html.includes(text), `${operation.op} review omits ${text}`);
  }
});

function assertApproval(html: string, enabled: boolean) {
  const button = html.match(/<button[^>]*>人工批准<\/button>/)?.[0];
  assert.ok(button, "approval button is present");
  assert.equal(button.includes('disabled=""'), !enabled);
  assert.match(html, /<button(?![^>]*disabled)[^>]*>拒绝<\/button>/);
}

test("runtime unknown operation warns and disables approval without undefined text", () => {
  const html = renderPlanReview([{ op: "future_operation", mode: "destructive" }]);
  assert.match(html, /future_operation/);
  assert.match(html, /当前界面无法审核/);
  assert.doesNotMatch(html, /undefined/);
  assertApproval(html, false);
});

test("missing or mistyped known parameters disable approval", () => {
  for (const operations of [
    [{ op: "normalize_missing", columns: ["score"] }],
    [{ op: "normalize_missing", columns: "score", tokens: ["0"] }],
  ]) {
    const html = renderPlanReview(operations);
    assert.match(html, /当前界面无法审核/);
    assert.doesNotMatch(html, /视为缺失/);
    assertApproval(html, false);
  }
});

test("unknown parameter on known operation disables approval", () => {
  const html = renderPlanReview([{
    op: "normalize_missing", columns: ["score"], tokens: ["0"], future_mode: "aggressive",
  }]);
  assert.match(html, /当前界面无法审核/);
  assert.doesNotMatch(html, /视为缺失/);
  assertApproval(html, false);
});

test("one unknown step disables approval for the entire ordered plan", () => {
  const html = renderPlanReview([
    { op: "trim_whitespace", columns: ["score"] },
    { op: "future_operation", mode: "destructive" },
    { op: "drop_missing", columns: ["score"] },
  ]);
  assert.ok(html.indexOf("trim_whitespace") < html.indexOf("future_operation"));
  assert.ok(html.indexOf("future_operation") < html.indexOf("drop_missing"));
  assert.match(html, /当前界面无法审核/);
  assertApproval(html, false);
});

test("all six current operations remain approvable", () => {
  const html = renderPlanReview([
    { op: "drop_exact_duplicates" },
    { op: "trim_whitespace", columns: ["score"] },
    { op: "normalize_missing", columns: ["score"], tokens: ["0", ""] },
    { op: "drop_missing", columns: ["score"] },
    { op: "cast_numeric", column: "score" },
    { op: "cast_categorical", column: "group" },
  ]);
  assert.match(html, /&quot;0&quot;/);
  assert.match(html, /&quot;&quot;/);
  assert.ok(html.indexOf("normalize_missing") < html.indexOf("drop_missing"));
  assert.doesNotMatch(html, /当前界面无法审核/);
  assertApproval(html, true);
});

test("an already accepted but unreviewable plan cannot be executed from the UI", () => {
  const html = renderPlanReview([{ op: "future_operation" }], "accepted");
  assert.match(html, /当前界面无法审核/);
  assert.match(html, /<button[^>]*disabled=""[^>]*>执行确定性清洗<\/button>/);
});
