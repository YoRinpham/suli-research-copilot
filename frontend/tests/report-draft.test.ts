import assert from "node:assert/strict";
import test from "node:test";
import { reconcileReportDraft, type ReportDraft } from "../src/lib/report-draft.ts";

const first = { id: "r1", title: "已保存的报告", narrative_blocks: [{ section: "conclusion" as const, text: "原始文本", source_refs: ["E1"] }] };
const latest = { ...first, id: "r2", title: "新报告" };
const empty: ReportDraft = { reportId: "", title: "", blocks: [], dirty: false };

test("first report load opens the newest server version", () => {
  assert.equal(reconcileReportDraft(empty, [first, latest]).reportId, "r2");
});
test("returning to reports preserves unsaved edits and the chosen historical version", () => {
  const editing = { ...empty, reportId: "r1", title: "还未保存的标题", blocks: [{ ...first.narrative_blocks[0], text: "正在修改" }], dirty: true };
  assert.deepEqual(reconcileReportDraft(editing, [first, latest]), editing);
  assert.equal(reconcileReportDraft({ ...editing, dirty: false }, [first, latest]).reportId, "r1");
});
test("a successful explicit operation can open its new server version", () => {
  const editing = { ...empty, reportId: "r1", title: "修改标题", dirty: true };
  assert.deepEqual(reconcileReportDraft(editing, [first, latest], true), {
    reportId: "r2", title: "新报告", blocks: first.narrative_blocks, dirty: false,
  });
  assert.deepEqual(reconcileReportDraft(editing, []), editing);
});
