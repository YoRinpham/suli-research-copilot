import type { ResultInterpretation } from "./m5";

type Draft = Record<"summary" | "hypothesis_relationship" | "uncertainty" | "limitations" | "alternative_explanations", string>;
type Sources = Pick<ResultInterpretation, "statistical_result_ids" | "figure_artifact_ids">;

export function interpretationRevisionFields(source: Sources, draft: Draft) {
  const list = (value: string) => value.split(/\r?\n/).map(item => item.trim()).filter(Boolean);
  return { summary: draft.summary, hypothesis_relationship: draft.hypothesis_relationship,
    uncertainty: draft.uncertainty, limitations: list(draft.limitations),
    alternative_explanations: list(draft.alternative_explanations),
    statistical_result_ids: source.statistical_result_ids, figure_artifact_ids: source.figure_artifact_ids };
}
