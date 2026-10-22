import assert from "node:assert/strict";
import test from "node:test";

import { ApiError, requestJson } from "../src/lib/api/client.ts";
import { recoverGenerationTimeout } from "../src/lib/api/generation-recovery.ts";

test("a lost generation response reads persisted objects without resubmitting", async () => {
  const calls: string[] = [];
  const saved = [{ id: "saved-claim", human_grounding_status: "pending_review", selection_status: "candidate" }];
  const fetcher: typeof fetch = async (_input, init) => {
    calls.push(init?.method ?? "GET");
    if (init?.method === "POST") {
      const response = new Response("{}", { status: 201 });
      Object.defineProperty(response, "json", { value: () => new Promise(() => undefined) });
      return response;
    }
    return new Response(JSON.stringify(saved), { status: 200 });
  };
  const errors: string[] = [];
  let displayed: unknown = [];
  let refreshed: Promise<void> | undefined;
  try {
    await requestJson("/projects/p/claims/generate", { method: "POST" }, "http://api", fetcher, 5);
    assert.fail("The lost response must time out");
  } catch (cause) {
    assert.equal(recoverGenerationTimeout(cause, () => {
      refreshed = requestJson("/projects/p/claims", {}, "http://api", fetcher).then(value => { displayed = value; });
      return refreshed;
    }, message => errors.push(message)), true);
  }
  await refreshed;
  assert.deepEqual(displayed, saved);
  assert.deepEqual(calls, ["POST", "GET"]);
  assert.match(errors[0], /服务器可能仍在处理/);
  assert.match(errors[0], /不要重复生成/);
});

test("a failed state read preserves the timeout warning and explains the missing refresh", async () => {
  const errors: string[] = [];
  recoverGenerationTimeout(new ApiError("timeout", "request_timeout", 0),
    async () => { throw new Error("offline"); }, message => errors.push(message));
  await new Promise(resolve => setImmediate(resolve));
  assert.match(errors.at(-1) ?? "", /读取已保存状态失败/);
  assert.match(errors.at(-1) ?? "", /不要重复生成/);
});

test("provider rejection is not treated as an ambiguous client timeout", () => {
  let refreshed = false;
  assert.equal(recoverGenerationTimeout(new ApiError("invalid output", "structured_output_invalid", 502),
    async () => { refreshed = true; }, () => assert.fail("The caller handles provider errors")), false);
  assert.equal(refreshed, false);
});
