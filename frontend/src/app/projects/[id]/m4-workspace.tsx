"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { EvidenceReferences, InspectSources, useManuscript } from "../../../components/manuscript";
import type { Evidence } from "../../../lib/api/evidence";
import type { Claim } from "../../../lib/api/claims";
import type { Landscape } from "../../../lib/api/landscapes";
import { generateGaps, listGaps, reviseGap, selectGap, type Gap, type M4Assessment } from "../../../lib/api/gaps";
import { generateHypotheses, listHypotheses, reviseHypothesis, selectHypothesis, type Hypothesis } from "../../../lib/api/hypotheses";
import { generatePlan, listPlans, reviewPlan, revisePlan, type ExperimentPlan } from "../../../lib/api/experiment-plans";
import { ApiError } from "../../../lib/api/client";
import { withRefreshFence } from "../../../lib/refresh-fence";
import { GAP_REVISION_FIELDS, HYPOTHESIS_REVISION_FIELDS, PLAN_REVISION_FIELDS, REVISION_LIST_FIELDS, revisionDraft, gapRevisionPayload, hypothesisRevisionPayload, planRevisionPayload } from "../../../lib/api/m4-revision";

type Props = { projectId: string; revision: number; landscapes: Landscape[]; claims: Claim[]; evidence: Evidence[] };
const fields: Record<string, string> = { title: "标题", description: "空白陈述", why_it_matters: "研究价值", uncertainty: "不确定性", statement: "假设陈述", rationale: "理由", independent_variable: "比较因素", dependent_variable: "测量结果", falsification_condition: "证伪条件", objective: "实验目标", procedure: "实验过程", analysis_approach: "分析方法", falsification_criteria: "证伪标准", assumptions: "前提假设", control_variables: "控制条件", experimental_groups: "实验组", baseline: "基线或比较参照", dataset_requirements: "数据要求", metrics: "指标", expected_observations: "预期观察", confounders: "混杂因素", limitations: "局限" };

function Assessment({ value }: { value: M4Assessment | null }) {
  return value ? <details className="assessment-details"><summary>AI 参考评估：{value.verdict}{value.risk_flags.length > 0 ? ` · ${value.risk_flags.length} 项风险` : ""}</summary><p>{value.justification}</p>{value.risk_flags.length > 0 && <p className="rejected">风险：{value.risk_flags.join("、")}</p>}</details> : <p className="muted">人工修订版本尚无 AI 评估。</p>;
}

export default function M4Workspace({ projectId, revision, landscapes, claims, evidence }: Props) {
  const { stage } = useManuscript();
  const refreshSequence = useRef(0);
  const [gaps, setGaps] = useState<Gap[]>([]);
  const [hypotheses, setHypotheses] = useState<Hypothesis[]>([]);
  const [plans, setPlans] = useState<ExperimentPlan[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const refresh = useCallback(async () => {
    const sequence = ++refreshSequence.current;
    const [nextGaps, nextHypotheses, nextPlans] = await Promise.all([listGaps(projectId), listHypotheses(projectId), listPlans(projectId)]);
    if (sequence !== refreshSequence.current) return;
    setGaps(nextGaps); setHypotheses(nextHypotheses); setPlans(nextPlans);
  }, [projectId]);
  useEffect(() => {
    if (stage === "directions" || stage === "experiment") void refresh().catch((cause) => setError(cause instanceof Error ? cause.message : "无法读取研究方向与实验计划"));
  }, [refresh, stage]);

  async function act(id: string, task: () => Promise<unknown>, message: string) {
    setBusy(id); setError(""); setNotice("");
    try { await withRefreshFence(refreshSequence, task); await refresh(); setNotice(message); setEditing(null); }
    catch (cause) {
      setError(cause instanceof ApiError && (cause.code === "invalid_state" || cause.code === "superseded_resource")
        ? "上游状态或版本已改变，请重新加载项目页面以更新研究问题和全景。" : cause instanceof Error ? cause.message : "操作失败");
    } finally { setBusy(null); }
  }
  function sources(ids: string[]) {
    return <EvidenceReferences ids={ids} evidence={evidence} />;
  }
  function begin(id: string, values: Record<string, string>) { setEditing(id); setDraft(values); }
  function editForm(id: string, names: readonly string[], save: () => Promise<unknown>, message: string) {
    const required = (name: string) => !REVISION_LIST_FIELDS.has(name) || name === "experimental_groups" || name === "metrics";
    return editing === id && <div className="revision-form">{names.map((name) => <label key={name}>{fields[name]}{REVISION_LIST_FIELDS.has(name) && <span className="muted">（每行一项；{required(name) ? "至少一项" : "无条目可留空"}）</span>}<textarea disabled={busy !== null} rows={REVISION_LIST_FIELDS.has(name) ? 3 : 2} value={draft[name] ?? ""} onChange={(event) => setDraft({ ...draft, [name]: event.target.value })} /></label>)}<p className="muted">来源关联和空白类别保持不变；请核对全部可编辑内容，新版本需要重新选择或审核。</p><button className="small-button" disabled={busy !== null || names.some((name) => required(name) && !draft[name]?.trim())} onClick={() => void act(id, save, message)}>保存新版本</button><button className="small-button" onClick={() => setEditing(null)}>取消</button></div>;
  }
  const gapSuperseded = (id: string) => gaps.some((item) => item.supersedes_id === id);
  const hypothesisSuperseded = (id: string) => hypotheses.some((item) => item.supersedes_id === id);
  const planSuperseded = (id: string) => plans.some((item) => item.supersedes_id === id);
  return <>
    {error && <p className="alert" role="alert">{error} <button onClick={() => void refresh().catch(cause => setError(cause instanceof Error ? cause.message : "刷新失败"))}>刷新研究输出</button> <button onClick={() => window.location.reload()}>重新加载项目</button></p>}
    {notice && <p className="notice" role="status">{notice}</p>}
    <section className="workspace-section m2-section" aria-labelledby="gaps-title" hidden={stage !== "directions"}>
      <div className="section-heading"><h2 id="gaps-title">候选研究空白</h2><span>已接受全景 → 人工选择</span></div>
      <p className="muted">空白仅针对已提供的文献。人工选择表示要研究这个方向，不表示它已获全球新颖性证明。</p>
      {landscapes.filter((item) => item.eligible_for_downstream).map((item) => <div className="source-card-actions" key={item.id}><span>已接受全景：{item.summary}</span><button className="small-button" disabled={busy !== null} onClick={() => void act(item.id, () => generateGaps(projectId, item.id, revision), "已生成候选空白，请核查来源并人工选择。")}>{busy === item.id ? "生成中…" : "根据此全景生成空白"}</button></div>)}
      {gaps.length === 0 && <p className="empty">先接受一份研究全景，再生成有来源的候选空白。</p>}
      <div className="result-list">{gaps.map((gap) => <article className="source-card" id={`gap-${gap.id}`} key={gap.id}>
        <div className="source-card-meta"><strong>{gap.title}</strong><span>范围：已提供文献</span><span>{gap.selection_status === "selected" ? "人工已选择" : gap.selection_status === "rejected" ? "已拒绝" : "候选"}</span><span>{gap.origin === "model" ? "Qwen 提议" : "人工修订"}</span></div>
        <InspectSources note={{ title: gap.title, claimIds: gap.claim_ids, evidenceRefs: [...gap.evidence_for_ids.map(id => ({ id, role: "支持证据" })), ...gap.evidence_against_ids.map(id => ({ id, role: "质疑证据" }))] }} />
        <p className="scientific-text">{gap.description}</p><p>类型：{gap.gap_type}</p><p>研究价值：{gap.why_it_matters}</p><p>前提：{gap.assumptions.join("、") || "未列明"}</p><p className="muted">不确定性：{gap.uncertainty}</p><Assessment value={gap.ai_assessment} />
        <div className="source-groups"><div><strong>全景条目</strong><div className="source-links">{gap.landscape_item_ids.map((id) => <a key={id} href={`#landscape-item-${id}`}>{landscapes.flatMap((entry) => entry.items).find((entry) => entry.id === id)?.text ?? id}</a>)}</div><strong>科学主张</strong><div className="source-links">{gap.claim_ids.map((id) => <a key={id} href={`#claim-${id}`}>{claims.find((entry) => entry.id === id)?.text ?? id}</a>)}</div></div><div><strong>支持证据</strong><div className="source-links">{sources(gap.evidence_for_ids)}</div><strong>质疑证据</strong><div className="source-links">{sources(gap.evidence_against_ids)}</div></div></div>
        <div className="source-card-actions"><button className="small-button" disabled={busy !== null || gap.question_revision !== revision || gapSuperseded(gap.id)} onClick={() => void act(gap.id, () => selectGap(projectId, gap.id, "selected", revision), "研究方向已选择，下一步仍需人工选择假设。")}>选择空白</button><button className="small-button" disabled={busy !== null || gap.question_revision !== revision || gapSuperseded(gap.id)} onClick={() => void act(gap.id, () => selectGap(projectId, gap.id, "rejected", revision), "候选空白已拒绝。")}>拒绝</button><button className="small-button" disabled={busy !== null || gap.question_revision !== revision || gapSuperseded(gap.id)} onClick={() => begin(gap.id, revisionDraft(gap, GAP_REVISION_FIELDS))}>创建修订</button></div>
        {editForm(gap.id, GAP_REVISION_FIELDS, () => reviseGap(projectId, gap.id, gapRevisionPayload(gap, draft, revision)), "研究空白新版本已创建，需重新选择。")}
        {gap.eligible_for_generation && <button className="primary-button" disabled={busy !== null} onClick={() => void act(`hyp-${gap.id}`, () => generateHypotheses(projectId, gap.id, 3, revision), "已生成候选假设，请检查可证伪性后人工选择。")}>{busy === `hyp-${gap.id}` ? "生成中…" : "基于此空白生成假设"}</button>}
      </article>)}</div>
    </section>
    <section className="workspace-section m2-section" aria-labelledby="hypotheses-title" hidden={stage !== "directions"}>
      <div className="section-heading"><h2 id="hypotheses-title">可检验假设</h2><span>已选择空白 → 人工选择</span></div><p className="muted">假设是待检验的预测。来源证据可以启发或质疑它；AI 评估仅供参考。</p>
      {hypotheses.length === 0 && <p className="empty">选择当前研究空白后，显式生成假设。</p>}
      <div className="result-list">{hypotheses.map((hypothesis) => <article className="source-card" id={`hypothesis-${hypothesis.id}`} key={hypothesis.id}>
        <div className="source-card-meta"><strong>假设</strong><span>{hypothesis.selection_status === "selected" ? "人工已选择" : hypothesis.selection_status === "rejected" ? "已拒绝" : "候选"}</span><span>{hypothesis.origin === "model" ? "Qwen 提议" : "人工修订"}</span></div>
        <InspectSources note={{ title: "假设的文献依据", explanation: "启发证据说明提出假设的依据，不表示假设已经得到证实。", evidenceRefs: [...hypothesis.evidence_motivating_ids.map(id => ({ id, role: "启发证据" })), ...hypothesis.evidence_challenging_ids.map(id => ({ id, role: "质疑证据" }))] }} />
        <p className="scientific-text">{hypothesis.statement}</p><p>理由：{hypothesis.rationale}</p><p>前提：{hypothesis.assumptions.join("、") || "未列明"}</p><p>证伪条件：{hypothesis.falsification_condition}</p><p className="muted">比较因素：{hypothesis.independent_variable}；结果量：{hypothesis.dependent_variable}；控制条件：{hypothesis.control_variables.join("、") || "待确认"}</p><p className="muted">不确定性：{hypothesis.uncertainty}</p><p>来源空白：<a href={`#gap-${hypothesis.research_gap_id}`}>{gaps.find((item) => item.id === hypothesis.research_gap_id)?.title ?? hypothesis.research_gap_id}</a></p><Assessment value={hypothesis.ai_assessment} />
        <div className="source-groups"><div><strong>启发证据</strong><div className="source-links">{sources(hypothesis.evidence_motivating_ids)}</div></div><div><strong>质疑证据</strong><div className="source-links">{sources(hypothesis.evidence_challenging_ids)}</div></div></div>
        <div className="source-card-actions"><button className="small-button" disabled={busy !== null || hypothesis.question_revision !== revision || hypothesisSuperseded(hypothesis.id)} onClick={() => void act(hypothesis.id, () => selectHypothesis(projectId, hypothesis.id, "selected", revision), "待检验假设已选择；这不表示它为真。")}>选择假设</button><button className="small-button" disabled={busy !== null || hypothesis.question_revision !== revision || hypothesisSuperseded(hypothesis.id)} onClick={() => void act(hypothesis.id, () => selectHypothesis(projectId, hypothesis.id, "rejected", revision), "候选假设已拒绝。")}>拒绝</button><button className="small-button" disabled={busy !== null || hypothesis.question_revision !== revision || hypothesisSuperseded(hypothesis.id)} onClick={() => begin(hypothesis.id, revisionDraft(hypothesis, HYPOTHESIS_REVISION_FIELDS))}>创建修订</button></div>
        {editForm(hypothesis.id, HYPOTHESIS_REVISION_FIELDS, () => reviseHypothesis(projectId, hypothesis.id, hypothesisRevisionPayload(hypothesis, draft, revision)), "假设新版本已创建，需重新选择。")}
        {hypothesis.eligible_for_generation && <button className="primary-button" disabled={busy !== null} onClick={() => void act(`plan-${hypothesis.id}`, () => generatePlan(projectId, hypothesis.id, revision), "实验计划已生成，等待人工审核。")}>{busy === `plan-${hypothesis.id}` ? "生成中…" : "为此假设生成实验计划"}</button>}
      </article>)}</div>
    </section>
    <section className="workspace-section m2-section" aria-labelledby="plans-title" hidden={stage !== "experiment"}>
      <div className="section-heading"><h2 id="plans-title">实验计划</h2><span>已选择假设 → 人工接受</span></div><p className="muted">计划描述未来的研究设计，没有实验结果。AI 对齐评估不代表可行性或科学正确性。</p>
      {plans.length === 0 && <p className="empty">选择一条当前假设后，显式生成实验计划。</p>}
      <div className="result-list">{plans.map((plan) => <article className="source-card" id={`plan-${plan.id}`} key={plan.id}>
        <div className="source-card-meta"><strong>{plan.objective}</strong><span>人工审核：{plan.review_status === "accepted" ? "已接受" : plan.review_status === "rejected" ? "已拒绝" : "待审核"}</span><span>{plan.origin === "model" ? "Qwen 提议" : "人工修订"}</span></div>
        <p>检验假设：<a href={`#hypothesis-${plan.hypothesis_id}`}>{hypotheses.find((item) => item.id === plan.hypothesis_id)?.statement ?? plan.hypothesis_id}</a></p>
        <div className="plan-grid">{(["independent_variable", "dependent_variable", "baseline", "dataset_requirements", "procedure", "analysis_approach", "falsification_criteria"] as const).map((name) => <p key={name}><strong>{fields[name] ?? (name === "baseline" ? "基线" : "数据条件")}</strong><br />{plan[name]}</p>)}<p><strong>控制条件</strong><br />{plan.control_variables.join("、") || "待确认"}</p><p><strong>预期观察</strong><br />{plan.expected_observations.join("、") || "待确认"}</p><p><strong>实验组</strong><br />{plan.experimental_groups.join("、")}</p><p><strong>指标</strong><br />{plan.metrics.join("、")}</p><p><strong>混杂因素</strong><br />{plan.confounders.join("、") || "待确认"}</p><p><strong>局限</strong><br />{plan.limitations.join("、") || "未列明"}</p><p><strong>假设条件</strong><br />{plan.assumptions.join("、") || "未列明"}</p></div>
        <p className="muted">不确定性：{plan.uncertainty}</p><Assessment value={plan.ai_alignment_assessment} />
        <div className="source-card-actions"><button className="small-button" disabled={busy !== null || plan.question_revision !== revision || planSuperseded(plan.id)} onClick={() => void act(plan.id, () => reviewPlan(projectId, plan.id, "accepted", revision), "计划已人工接受，但尚未执行实验。")}>人工接受计划</button><button className="small-button" disabled={busy !== null || plan.question_revision !== revision || planSuperseded(plan.id)} onClick={() => void act(plan.id, () => reviewPlan(projectId, plan.id, "rejected", revision), "计划已拒绝。")}>拒绝</button><button className="small-button" disabled={busy !== null || plan.question_revision !== revision || planSuperseded(plan.id)} onClick={() => begin(plan.id, revisionDraft(plan, PLAN_REVISION_FIELDS))}>创建修订</button></div>
        {editForm(plan.id, PLAN_REVISION_FIELDS, () => revisePlan(projectId, plan.id, planRevisionPayload(draft, revision)), "计划新版本已创建，需重新人工审核。")}
      </article>)}</div>
    </section>
  </>;
}
