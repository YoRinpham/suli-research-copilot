import assert from "node:assert/strict";
import test from "node:test";
import { withRefreshFence } from "../src/lib/refresh-fence.ts";

test("reads begun before or during a human decision cannot restore its older state", async () => {
  const epoch = { current: 0 };
  const before = ++epoch.current;
  let finish!: (value: string) => void;
  const decision = withRefreshFence(epoch, () => new Promise<string>(resolve => { finish = resolve; }));
  assert.notEqual(epoch.current, before);
  const during = ++epoch.current;
  finish("supported");
  assert.equal(await decision, "supported");
  assert.notEqual(epoch.current, during);
  const after = ++epoch.current;
  assert.equal(epoch.current, after);
});
test("failed mutations also invalidate ambiguous in-flight snapshots and keep the original error", async () => {
  const epoch = { current: 0 };
  const error = new Error("response lost");
  let fail!: (reason: Error) => void;
  const decision = withRefreshFence(epoch, () => new Promise<void>((_resolve, reject) => { fail = reject; }));
  const during = ++epoch.current;
  fail(error);
  await assert.rejects(decision, cause => cause === error);
  assert.notEqual(epoch.current, during);
});
