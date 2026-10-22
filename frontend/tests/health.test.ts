import assert from "node:assert/strict";
import test from "node:test";

import { getHealth } from "../src/lib/api/health.ts";

test("health client returns typed status from backend", async () => {
  const fetcher = async (input: RequestInfo | URL) => {
    assert.equal(input, "http://localhost:8000/api/v1/health");
    return new Response(JSON.stringify({ status: "ok", database: "connected" }), {
      status: 200,
    });
  };

  assert.deepEqual(await getHealth("http://localhost:8000", fetcher), {
    status: "ok",
    database: "connected",
  });
});

test("health client reports unavailable backend", async () => {
  const fetcher = async () => new Response("unavailable", { status: 503 });
  await assert.rejects(
    getHealth("http://localhost:8000", fetcher),
    /健康检查失败：503/,
  );
});
