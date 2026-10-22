"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getPaperFileUrl } from "../../../lib/api/papers";
import { figureFileUrl, listInterpretations, type ResultInterpretation } from "../../../lib/api/m5";
import { generateReport, listReports, reportExportUrl, reviewReport, reviseReport,
  getBenchmark, previewReport, type BenchmarkSummary, type ReportBlock, type ResearchReport } from "../../../lib/api/m6";
import { runReportAction } from "../../../lib/api/report-action";
import { InspectSources, useManuscript } from "../../../components/manuscript";
import { reconcileReportDraft, type ReportDraft } from "../../../lib/report-draft";
import { withRefreshFence } from "../../../lib/refresh-fence";
import { RecordedValues } from "../../../components/recorded-values";

type Props = { projectId: string; revision: number };
const labels: Record<ReportBlock["section"], string> = {
  executive_summary: "执行摘要", evidence_landscape: "证据全景叙述", limitations: "局限性综合", conclusion: "结论措辞",
};

export default function M6Workspace({ projectId, revision }: Props) {
  const { stage } = useManuscript();
  const refreshSequence = useRef(0);
  const [interpretations, setInterpretations] = useState<ResultInterpretation[]>([]);
  const [reports, setReports] = useState<ResearchReport[]>([]);
  const [draft, setDraft] = useState<ReportDraft>({ reportId: "", title: "", blocks: [], dirty: false });
  const { reportId, title, blocks } = draft;
  const [selected, setSelected] = useState("");
  const [busy, setBusy] = useState(false);
  const actionGate = useRef({ active: false });
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [benchmark, setBenchmark] = useState<BenchmarkSummary | null>(null);
  const [preview, setPreview] = useState("");
  const refresh = useCallback(async (replaceDraft = false) => {
    const sequence = ++refreshSequence.current;
    const [loadedInterpretations, loadedReports] = await Promise.all([
      listInterpretations(projectId), listReports(projectId)]);
    if (sequence !== refreshSequence.current) return;
    setInterpretations(loadedInterpretations);
    setReports(loadedReports);
    setDraft(current => reconcileReportDraft(current, loadedReports, replaceDraft));
  }, [projectId]);
  useEffect(() => { if (stage === "report") void refresh().catch(cause => setError(cause instanceof Error ? cause.message : "无法加载报告")); }, [refresh, stage]);
  useEffect(() => { void getBenchmark().then(setBenchmark).catch(() => setBenchmark(null)); }, []);

  async function act(action: () => Promise<unknown>, success: string, replaceDraft = false) {
    if (actionGate.current.active) return;
    setError(""); setNotice("");
    let succeeded = false;
    await runReportAction(actionGate.current, async () => { const result = await withRefreshFence(refreshSequence, action); succeeded = true; return result; }, () => refresh(succeeded && replaceDraft), setBusy,
      () => setNotice(success), setError);
  }

  const active = reports.find(item => item.id === reportId) || reports.at(-1);
  const eligible = interpretations.filter(item => item.eligible_for_future_report);
  const selectedIsEligible = eligible.some(item => item.id === selected);
  return <section className="workspace-section" aria-labelledby="report-title">
    <div className="section-heading"><h2 id="report-title">研究报告</h2><span>保留每一步的证据与判断</span></div>
    <p className="muted">仅使用当前已接受的解释和已审核科学对象。Qwen 叙述始终先作为草稿，人工接受后才能最终导出。</p>
    <label className="m6-choice">当前已接受的结果解释 <select value={selected} onChange={event => setSelected(event.target.value)}>
      <option value="">请选择</option>{eligible.map(item => <option key={item.id} value={item.id}>{item.summary.slice(0, 80)}</option>)}
    </select></label>
    <button className="primary-button" disabled={busy || !selectedIsEligible} onClick={() => void act(
      () => generateReport(projectId, selected, revision), "报告草稿已生成，须人工检查。", true)}>{busy ? "处理中…" : "生成报告草稿"}</button>
    <button className="small-button" disabled={busy || !selectedIsEligible} onClick={() => void act(async () => {
      const result = await previewReport(projectId, selected, revision); setPreview(result.markdown);
    }, "已生成确定性预览；无需 Qwen。")}>预览确定性内容</button>
    {preview && <details><summary>确定性报告预览（草稿）</summary><pre style={{ whiteSpace: "pre-wrap" }}>{preview}</pre></details>}
    {!eligible.length && <p className="empty">先完成并人工接受当前结果解释；已有报告仍可阅读。</p>}
    {error && <p role="alert" className="error">报告操作失败：{error}。可核查状态后重试。</p>}
    {notice && <p role="status">{notice}</p>}
    {reports.length > 0 && <label className="m6-choice">报告历史版本 <select disabled={busy} value={active?.id || ""} onChange={event => {
      const chosen = reports.find(item => item.id === event.target.value);
      if (chosen) setDraft({ reportId: chosen.id, title: chosen.title, blocks: chosen.narrative_blocks, dirty: false });
    }}>{reports.map(item => <option key={item.id} value={item.id}>{item.title} · {item.review_status} · {item.id.slice(0, 8)}</option>)}</select></label>}
    {active && <article className="source-card m6-report" id={`report-${active.id}`}>
      <div className="source-card-meta"><strong>{active.title}</strong><span>{active.review_status === "accepted" ? "人工已接受" : active.review_status === "rejected" ? "已拒绝" : "待人工审核 / 草稿"}</span><span>{active.current_source_bundle ? "来源仍为当前版本" : "来源已过期"}</span></div>
      <InspectSources label="核查报告文献来源" note={{ title: "报告的文献来源", explanation: "以下原文来自这份报告保存的来源快照。", excerpts: active.source_bundle.citations.map(citation => ({ id: citation.token, title: citation.paper_title || citation.source_filename, paperId: citation.paper_id, page: citation.page_number, text: citation.source_text, role: citation.token })) }} />
      <details className="assessment-details"><summary>来源快照与版本信息</summary><p className="muted">来源快照 SHA-256：{active.source_bundle_hash}</p><p className="muted">研究问题修订 {active.question_revision} · {active.origin === "model" ? "Qwen 草稿" : "人工修订"}</p></details>
      <h3>研究问题</h3><p>{active.source_bundle.research_question}</p>
      <h3>已选研究空白（仅限所提供文献）</h3><p>{active.source_bundle.gap.title}：{active.source_bundle.gap.description}</p>
      <h3>可证伪假设</h3><p>{active.source_bundle.hypothesis.statement}</p>
      <p>证伪条件：{active.source_bundle.hypothesis.falsification_condition}</p>
      <h3>确定性统计结果</h3>{active.source_bundle.results.map(result =>
        <div key={result.id}><RecordedValues values={result.values} /><details className="assessment-details"><summary>计算记录</summary><p className="muted">{result.method} · {result.id}</p></details></div>)}
      <h3>真实图表</h3>{active.source_bundle.figures.map(figure => <figure key={figure.id}>
        {/* Figure bytes are served by the existing project-scoped M5 endpoint. */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={figureFileUrl(projectId, figure.id)} alt={figure.title} style={{ maxWidth: "100%" }} />
        <figcaption>{figure.title} · FigureArtifact {figure.id}</figcaption></figure>)}
      <h3>人工接受的结果解释</h3><p>{active.source_bundle.interpretation.summary}</p>
      <h3>Qwen 草稿叙述</h3>{active.narrative_blocks.map((block, index) => <div key={index}>
        <strong>{labels[block.section]}</strong><p>{block.text}</p>
        <p className="muted">来源：{block.source_refs.map(token => {
          const source = active.source_bundle.source_map[token];
          return source ? `${token}（${source.type} ${source.id}）` : token;
        }).join("、")}</p></div>)}
      <details className="m6-citations" id="report-citations"><summary>文献证据与原始页（{active.source_bundle.citations.length}）</summary>{active.source_bundle.citations.map(citation =>
        <div key={citation.token}><strong>{citation.token} · {citation.paper_title || citation.source_filename}</strong>
          <p>{citation.source_text}</p><a href={`${getPaperFileUrl(projectId, citation.paper_id)}#page=${citation.page_number}`} target="_blank" rel="noopener noreferrer">查看 PDF 物理第 {citation.page_number} 页</a></div>)}</details>
      {active.review_status === "pending_review" && active.current_source_bundle && <div className="source-card-actions">
        <button className="small-button" disabled={busy} onClick={() => void act(() => reviewReport(projectId, active.id, "accepted", revision), "已人工接受报告。")}>人工接受</button>
        <button className="small-button" disabled={busy} onClick={() => void act(() => reviewReport(projectId, active.id, "rejected", revision), "已拒绝报告。")}>拒绝</button>
      </div>}
      {active.current_source_bundle && <div className="revision-form m6-revision"><h3>创建人工修订</h3>
        <label>标题 <input disabled={busy} value={title} onChange={event => setDraft(current => ({ ...current, title: event.target.value, dirty: true }))} /></label>
        {blocks.map((block, index) => <label key={index}>{labels[block.section]}
          <textarea disabled={busy} value={block.text} onChange={event => setDraft(current => ({ ...current, dirty: true, blocks: current.blocks.map((item, at) => at === index ? { ...item, text: event.target.value } : item) }))} />
        </label>)}
        <p className="muted">修订会生成新版本并重新等待审核；来源引用保留。数值和确定性章节不能在此改写。</p>
        <button className="small-button" disabled={busy || !title.trim() || blocks.length === 0} onClick={() => void act(
          () => reviseReport(projectId, active.id, revision, title, blocks), "已创建待审核的人工修订。", true)}>保存新修订</button>
      </div>}
      <div className="source-card-actions"><a href={reportExportUrl(projectId, active.id, "markdown", !active.final_export_eligible)} download>导出 Markdown{active.final_export_eligible ? " 最终版" : " 草稿"}</a>
        <a href={reportExportUrl(projectId, active.id, "html", !active.final_export_eligible)} target="_blank" rel="noopener noreferrer">查看/打印 HTML{active.final_export_eligible ? " 最终版" : " 草稿"}</a></div>
    </article>}
    <h3 id="report-benchmark">效率基准</h3>
    <p>场景：{benchmark?.manual?.scenario_id || benchmark?.research_copilot?.scenario_id || "尚未记录"}</p>
    <p>人工基线：{benchmark?.manual ? benchmark.manual.complete ? `${benchmark.manual.duration_seconds} 秒，${benchmark.manual.human_decisions} 次科学决定（完整）` :
      `未完成；缺少阶段：${benchmark.manual.missing_stages.join("、") || "无"}；无效阶段：${benchmark.manual.invalid_stages.join("、") || "无"}` : "尚未测量"}</p>
    <p>溯理 SULI：{benchmark?.research_copilot ? benchmark.research_copilot.complete ? `${benchmark.research_copilot.duration_seconds} 秒，${benchmark.research_copilot.human_decisions} 次科学决定（完整）` :
      `未完成；缺少阶段：${benchmark.research_copilot.missing_stages.join("、") || "无"}；无效阶段：${benchmark.research_copilot.invalid_stages.join("、") || "无"}` : "尚未测量"}</p>
    {benchmark?.comparison === "MEASURED" && <p>实测节省 {benchmark.time_saved_seconds} 秒；时间变化 {benchmark.percentage_reduction?.toFixed(1)}%。</p>}
    <p className="muted">仅同场景、同输入哈希、同协议且全部完成的两次计时允许比较。基准协议见文档。</p>
  </section>;
}
