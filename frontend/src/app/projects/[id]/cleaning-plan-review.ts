import { createElement } from "react";

type ReviewStep = { name: string; detail: string | null };

const isRecord = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);

const hasExactKeys = (value: Record<string, unknown>, keys: string[]) =>
  Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));

const isColumn = (value: unknown): value is string =>
  typeof value === "string" && value.trim().length > 0;

const isColumns = (value: unknown): value is string[] =>
  Array.isArray(value) && value.length > 0 && value.every(isColumn) &&
  new Set(value).size === value.length;

const isTokens = (value: unknown): value is string[] =>
  Array.isArray(value) && value.length > 0 && value.every(token => typeof token === "string");

function reviewStep(value: unknown): ReviewStep {
  const name = isRecord(value) && typeof value.op === "string" ? value.op : "未知操作";
  if (!isRecord(value)) return { name, detail: null };
  switch (value.op) {
    case "drop_exact_duplicates":
      return { name, detail: hasExactKeys(value, ["op"])
        ? "所有列的值完全相同：保留首次出现的行，删除后续重复行。" : null };
    case "trim_whitespace":
      return { name, detail: hasExactKeys(value, ["op", "columns"]) && isColumns(value.columns)
        ? `列：${value.columns.join("、")}；去除首尾空白；只含空白的值变为缺失。本步骤不删除行。` : null };
    case "normalize_missing":
      return { name, detail: hasExactKeys(value, ["op", "columns", "tokens"]) &&
        isColumns(value.columns) && isTokens(value.tokens)
        ? `列：${value.columns.join("、")}；将精确匹配的标记 ${value.tokens.map(token => JSON.stringify(token)).join("、")} 视为缺失。本步骤不删除行。` : null };
    case "drop_missing":
      return { name, detail: hasExactKeys(value, ["op", "columns"]) && isColumns(value.columns)
        ? `列：${value.columns.join("、")}；任一指定列为缺失时删除该行。` : null };
    case "cast_numeric":
      return { name, detail: hasExactKeys(value, ["op", "column"]) && isColumn(value.column)
        ? `列：${value.column}；转换为有限数值；有无法转换的值时整个清洗失败。` : null };
    case "cast_categorical":
      return { name, detail: hasExactKeys(value, ["op", "column"]) && isColumn(value.column)
        ? `列：${value.column}；将非缺失值转换为分类文本。` : null };
    default:
      return { name, detail: null };
  }
}

function reviewOperations(operations: unknown): { reviewable: boolean; steps: ReviewStep[] } {
  if (!Array.isArray(operations)) return { reviewable: false, steps: [] };
  const steps = operations.map(reviewStep);
  return { reviewable: operations.length <= 20 && steps.every(step => step.detail !== null), steps };
}

function operationList(steps: ReviewStep[]) {
  return createElement("ol", null, steps.map((step, index) => createElement("li", { key: index },
    `${step.name} · ${step.detail ?? "当前界面无法审核此操作或参数。"}`)));
}

export function CleaningPlanReview({ operations, status, busy, executed, onApprove, onReject, onExecute }: {
  operations: unknown; status: "pending_review" | "accepted" | "rejected";
  busy: boolean; executed: boolean; onApprove: () => void; onReject: () => void; onExecute: () => void;
}) {
  const review = reviewOperations(operations);
  return createElement("div", null,
    operationList(review.steps),
    !review.reviewable && createElement("p", { className: "alert", role: "alert" },
      "此清洗方案包含当前界面无法审核的操作或参数，无法批准或执行。请刷新或更新客户端后重试。"),
    status === "pending_review" && createElement("div", { className: "source-card-actions" },
      createElement("button", { className: "small-button", disabled: busy || !review.reviewable,
        onClick: () => { if (review.reviewable) onApprove(); } }, "人工批准"),
      createElement("button", { className: "small-button", disabled: busy, onClick: onReject }, "拒绝")),
    status === "accepted" && createElement("button", { className: "small-button",
      disabled: busy || executed || !review.reviewable,
      onClick: () => { if (review.reviewable) onExecute(); } }, "执行确定性清洗"));
}
