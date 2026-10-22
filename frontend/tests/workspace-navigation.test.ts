import assert from "node:assert/strict";
import test from "node:test";
import { resolveWorkspaceLocation, stageForAnchor, workspaceHref } from "../src/lib/workspace-navigation.ts";

test("stage links survive reloads and unknown stages fall back to literature", () => {
  assert.deepEqual(resolveWorkspaceLocation("?stage=experiment", ""), { stage: "experiment", anchor: null });
  assert.deepEqual(resolveWorkspaceLocation("?stage=unknown", ""), { stage: "literature", anchor: null });
  assert.deepEqual(resolveWorkspaceLocation("", ""), { stage: "literature", anchor: null });
});

test("source anchors reveal their owning stage, including existing links", () => {
  for (const [anchor, stage] of [["evidence-e1", "literature"], ["claim-c1", "landscape"],
    ["landscape-item-l1", "landscape"], ["gap-g1", "directions"], ["hypothesis-h1", "directions"],
    ["plan-p1", "experiment"], ["m5-title", "experiment"], ["report-title", "report"]]) {
    assert.equal(stageForAnchor(anchor), stage);
    assert.deepEqual(resolveWorkspaceLocation("?stage=report", `#${anchor}`), { stage, anchor });
  }
  assert.equal(stageForAnchor("unrelated"), null);
});

test("stage navigation preserves other query parameters and replaces the previous target", () => {
  assert.equal(workspaceHref("/projects/p?filter=current&stage=report#report-title", "landscape", "claim-c1"),
    "/projects/p?filter=current&stage=landscape#claim-c1");
  assert.equal(workspaceHref("/projects/p?stage=report#report-title", "literature"), "/projects/p?stage=literature");
});

test("encoded targets resolve safely without allowing malformed hashes to break navigation", () => {
  assert.deepEqual(resolveWorkspaceLocation("?stage=landscape", "#claim-%E4%B8%80"), { stage: "landscape", anchor: "claim-一" });
  assert.deepEqual(resolveWorkspaceLocation("?stage=report", "#%E0%A4%A"), { stage: "report", anchor: null });
});
