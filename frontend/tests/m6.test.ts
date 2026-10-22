import assert from "node:assert/strict";
import test from "node:test";

import { generateReport, previewReport, reviewReport, reviseReport, reportExportUrl, getBenchmark } from "../src/lib/api/m6.ts";
import { requestJson, ApiError, DEFAULT_REQUEST_TIMEOUT_MS } from "../src/lib/api/client.ts";
import { REPORT_REQUEST_TIMEOUT_MS } from "../src/lib/api/m6.ts";
import { runReportAction } from "../src/lib/api/report-action.ts";

test("M6 client keeps generation, human review, revision and export distinct", async () => {
  const calls: { path: string; method: string; body?: Record<string, unknown> }[] = [];
  const fetcher: typeof fetch = async (input, init) => {
    calls.push({ path: String(input), method: init?.method ?? "GET",
      body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined });
    return new Response(JSON.stringify({ id: "r" }), { status: 200 });
  };
  await generateReport("p", "i", 2, "http://api", fetcher);
  await reviewReport("p", "r", "accepted", 2, "http://api", fetcher);
  await reviseReport("p", "r", 2, "Title", [{ section: "conclusion", text: "Cautious.", source_refs: ["RESULT_1"] }], "http://api", fetcher);
  assert.deepEqual(calls.map(c => c.method), ["POST", "PATCH", "POST"]);
  assert.equal(calls[0].body?.interpretation_id, "i");
  assert.equal(calls[1].body?.review_status, "accepted");
  assert.equal(calls[2].body?.expected_question_revision, 2);
  assert.match(reportExportUrl("p", "r", "markdown", true, "http://api"), /draft=true/);
});

test("benchmark client reads measured summary separately from project report", async () => {
  let path = "";
  const fetcher: typeof fetch = async input => { path = String(input); return new Response(JSON.stringify({
    comparison: "NOT MEASURED", manual: null, research_copilot: null }), { status: 200 }); };
  const summary = await getBenchmark("http://api", fetcher);
  assert.equal(path, "http://api/api/v1/benchmark");
  assert.equal(summary.comparison, "NOT MEASURED");
});

test("deterministic report preview does not submit generation", async () => {
  let path = "";
  let method = "";
  const fetcher: typeof fetch = async (input, init) => { path = String(input); method = init?.method ?? "GET";
    return new Response(JSON.stringify({ markdown: "# Preview" }), { status: 200 }); };
  const result = await previewReport("p", "i", 2, "http://api", fetcher);
  assert.equal(method, "GET");
  assert.match(path, /\/projects\/p\/reports\/preview\?interpretation_id=i&expected_question_revision=2/);
  assert.equal(result.markdown, "# Preview");
});

test("shared client returns fast responses and controlled server errors", async () => {
  const ok: typeof fetch = async () => new Response(JSON.stringify({ id: "ok" }), { status: 200 });
  assert.deepEqual(await requestJson("/health", {}, "http://api", ok, 20), { id: "ok" });
  const failed: typeof fetch = async () => new Response(JSON.stringify({ error: { code: "conflict", message: "已存在" } }), { status: 409 });
  await assert.rejects(requestJson("/health", {}, "http://api", failed, 20),
    (error: unknown) => error instanceof ApiError && error.code === "conflict" && error.status === 409);
});

test("report generation succeeds after the old deadline without a retry warning", async t => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let finishResponse: ((value: Response) => void) | undefined;
  const fetcher: typeof fetch = async () => new Promise<Response>(resolve => { finishResponse = resolve; });
  const busy: boolean[] = [];
  const errors: string[] = [];
  let succeeded = 0;
  const gate = { active: false };
  const action = runReportAction(gate, () => generateReport("p", "i", 1, "http://api", fetcher),
    async () => {}, value => busy.push(value), () => { succeeded++; }, message => errors.push(message));
  t.mock.timers.tick(DEFAULT_REQUEST_TIMEOUT_MS + 1);
  finishResponse?.(new Response(JSON.stringify({ report: { id: "late-success" } }), { status: 201 }));
  await action;
  t.mock.timers.tick(REPORT_REQUEST_TIMEOUT_MS);
  assert.equal(succeeded, 1);
  assert.deepEqual(busy, [true, false]);
  assert.deepEqual(errors, []);
  assert.equal(gate.active, false);
});

test("report generation times out at its own deadline and permits retry", async t => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const signals: AbortSignal[] = [];
  const hanging: typeof fetch = async (_input, init) => {
    signals.push(init!.signal!);
    return new Promise<Response>(() => undefined);
  };
  const busy: boolean[] = [];
  const errors: string[] = [];
  const gate = { active: false };
  const action = runReportAction(gate, () => generateReport("p", "i", 1, "http://api", hanging),
    async () => {}, value => busy.push(value), () => {}, message => errors.push(message));
  t.mock.timers.tick(REPORT_REQUEST_TIMEOUT_MS + 1);
  await action;
  assert.equal(signals[0].aborted, true);
  assert.deepEqual(busy, [true, false]);
  assert.equal(gate.active, false);
  assert.match(errors[0], /超时/);
  assert.doesNotMatch(errors[0], /AbortError/);
  await runReportAction(gate, async () => ({}), async () => {}, () => {}, () => {}, () => {});
});

test("report deadline includes response-body parsing", async t => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const response = new Response("{}", { status: 201 });
  Object.defineProperty(response, "json", { value: () => new Promise<never>(() => undefined) });
  const pending = generateReport("p", "i", 1, "http://api", async () => response);
  t.mock.timers.tick(REPORT_REQUEST_TIMEOUT_MS + 1);
  await assert.rejects(pending,
    (error: unknown) => error instanceof ApiError && error.code === "request_timeout");
});

test("completed requests clear timers without later aborting their controllers", async t => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const signals: AbortSignal[] = [];
  const fetcher: typeof fetch = async (_input, init) => {
    signals.push(init!.signal!);
    return new Response(JSON.stringify(signals.length === 1 ? { ok: true } :
      { error: { code: "conflict", message: "already active" } }), { status: signals.length === 1 ? 200 : 409 });
  };
  await requestJson("/health", {}, "http://api", fetcher, 10);
  await assert.rejects(requestJson("/health", {}, "http://api", fetcher, 10),
    (error: unknown) => error instanceof ApiError && error.status === 409);
  t.mock.timers.tick(11);
  assert.deepEqual(signals.map(signal => signal.aborted), [false, false]);
});

test("hanging request aborts at deadline and report action clears busy for safe retry", async () => {
  let submissions = 0;
  const hanging: typeof fetch = async (_input, init) => {
    submissions++;
    return new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), { once: true });
    });
  };
  await assert.rejects(requestJson("/reports/generate", {}, "http://api", hanging, 10),
    (error: unknown) => error instanceof ApiError && error.code === "request_timeout" && !error.message.includes("AbortError"));
  const gate = { active: false };
  const busy: boolean[] = [];
  const errors: string[] = [];
  let refreshes = 0;
  const action = () => requestJson("/reports/generate", {}, "http://api", hanging, 10);
  const first = runReportAction(gate, action, async () => { refreshes++; }, value => busy.push(value),
    () => {}, message => errors.push(message));
  const duplicate = runReportAction(gate, action, async () => { refreshes++; }, value => busy.push(value),
    () => {}, message => errors.push(message));
  await Promise.all([first, duplicate]);
  assert.equal(submissions, 2); // one direct probe, one guarded UI action
  assert.deepEqual(busy, [true, false]);
  assert.equal(gate.active, false);
  assert.equal(refreshes, 1);
  assert.match(errors[0], /超时/);
  await runReportAction(gate, async () => ({ id: "saved" }), async () => { refreshes++; },
    value => busy.push(value), () => {}, message => errors.push(message));
  assert.deepEqual(busy, [true, false, true, false]);
  assert.equal(refreshes, 2);
});

test("report timeout clears busy even when state refresh also hangs", async () => {
  const gate = { active: false };
  const busy: boolean[] = [];
  const errors: string[] = [];
  let releaseRefresh: (() => void) | undefined;
  const refresh = () => new Promise<void>(resolve => { releaseRefresh = resolve; });
  const action = () => Promise.reject(new ApiError("请求超时", "request_timeout", 0));
  const completed = await Promise.race([
    runReportAction(gate, action, refresh, value => busy.push(value), () => {}, message => errors.push(message)).then(() => true),
    new Promise<boolean>(resolve => setTimeout(() => resolve(false), 20)),
  ]);
  releaseRefresh?.();
  assert.equal(completed, true);
  assert.deepEqual(busy, [true, false]);
  assert.equal(gate.active, false);
  assert.match(errors[0], /超时/);
});

test("request deadline also covers a response body that never finishes", async () => {
  const response = new Response("{}", { status: 200 });
  Object.defineProperty(response, "json", { value: () => new Promise<never>(() => undefined) });
  const fetcher: typeof fetch = async () => response;
  await assert.rejects(requestJson("/reports", {}, "http://api", fetcher, 10),
    (error: unknown) => error instanceof ApiError && error.code === "request_timeout");
});
