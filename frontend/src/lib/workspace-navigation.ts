export const WORKSPACE_STAGES = [
  { id: "literature", label: "文献", description: "从原始文献出发，为研究留下可核查的依据。", chapters: [["papers-title", "论文原文"], ["retrieval-title", "检索片段"], ["evidence-title", "已保存证据"]] },
  { id: "landscape", label: "研究全景", description: "比较证据与主张，理解共识、分歧和未知。", chapters: [["claims-title", "科学主张"], ["landscape-title", "研究全景"]] },
  { id: "directions", label: "研究方向", description: "沿着已有证据，提出值得继续追问的问题。", chapters: [["gaps-title", "研究空白"], ["hypotheses-title", "可检验假设"]] },
  { id: "experiment", label: "实验", description: "让假设成为可检验的计划，让解释回到真实数据。", chapters: [["plans-title", "实验计划"], ["m5-title", "数据与分析"], ["data-cleaning", "清洗审核"], ["data-analysis", "统计分析"], ["data-figures", "图表"], ["data-interpretation", "结果解释"]] },
  { id: "report", label: "报告", description: "将研究过程整理成一份来源清晰、经过审核的手稿。", chapters: [["report-title", "研究报告"], ["report-citations", "文献来源"], ["report-benchmark", "效率基准"]] },
] as const;

export type WorkspaceStage = typeof WORKSPACE_STAGES[number]["id"];

export function stageForAnchor(anchor: string): WorkspaceStage | null {
  for (const stage of WORKSPACE_STAGES) {
    if (stage.chapters.some(([id]) => id === anchor)) return stage.id;
  }
  if (anchor.startsWith("evidence-")) return "literature";
  if (anchor.startsWith("claim-") || anchor.startsWith("landscape-")) return "landscape";
  if (anchor.startsWith("gap-") || anchor.startsWith("hypothesis-")) return "directions";
  if (anchor.startsWith("plan-") || anchor.startsWith("data-")) return "experiment";
  if (anchor.startsWith("report-")) return "report";
  return null;
}

export function resolveWorkspaceLocation(search: string, hash: string): { stage: WorkspaceStage; anchor: string | null } {
  const requested = new URLSearchParams(search).get("stage");
  const stage = WORKSPACE_STAGES.find(item => item.id === requested)?.id ?? "literature";
  let anchor: string | null = null;
  try { anchor = hash ? decodeURIComponent(hash.replace(/^#/, "")) : null; } catch { /* Invalid external hash: show the requested stage. */ }
  return { stage: anchor && stageForAnchor(anchor) || stage, anchor };
}

export function workspaceHref(current: string, stage: WorkspaceStage, anchor?: string): string {
  const url = new URL(current, "http://workspace.local");
  url.searchParams.set("stage", stage);
  url.hash = anchor ? encodeURIComponent(anchor) : "";
  return `${url.pathname}${url.search}${url.hash}`;
}
