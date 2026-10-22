import type { ReportBlock, ResearchReport } from "./api/m6.ts";

export type ReportDraft = { reportId: string; title: string; blocks: ReportBlock[]; dirty: boolean };

export function reconcileReportDraft(current: ReportDraft,
  reports: Pick<ResearchReport, "id" | "title" | "narrative_blocks">[], replace = false): ReportDraft {
  if (current.dirty && !replace) return current;
  const chosen = (!replace && reports.find(item => item.id === current.reportId)) || reports.at(-1);
  return chosen ? { reportId: chosen.id, title: chosen.title, blocks: chosen.narrative_blocks, dirty: false } : current;
}
