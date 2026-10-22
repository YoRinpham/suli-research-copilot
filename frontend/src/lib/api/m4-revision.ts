import type { Gap, GapRevision } from "./gaps.ts";
import type { Hypothesis, HypothesisRevision } from "./hypotheses.ts";
import type { PlanRevision } from "./experiment-plans.ts";

export const GAP_REVISION_FIELDS = ["title", "description", "why_it_matters", "assumptions", "uncertainty"] as const;
export const HYPOTHESIS_REVISION_FIELDS = ["statement", "rationale", "independent_variable", "dependent_variable", "control_variables", "falsification_condition", "assumptions", "uncertainty"] as const;
export const PLAN_REVISION_FIELDS = ["objective", "independent_variable", "dependent_variable", "control_variables", "experimental_groups", "baseline", "dataset_requirements", "metrics", "procedure", "analysis_approach", "expected_observations", "falsification_criteria", "confounders", "limitations", "assumptions", "uncertainty"] as const;
export const REVISION_LIST_FIELDS = new Set(["assumptions", "control_variables", "experimental_groups", "metrics", "expected_observations", "confounders", "limitations"]);

export function revisionDraft(values: Record<string, unknown>, names: readonly string[] = Object.keys(values)): Record<string, string> {
  return Object.fromEntries(names.map(name => {
    const value = values[name];
    if (typeof value === "string") return [name, value];
    if (Array.isArray(value) && value.every(entry => typeof entry === "string")) return [name, value.join("\n")];
    throw new Error(`无法编辑字段：${name}`);
  }));
}

function list(value: string | undefined): string[] {
  return (value ?? "").split(/\r?\n/).map(entry => entry.trim()).filter(Boolean);
}

export function gapRevisionPayload(source: Pick<Gap, "gap_type" | "novelty_scope" | "landscape_item_ids" | "claim_ids" | "evidence_for_ids" | "evidence_against_ids">, draft: Record<string, string>, revision: number): GapRevision {
  return { title: draft.title, description: draft.description, why_it_matters: draft.why_it_matters,
    uncertainty: draft.uncertainty, assumptions: list(draft.assumptions), gap_type: source.gap_type,
    novelty_scope: source.novelty_scope, landscape_item_ids: source.landscape_item_ids,
    claim_ids: source.claim_ids, evidence_for_ids: source.evidence_for_ids, evidence_against_ids: source.evidence_against_ids,
    expected_question_revision: revision };
}

export function hypothesisRevisionPayload(source: Pick<Hypothesis, "evidence_motivating_ids" | "evidence_challenging_ids" | "claim_ids">, draft: Record<string, string>, revision: number): HypothesisRevision {
  return { statement: draft.statement, rationale: draft.rationale, independent_variable: draft.independent_variable,
    dependent_variable: draft.dependent_variable, control_variables: list(draft.control_variables),
    falsification_condition: draft.falsification_condition, assumptions: list(draft.assumptions), uncertainty: draft.uncertainty,
    evidence_motivating_ids: source.evidence_motivating_ids, evidence_challenging_ids: source.evidence_challenging_ids,
    claim_ids: source.claim_ids, expected_question_revision: revision };
}

export function planRevisionPayload(draft: Record<string, string>, revision: number): PlanRevision {
  return { objective: draft.objective, independent_variable: draft.independent_variable, dependent_variable: draft.dependent_variable,
    control_variables: list(draft.control_variables), experimental_groups: list(draft.experimental_groups),
    baseline: draft.baseline, dataset_requirements: draft.dataset_requirements, metrics: list(draft.metrics),
    procedure: draft.procedure, analysis_approach: draft.analysis_approach, expected_observations: list(draft.expected_observations),
    falsification_criteria: draft.falsification_criteria, confounders: list(draft.confounders), limitations: list(draft.limitations),
    assumptions: list(draft.assumptions), uncertainty: draft.uncertainty, expected_question_revision: revision };
}
