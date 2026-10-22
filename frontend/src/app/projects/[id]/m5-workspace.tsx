"use client";

import { ChangeEvent, useCallback, useEffect, useRef, useState } from "react";
import { useManuscript } from "../../../components/manuscript";
import { withRefreshFence } from "../../../lib/refresh-fence";
import { RecordedValues } from "../../../components/recorded-values";
import { CleaningPlanReview } from "./cleaning-plan-review";
import { interpretationRevisionFields } from "../../../lib/api/m5-revision";
import { listPlans, type ExperimentPlan } from "../../../lib/api/experiment-plans";
import { createAnalysis, createCleaning, createFigure, deleteFigure, datasetFileUrl, executeAnalysis,
  executeCleaning, figureFileUrl, generateInterpretation, listAnalysisRuns, listAnalysisSpecs,
  listCleaning, listCleaningRuns, listDatasets, listFigures, listInterpretations, reviewAnalysis, reviewCleaning,
  reviewInterpretation, reviseInterpretation, suggestAnalysis, suggestCleaning, uploadDataset,
  type AnalysisRun, type AnalysisSpec, type CleaningOperation, type CleaningPlan, type CleaningRun, type Dataset,
  type FigureArtifact, type ResultInterpretation } from "../../../lib/api/m5";

type Props = { projectId: string; revision: number };
const methods = ["descriptive", "grouped_descriptive", "welch_t", "paired_t", "pearson", "spearman"] as const;
const methodLabels: Record<string, string> = { descriptive: "描述统计", grouped_descriptive: "分组描述统计",
  welch_t: "Welch 独立双组 t 检验", paired_t: "配对 t 检验", pearson: "Pearson 相关",
  spearman: "Spearman 相关" };
const reviewLabel = (value: string) => value === "accepted" ? "人工已接受" : value === "rejected" ? "人工已拒绝" : "待人工审核";
const commaList = (text: string) => text.split(",").map(value => value.trim()).filter(Boolean);

export default function M5Workspace({ projectId, revision }: Props) {
  const { stage } = useManuscript();
  const refreshSequence = useRef(0);
  const [plans, setPlans] = useState<ExperimentPlan[]>([]);
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [cleaning, setCleaning] = useState<CleaningPlan[]>([]);
  const [cleaningRuns, setCleaningRuns] = useState<CleaningRun[]>([]);
  const [specs, setSpecs] = useState<AnalysisSpec[]>([]);
  const [runs, setRuns] = useState<AnalysisRun[]>([]);
  const [figures, setFigures] = useState<FigureArtifact[]>([]);
  const [interpretations, setInterpretations] = useState<ResultInterpretation[]>([]);
  const [planId, setPlanId] = useState("");
  const [versionId, setVersionId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [cleanOp, setCleanOp] = useState<CleaningOperation["op"]>("drop_exact_duplicates");
  const [cleanColumns, setCleanColumns] = useState("");
  const [missingTokens, setMissingTokens] = useState("NA, N/A");
  const [cleanRationale, setCleanRationale] = useState("");
  const [method, setMethod] = useState<string>("descriptive");
  const [outcome, setOutcome] = useState("");
  const [group, setGroup] = useState("");
  const [predictor, setPredictor] = useState("");
  const [subject, setSubject] = useState("");
  const [comparisonGroups, setComparisonGroups] = useState("");
  const [analysisRationale, setAnalysisRationale] = useState("");
  const [chartType, setChartType] = useState<"histogram" | "box_plot" | "scatter" | "group_summary">("histogram");
  const [chartX, setChartX] = useState("");
  const [chartY, setChartY] = useState("");
  const [chartGroup, setChartGroup] = useState("");
  const [chartRun, setChartRun] = useState("");
  const [chartTitle, setChartTitle] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [revisedSummary, setRevisedSummary] = useState("");
  const [revisedRelationship, setRevisedRelationship] = useState("");
  const [revisedUncertainty, setRevisedUncertainty] = useState("");
  const [revisedLimitations, setRevisedLimitations] = useState("");
  const [revisedAlternatives, setRevisedAlternatives] = useState("");

  const refresh = useCallback(async () => {
    const sequence = ++refreshSequence.current;
    const [nextPlans, nextDatasets, nextCleaning, nextCleaningRuns, nextSpecs, nextRuns, nextFigures, nextInterpretations] = await Promise.all([
      listPlans(projectId), listDatasets(projectId), listCleaning(projectId), listCleaningRuns(projectId), listAnalysisSpecs(projectId),
      listAnalysisRuns(projectId), listFigures(projectId), listInterpretations(projectId),
    ]);
    if (sequence !== refreshSequence.current) return;
    setPlans(nextPlans); setDatasets(nextDatasets); setCleaning(nextCleaning); setCleaningRuns(nextCleaningRuns); setSpecs(nextSpecs);
    setRuns(nextRuns); setFigures(nextFigures); setInterpretations(nextInterpretations);
  }, [projectId]);
  useEffect(() => { if (stage === "experiment") void refresh().catch(cause => setError(cause instanceof Error ? cause.message : "读取实验数据失败")); }, [refresh, stage]);

  async function act(action: () => Promise<unknown>, message: string) {
    setBusy(true); setError(""); setNotice("");
    try { await withRefreshFence(refreshSequence, action); await refresh(); setNotice(message); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "操作失败，请刷新后重试"); }
    finally { setBusy(false); }
  }

  const currentPlans = plans.filter(plan => plan.eligible_for_downstream && plan.question_revision === revision);
  const selectedPlanId = currentPlans.some(plan => plan.id === planId) ? planId : currentPlans[0]?.id ?? "";
  const plan = currentPlans.find(item => item.id === selectedPlanId);
  const versions = datasets.filter(item => item.experiment_plan_id === selectedPlanId).flatMap(item => item.versions);
  const selectedVersionId = versions.some(item => item.id === versionId) ? versionId : versions.at(-1)?.id ?? "";
  const version = versions.find(item => item.id === selectedVersionId);
  const selectedDataset = datasets.find(item => item.versions.some(candidate => candidate.id === selectedVersionId));
  const relatedVersionIds = new Set(selectedDataset?.versions.map(item => item.id) ?? []);
  const columns = version?.profile.columns.map(item => item.name) ?? [];
  const versionSpecs = specs.filter(item => item.dataset_version_id === selectedVersionId);
  const versionRuns = runs.filter(item => item.dataset_version_id === selectedVersionId);
  const datasetRuns = runs.filter(item => relatedVersionIds.has(item.dataset_version_id));
  const datasetFigures = figures.filter(item => relatedVersionIds.has(item.dataset_version_id));

  function oneOperation(): CleaningOperation {
    if (cleanOp === "drop_exact_duplicates") return { op: cleanOp };
    const names = commaList(cleanColumns);
    if (cleanOp === "cast_numeric") return { op: cleanOp, column: names[0] };
    if (cleanOp === "cast_categorical") return { op: cleanOp, column: names[0] };
    if (cleanOp === "normalize_missing") return { op: cleanOp, columns: names, tokens: commaList(missingTokens) };
    if (cleanOp === "trim_whitespace") return { op: cleanOp, columns: names };
    return { op: "drop_missing", columns: names };
  }

  function mapping(): Record<string, string> {
    const value: Record<string, string> = { outcome };
    if (["grouped_descriptive", "welch_t", "paired_t"].includes(method)) value.group = group;
    if (["pearson", "spearman"].includes(method)) value.predictor = predictor;
    if (method === "paired_t") value.subject = subject;
    return value;
  }

  const chartRunInfo = versionRuns.find(item => item.id === chartRun && item.status === "succeeded");
  return <section className="workspace-section m2-section" aria-labelledby="m5-title">
    <div className="section-heading"><h2 id="m5-title">实验数据与结果分析</h2><span>人工决定每一步</span></div>
    <p className="muted">从已接受且当前有效的实验计划开始。代码从不可变 CSV 版本计算统计值；Qwen 仅建议清洗和分析方案，并提供待审核的解释。</p>
    <button className="small-button" disabled={busy} onClick={() => void refresh().catch(cause => setError(cause instanceof Error ? cause.message : "刷新失败"))}>刷新实验数据</button>
    {error && <p className="alert" role="alert">{error}</p>}{notice && <p className="notice">{notice}</p>}
    {currentPlans.length === 0 ? <p className="empty">请先在上方接受当前实验计划，再刷新这里。</p> : <>
      <div className="m5-controls"><label>已接受实验计划 <select value={selectedPlanId} onChange={event => { setPlanId(event.target.value); setVersionId(""); }}>
        {currentPlans.map(item => <option value={item.id} key={item.id}>{item.objective}</option>)}
      </select></label><p className="muted">来源：<a href={`#hypothesis-${plan?.hypothesis_id}`}>查看关联假设</a> · 基线：{plan?.baseline}</p></div>

      <h3>一、上传与检查 CSV</h3>
      <div className="source-card"><p>仅支持 UTF-8 CSV。上传不会自动清洗，也不会把原始数据行发送给 Qwen。</p>
        <input type="file" accept=".csv,text/csv" onChange={(event: ChangeEvent<HTMLInputElement>) => setFile(event.target.files?.[0] ?? null)} />
        <button className="small-button" disabled={busy || !file} onClick={() => file && void act(() => uploadDataset(projectId, selectedPlanId, revision, file), "CSV 已保存为不可变原始版本")}>上传 CSV</button>
      </div>
      {versions.length > 0 && <div className="m5-controls"><label>数据版本 <select value={selectedVersionId} onChange={event => setVersionId(event.target.value)}>
        {versions.map(item => <option value={item.id} key={item.id}>{datasets.find(dataset => dataset.versions.some(candidate => candidate.id === item.id))?.original_filename} · {item.kind === "raw" ? "原始" : "清洗后"} · {item.id.slice(0, 8)} · {item.row_count} 行</option>)}
      </select></label></div>}
      {version && <article className="source-card"><div className="source-card-meta"><strong>{version.kind === "raw" ? "原始 CSV" : "清洗后版本"}</strong><span>文件：{selectedDataset?.original_filename}</span><span>{version.row_count} 行 · {version.column_count} 列</span><span>SHA-256：{version.sha256.slice(0, 16)}…</span></div>
        <p><a href={datasetFileUrl(projectId, version.id)}>下载此版本 CSV</a></p>
        <div className="m5-table-wrap"><table className="m5-table"><thead><tr><th>列</th><th>类型</th><th>缺失</th><th>唯一值</th><th>均值 / 中位数 / 标准差</th></tr></thead><tbody>{version.profile.columns.map(item =>
          <tr key={item.name}><td>{item.name}</td><td>{item.inferred_type}</td><td>{item.missing_count}（{(item.missing_fraction * 100).toFixed(1)}%）</td><td>{item.unique_count}</td>
          <td>{item.inferred_type === "numeric" ? `${item.mean ?? "—"} / ${item.median ?? "—"} / ${item.standard_deviation ?? "—"}` : item.categories?.map(category => `${category.value}: ${category.count}`).join("；") ?? "—"}</td></tr>)}</tbody></table></div>
      </article>}

      {version && <><h3 id="data-cleaning">二、清洗方案与人工审核</h3><p className="muted">如无需清洗，可直接将上方原始版本用于分析。删除行仅在你接受明确的清洗方案后执行。</p>
        <div className="source-card"><button className="small-button" disabled={busy} onClick={() => void act(() => suggestCleaning(projectId, version.id, revision), "Qwen 清洗建议已生成，请逐项审核")}>请求 Qwen 建议</button>
          <div className="m5-controls"><label>手动操作 <select value={cleanOp} onChange={event => setCleanOp(event.target.value as CleaningOperation["op"])}>
            <option value="drop_exact_duplicates">删除完全重复行</option><option value="trim_whitespace">修剪文本首尾空格</option>
            <option value="normalize_missing">明确缺失值标记</option><option value="drop_missing">删除指定列缺失的行</option>
            <option value="cast_numeric">转换为数值</option><option value="cast_categorical">转换为分类文本</option>
          </select></label>{cleanOp !== "drop_exact_duplicates" && <label>列名（多列用英文逗号）<input value={cleanColumns} onChange={event => setCleanColumns(event.target.value)} placeholder={columns.join(", ")} /></label>}
          {cleanOp === "normalize_missing" && <label>缺失标记 <input value={missingTokens} onChange={event => setMissingTokens(event.target.value)} /></label>}
          <label>清洗理由 <input value={cleanRationale} onChange={event => setCleanRationale(event.target.value)} /></label>
          <button className="small-button" disabled={busy || !cleanRationale.trim()} onClick={() => void act(() => createCleaning(projectId, version.id, revision, [oneOperation()], cleanRationale), "清洗方案已创建，等待人工审核")}>创建待审核方案</button></div></div>
        <div className="result-list">{cleaning.filter(item => item.source_version_id === version.id).map(item => <article className="source-card" key={item.id}>
          <div className="source-card-meta"><strong>{item.origin === "model" ? "Qwen 建议" : "人工方案"}</strong><span>{reviewLabel(item.review_status)}</span><span>来源版本 {item.source_version_id.slice(0, 8)}</span></div>
          <p>{item.rationale}</p><CleaningPlanReview operations={item.operations} status={item.review_status}
            busy={busy} executed={cleaningRuns.some(run => run.cleaning_plan_id === item.id && run.status === "succeeded")}
            onApprove={() => void act(() => reviewCleaning(projectId, item.id, "accepted", revision), "清洗方案已人工批准")}
            onReject={() => void act(() => reviewCleaning(projectId, item.id, "rejected", revision), "清洗方案已拒绝")}
            onExecute={() => void act(() => executeCleaning(projectId, item.id, revision), "确定性清洗已生成新版本；请查看行数变化")} />
          {cleaningRuns.filter(run => run.cleaning_plan_id === item.id).map(run => <p className="muted" key={run.id}>执行 {run.status}：{run.rows_before ?? "—"} → {run.rows_after ?? "—"} 行；移除 {run.rows_before !== null && run.rows_after !== null ? run.rows_before - run.rows_after : "—"} 行；新版本 {run.output_version?.id.slice(0, 8) ?? "无"}{run.error_type ? `；错误 ${run.error_type}` : ""}</p>)}
        </article>)}</div></>}

      {version && <><h3 id="data-analysis">三、分析方案与人工审核</h3><div className="source-card"><button className="small-button" disabled={busy} onClick={() => void act(() => suggestAnalysis(projectId, version.id, revision), "Qwen 分析建议已生成，请核对列映射")}>请求 Qwen 建议</button>
        <div className="m5-controls"><label>分析方法 <select value={method} onChange={event => setMethod(event.target.value)}>{methods.map(value => <option value={value} key={value}>{methodLabels[value]}</option>)}</select></label>
          <label>结果列 <select value={outcome} onChange={event => setOutcome(event.target.value)}><option value="">请选择</option>{columns.map(value => <option value={value} key={value}>{value}</option>)}</select></label>
          {["grouped_descriptive", "welch_t", "paired_t"].includes(method) && <label>分组/条件列 <select value={group} onChange={event => setGroup(event.target.value)}><option value="">请选择</option>{columns.map(value => <option value={value} key={value}>{value}</option>)}</select></label>}
          {["pearson", "spearman"].includes(method) && <label>预测列 <select value={predictor} onChange={event => setPredictor(event.target.value)}><option value="">请选择</option>{columns.map(value => <option value={value} key={value}>{value}</option>)}</select></label>}
          {method === "paired_t" && <label>受试者/配对 ID 列 <select value={subject} onChange={event => setSubject(event.target.value)}><option value="">请选择</option>{columns.map(value => <option value={value} key={value}>{value}</option>)}</select></label>}
          {["welch_t", "paired_t"].includes(method) && <label>两个比较组（英文逗号分隔，顺序决定差值方向）<input value={comparisonGroups} onChange={event => setComparisonGroups(event.target.value)} /></label>}
          <label>分析理由 <input value={analysisRationale} onChange={event => setAnalysisRationale(event.target.value)} /></label>
          <button className="small-button" disabled={busy || !outcome || !analysisRationale.trim()} onClick={() => void act(() => createAnalysis(projectId, version.id, revision, method, mapping(),
            ["welch_t", "paired_t"].includes(method) ? { groups: commaList(comparisonGroups), missing_policy: "reject" } : { missing_policy: "reject" }, analysisRationale), "分析方案已创建，等待人工审核")}>创建待审核分析方案</button></div></div>
        <div className="result-list">{versionSpecs.map(item => <article className="source-card" key={item.id}><div className="source-card-meta"><strong>{methodLabels[item.method] ?? item.method}</strong><span>{reviewLabel(item.review_status)}</span><span>拟检验假设 {item.hypothesis_id.slice(0, 8)}</span></div>
          <p>理由：{item.rationale}</p><RecordedValues values={{ "列映射": item.column_mapping, "分析参数": item.parameters }} />
          {item.review_status === "pending_review" && <div className="source-card-actions"><button className="small-button" disabled={busy} onClick={() => void act(() => reviewAnalysis(projectId, item.id, "accepted", revision), "分析方案已人工批准")}>人工批准</button><button className="small-button" disabled={busy} onClick={() => void act(() => reviewAnalysis(projectId, item.id, "rejected", revision), "分析方案已拒绝")}>拒绝</button></div>}
          {item.review_status === "accepted" && <button className="small-button" disabled={busy} onClick={() => void act(() => executeAnalysis(projectId, item.id, revision), "确定性统计已完成，原始计算结果已保存")}>执行确定性统计</button>}
        </article>)}</div></>}

      {version && <><h3>四、代码计算的统计结果</h3><p className="muted">以下显示当前文件所有数据版本的计算结果，便于核对 AI 解释引用的完整结果集合。</p>
        {datasetRuns.length === 0 ? <p className="empty">尚未执行分析。</p> : <div className="result-list">{datasetRuns.map(item => <article className="source-card" key={item.id}>
          <div className="source-card-meta"><strong>{methodLabels[item.method] ?? item.method}</strong><span>{item.status === "succeeded" ? "已计算" : item.status === "failed" ? "失败" : "计算中"}</span><span>方法版本 {item.engine_version}</span><span>数据版本 {item.dataset_version_id.slice(0, 8)}</span></div>
          {item.result ? <><RecordedValues values={item.result.values} /><details className="assessment-details"><summary>计算记录与原始值</summary><p>结果 ID：{item.result.id}</p><pre className="m5-result">{JSON.stringify(item.result.values, null, 2)}</pre></details></> : <p>无有效统计结果。{item.error_type ?? ""}</p>}
          {item.status === "succeeded" && <button className="small-button" disabled={busy} onClick={() => void act(() => generateInterpretation(projectId, item.id, revision), "AI 解释草案已生成，等待人工审核")}>请求 AI 解释</button>}
        </article>)}</div>}

        <h3 id="data-figures">五、从真实数据渲染图表</h3><div className="source-card m5-controls"><label>图表类型 <select value={chartType} onChange={event => setChartType(event.target.value as typeof chartType)}><option value="histogram">直方图</option><option value="box_plot">箱线图</option><option value="scatter">散点图</option><option value="group_summary">分组均值图</option></select></label>
          {chartType === "histogram" || chartType === "scatter" ? <label>X 列 <select value={chartX} onChange={event => setChartX(event.target.value)}><option value="">请选择</option>{columns.map(value => <option value={value} key={value}>{value}</option>)}</select></label> : null}
          {chartType !== "histogram" && <label>Y 数值列 <select value={chartY} onChange={event => setChartY(event.target.value)}><option value="">请选择</option>{columns.map(value => <option value={value} key={value}>{value}</option>)}</select></label>}
          {(chartType === "box_plot" || chartType === "group_summary") && <label>分组列 <select value={chartGroup} onChange={event => setChartGroup(event.target.value)}><option value="">请选择</option>{columns.map(value => <option value={value} key={value}>{value}</option>)}</select></label>}
          <label>关联分析 <select value={chartRun} onChange={event => setChartRun(event.target.value)}><option value="">仅数据版本</option>{versionRuns.filter(item => item.status === "succeeded").map(item => <option value={item.id} key={item.id}>{methodLabels[item.method] ?? item.method} · {item.id.slice(0, 8)}</option>)}</select></label>
          <label>图题 <input value={chartTitle} onChange={event => setChartTitle(event.target.value)} /></label>
          <button className="small-button" disabled={busy || !chartTitle.trim()} onClick={() => void act(() => createFigure(projectId, version.id, revision,
            { chart_type: chartType, x_column: chartType === "histogram" || chartType === "scatter" ? chartX : null,
              y_column: chartType === "histogram" ? null : chartY,
              group_column: chartType === "box_plot" || chartType === "group_summary" ? chartGroup : null,
              title: chartTitle, analysis_run_id: chartRunInfo?.id ?? null,
              statistical_result_id: chartRunInfo?.result?.id ?? null }), "图表已从真实数据渲染并保存来源")}>生成确定性图表</button></div>
        <div className="result-list">{datasetFigures.map(item => <article className="source-card" key={item.id}><strong>{item.title}</strong><p className="muted">{item.chart_type} · 数据版本 {item.dataset_version_id.slice(0, 8)} · 分析 {item.analysis_run_id?.slice(0, 8) ?? "未关联"}</p>
          <details className="assessment-details"><summary>图表来源与渲染记录</summary><p className="muted">图表 ID：{item.id} · 渲染版本：{item.renderer_version}</p></details>
          <img className="m5-figure" src={figureFileUrl(projectId, item.id)} alt={`${item.title}，由真实数据渲染的${item.chart_type}图`} />
          <button className="small-button" disabled={busy} onClick={() => {
            if (window.confirm(`永久删除图表 ${item.id} 及其文件？此操作不可恢复。引用它的解释和报告将不再具有当前资格，需要重新生成并审核。原始数据和统计结果保留。`)) {
              void act(() => deleteFigure(projectId, item.id, revision), "图表及文件已永久删除。请刷新页面核查相关解释与报告的当前资格；原始数据和统计结果保留。");
            }
          }}>永久删除图表</button>
        </article>)}</div>

        <h3 id="data-interpretation">六、AI 解释与人工判断</h3><p className="muted">AI 解释仅供参考。准确数值以上方“代码计算的统计结果”为准；接受解释不代表假设已被证明。</p>
        <div className="result-list">{interpretations.filter(item => datasetRuns.some(run => run.id === item.analysis_run_id)).map(item => <article className="source-card" key={item.id}>
          <div className="source-card-meta"><strong>AI 解释{item.origin === "human_revision" ? " · 人工修订" : ""}</strong><span>{reviewLabel(item.review_status)}</span><span>未来报告可用：{item.eligible_for_future_report ? "是" : "否"}</span></div>
          <p>{item.summary}</p><p>与假设的关系：{item.hypothesis_relationship}</p><p>不确定性：{item.uncertainty}</p>
          <p>局限：{item.limitations.join("；") || "未列出"}</p><p>其他解释：{item.alternative_explanations.join("；") || "未列出"}</p>
          <p className="muted">关联统计结果：{item.statistical_result_ids.join("、")}；关联图表：{item.figure_artifact_ids.join("、") || "无"}</p>
          {item.ai_assessment && <p className="muted">AI 独立评估：{item.ai_assessment.verdict} · {item.ai_assessment.justification}</p>}
          {item.review_status === "pending_review" && <div className="source-card-actions"><button className="small-button" disabled={busy} onClick={() => void act(() => reviewInterpretation(projectId, item.id, "accepted", revision), "解释已人工接受")}>人工接受</button><button className="small-button" disabled={busy} onClick={() => void act(() => reviewInterpretation(projectId, item.id, "rejected", revision), "解释已拒绝")}>拒绝</button></div>}
          {!interpretations.some(other => other.supersedes_id === item.id) && <button className="small-button" disabled={busy} onClick={() => { setEditing(item.id); setRevisedSummary(item.summary); setRevisedRelationship(item.hypothesis_relationship); setRevisedUncertainty(item.uncertainty); setRevisedLimitations(item.limitations.join("\n")); setRevisedAlternatives(item.alternative_explanations.join("\n")); }}>创建人工修订版本</button>}
          {editing === item.id && <div className="revision-form"><label>结果摘要<textarea value={revisedSummary} onChange={event => setRevisedSummary(event.target.value)} /></label><label>与假设的关系<textarea value={revisedRelationship} onChange={event => setRevisedRelationship(event.target.value)} /></label><label>不确定性<textarea value={revisedUncertainty} onChange={event => setRevisedUncertainty(event.target.value)} /></label><label>局限（每行一项）<textarea value={revisedLimitations} onChange={event => setRevisedLimitations(event.target.value)} /></label><label>其他解释（每行一项）<textarea value={revisedAlternatives} onChange={event => setRevisedAlternatives(event.target.value)} /></label><p className="muted">新版本沿用计算结果和图表来源，审核状态重置为待审核。文字中请勿重新填写统计数字。</p><button className="small-button" disabled={busy} onClick={() => void act(() => reviseInterpretation(projectId, item.id, revision, interpretationRevisionFields(item, { summary: revisedSummary, hypothesis_relationship: revisedRelationship, uncertainty: revisedUncertainty, limitations: revisedLimitations, alternative_explanations: revisedAlternatives })), "人工修订已保存，需重新审核")}>保存修订版本</button></div>}
        </article>)}</div>
      </>}
    </>}
  </section>;
}
