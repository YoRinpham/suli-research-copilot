"use client";

import { useParams } from "next/navigation";
import { ChangeEvent, FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { EvidenceReferences, InspectSources, ManuscriptFrame, ManuscriptProvider, StagePanel, useManuscript } from "../../../components/manuscript";

import { getProject, type Project } from "../../../lib/api/projects";
import { getPaperFileUrl, indexPaper, listPapers, uploadPaper, type Paper, type ParseStatus } from "../../../lib/api/papers";
import { createEvidence, listEvidence, selectEvidence, type Evidence, type EvidenceType } from "../../../lib/api/evidence";
import { retrieve, type RetrievalResult } from "../../../lib/api/retrieval";
import { ApiError } from "../../../lib/api/client";
import { recoverGenerationTimeout } from "../../../lib/api/generation-recovery";
import { withRefreshFence } from "../../../lib/refresh-fence";
import { generateClaims, listClaims, reviewClaimGrounding, reviseClaim, selectClaim, type Claim, type ClaimType, type GroundingStatus, type AiGroundingAssessment, type HumanGroundingStatus, type Confidence } from "../../../lib/api/claims";
import { generateLandscape, listLandscapes, reviewLandscape, type Landscape, type LandscapeCategory } from "../../../lib/api/landscapes";
import M4Workspace from "./m4-workspace";
import M5Workspace from "./m5-workspace";
import M6Workspace from "./m6-workspace";

const statusLabels: Record<ParseStatus, string> = {
  uploaded: "已上传，待索引", parsing: "解析中", parsed: "已解析，待嵌入", embedding: "嵌入中",
  parse_failed: "解析失败", low_quality: "文本质量不足", embedding_failed: "嵌入失败，可重试", indexed: "已索引",
};
const evidenceTypeLabels: Record<EvidenceType, string> = {
  reported_result: "研究结果", method_description: "方法描述", dataset_detail: "数据集细节",
  limitation: "局限性", author_claim: "作者主张", other: "其他",
};
const categoryLabels: Record<LandscapeCategory, string> = {
  approach: "研究路径", method: "方法", dataset: "数据集", metric: "指标", finding: "发现", conflict: "争议与条件差异",
};
const claimTypeLabels: Record<ClaimType, string> = { finding: "发现", method: "方法", comparison: "比较", limitation: "局限", other: "其他" };
const groundingLabels: Record<HumanGroundingStatus, string> = { pending_review: "待审核", supported: "确认有直接依据", inferred: "确认属推断", unsupported: "不受支持", insufficient_evidence: "证据不足" };
const aiGroundingLabels: Record<AiGroundingAssessment, string> = { directly_supported: "直接支持", inferentially_supported: "推断支持", unsupported: "不受支持", contradicted: "证据相悖", insufficient_evidence: "证据不足" };
const comparisonLabels: Record<string, string> = { dataset: "数据集", population: "人群", task: "任务", metric: "指标", experimental_condition: "实验条件", baseline: "基线" };
const comparisonValueLabels: Record<string, string> = { comparable: "可比", different: "不同", unknown: "未知" };
const confidenceLabels: Record<Confidence, string> = { low: "低", medium: "中", high: "高", unknown: "未知" };

function LandscapeSourceAssessment({ assessment }: { assessment: Record<string, unknown> }) {
  const supportLabels: Record<string, string> = { supported: "当前来源支持", unsupported: "不受支持", insufficient_evidence: "证据不足" };
  const canonical = assessment.source_binding_origin === "server_bound_canonical_source_text";
  return <>
    {typeof assessment.support_verdict === "string" && <p>AI 科学支持评估：{supportLabels[assessment.support_verdict] ?? assessment.support_verdict}（需人工审核）</p>}
    {Array.isArray(assessment.qualifications) && assessment.qualifications.length > 0 && <p>来源限制与条件：{assessment.qualifications.filter((value) => typeof value === "string").join("；")}</p>}
    {canonical && <details><summary>核查引用的 Evidence 完整原文（由系统绑定，需人工核对）</summary>
      {Array.isArray(assessment.support_excerpts) && assessment.support_excerpts.map((value: unknown, index: number) => {
        if (typeof value !== "object" || value === null) return null;
        const excerpt = value as Record<string, unknown>;
        if (typeof excerpt.evidence_id !== "string" || typeof excerpt.excerpt !== "string") return null;
        return <div key={index}><a href={`#evidence-${excerpt.evidence_id}`}>查看来源证据</a><blockquote style={{ whiteSpace: "pre-wrap" }}>{excerpt.excerpt}</blockquote></div>;
      })}
    </details>}
  </>;
}

function failure(cause: unknown): string {
  if (cause instanceof ApiError) {
    if (cause.code === "no_relevant_chunks") return "当前项目没有可检索的已索引片段。请先索引论文。";
    if (cause.code === "stale_revision") return "研究问题已更新，请刷新工作区后重试。";
    if (cause.code === "invalid_provenance") return "所选原文不属于该论文片段，请重新选择。";
    if (cause.code === "provider_timeout") return "嵌入服务超时，请稍后重试。";
    if (cause.code === "embedding_failed") return "嵌入服务失败，请检查配置后重试。";
    if (cause.code === "invalid_state") return "论文当前状态不允许此操作，请刷新后查看。";
    if (cause.code === "insufficient_evidence") return "请先明确选择足够的证据或科学主张。";
    if (cause.code === "structured_output_invalid") return "模型输出未通过结构或来源校验，未保存科学结论。";
    if (cause.code === "llm_failed") return "Qwen 调用失败，请检查模型配置后重试。";
    if (cause.code === "superseded_resource") return "此版本已有修订，请刷新后选择新版本。";
  }
  return cause instanceof Error ? cause.message : "操作失败，请重试。";
}

function Candidate({ result, projectId, filename, save }: {
  result: RetrievalResult; projectId: string; filename: string;
  save: (result: RetrievalResult, type: EvidenceType, note: string) => Promise<void>;
}) {
  const [type, setType] = useState<EvidenceType>("other");
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  async function onSave() {
    setSaving(true);
    try { await save(result, type, note); } finally { setSaving(false); }
  }
  return <article className="source-card">
    <div className="source-card-meta"><strong>{result.paper_title || filename}</strong><span>PDF 第 {result.page_number} 页</span><span>相关度 {result.similarity_score.toFixed(3)}</span></div>
    {result.section && <p className="muted">章节：{result.section}</p>}
    <blockquote>{result.text}</blockquote>
    <div className="source-card-actions">
      <a href={`${getPaperFileUrl(projectId, result.paper_id)}#page=${result.page_number}`} target="_blank" rel="noopener noreferrer">查看原始页</a>
      <label>证据类型 <select value={type} onChange={(event) => setType(event.target.value as EvidenceType)}>{Object.entries(evidenceTypeLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label>相关说明 <input value={note} onChange={(event) => setNote(event.target.value)} maxLength={2000} placeholder="可选" /></label>
      <button className="small-button" onClick={() => void onSave()} disabled={saving}>{saving ? "保存中…" : "保存为证据"}</button>
    </div>
  </article>;
}

export default function ProjectPage() {
  const projectId = useParams<{ id: string }>().id;
  return <ManuscriptProvider key={projectId}><ProjectContent projectId={projectId} /></ManuscriptProvider>;
}

function ProjectContent({ projectId }: { projectId: string }) {
  const { stage, visited } = useManuscript();
  const initialized = useRef(false);
  const refreshSequence = useRef(0);
  const [project, setProject] = useState<Project | null>(null);
  const [papers, setPapers] = useState<Paper[]>([]);
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [claims, setClaims] = useState<Claim[]>([]);
  const [landscapes, setLandscapes] = useState<Landscape[]>([]);
  const [results, setResults] = useState<RetrievalResult[]>([]);
  const [query, setQuery] = useState("");
  const [effectiveQuery, setEffectiveQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [indexingId, setIndexingId] = useState<string | null>(null);
  const [searching, setSearching] = useState(false);
  const [changingId, setChangingId] = useState<string | null>(null);
  const [generatingClaims, setGeneratingClaims] = useState(false);
  const [generatingLandscape, setGeneratingLandscape] = useState(false);
  const [editingClaimId, setEditingClaimId] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const [editUncertainty, setEditUncertainty] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const refresh = useCallback(async () => {
    const sequence = ++refreshSequence.current;
    setError("");
    try {
      const [loadedProject, loadedPapers, loadedEvidence, loadedClaims, loadedLandscapes] = await Promise.all([
        getProject(projectId), listPapers(projectId), listEvidence(projectId), listClaims(projectId), listLandscapes(projectId),
      ]);
      if (sequence !== refreshSequence.current) return;
      setProject(loadedProject);
      setPapers(loadedPapers);
      setEvidence(loadedEvidence);
      setClaims(loadedClaims);
      setLandscapes(loadedLandscapes);
      if (!initialized.current) {
        setQuery(loadedProject.research_question);
        initialized.current = true;
      }
    } catch (cause) { if (sequence === refreshSequence.current) setError(failure(cause)); }
    finally { if (sequence === refreshSequence.current) setLoading(false); }
  }, [projectId]);

  useEffect(() => { void refresh(); }, [refresh, stage]);

  function mutate<T>(task: () => Promise<T>) { return withRefreshFence(refreshSequence, task); }

  async function onFileChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setNotice("");
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      setError("请选择 PDF 文件。");
      event.target.value = "";
      return;
    }
    setUploading(true);
    setError("");
    try {
      const paper = await mutate(() => uploadPaper(projectId, file));
      setPapers((current) => [...current.filter(item => item.id !== paper.id), paper]);
      setNotice("PDF 已保存。点击「索引论文」开始解析和嵌入。");
    } catch (cause) { setError(failure(cause)); }
    finally { setUploading(false); event.target.value = ""; }
  }

  async function onIndex(paper: Paper) {
    setIndexingId(paper.id);
    setError(""); setNotice("");
    try {
      const updated = await mutate(() => indexPaper(projectId, paper.id));
      setPapers((current) => current.map((item) => item.id === updated.id ? updated : item));
      setNotice(`${updated.source_filename} 已索引，可用于检索。`);
    } catch (cause) {
      setError(failure(cause));
      try { setPapers(await listPapers(projectId)); } catch { /* Keep the original error. */ }
    } finally { setIndexingId(null); }
  }

  async function onSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!project) return;
    setSearching(true); setError(""); setResults([]);
    try {
      const response = await retrieve(projectId, { query, expected_question_revision: project.question_revision });
      setEffectiveQuery(response.query); setResults(response.results);
    } catch (cause) { setError(failure(cause)); }
    finally { setSearching(false); }
  }

  async function onSave(result: RetrievalResult, type: EvidenceType, note: string) {
    if (!project) return;
    setError("");
    try {
      const saved = await mutate(() => createEvidence(projectId, {
        paper_chunk_id: result.chunk_id, source_text: result.text, evidence_type: type,
        relevance_note: note || undefined, expected_question_revision: project.question_revision,
      }));
      setEvidence((current) => [...current.filter(item => item.id !== saved.id), saved]);
      setNotice("片段已保存为证据。请核查原始 PDF 页面。");
    } catch (cause) { setError(failure(cause)); }
  }

  async function onSelection(item: Evidence) {
    if (!project) return;
    setChangingId(item.id); setError("");
    try {
      const updated = await mutate(() => selectEvidence(projectId, item.id, item.selection_status === "selected" ? "rejected" : "selected", project.question_revision));
      setEvidence((current) => current.map((entry) => entry.id === updated.id ? updated : entry));
      await refresh();
    } catch (cause) { setError(failure(cause)); }
    finally { setChangingId(null); }
  }

  async function onGenerateClaims() {
    if (!project) return;
    const ids = evidence.filter((item) => item.selection_status === "selected" && item.question_revision === project.question_revision).map((item) => item.id);
    setGeneratingClaims(true); setError(""); setNotice("");
    try {
      const result = await mutate(() => generateClaims(projectId, ids, project.question_revision));
      setClaims((current) => [...current.filter(item => !result.claims.some(saved => saved.id === item.id)), ...result.claims]);
      setNotice(`已生成 ${result.claims.length} 条候选科学主张。请逐条核查来源并手动选择。`);
    } catch (cause) {
      if (!recoverGenerationTimeout(cause, async () => setClaims(await listClaims(projectId)), setError)) {
        setError(failure(cause));
      }
    }
    finally { setGeneratingClaims(false); }
  }

  async function onClaimSelection(item: Claim, status: "selected" | "rejected") {
    if (!project) return;
    setChangingId(item.id); setError("");
    try {
      const updated = await mutate(() => selectClaim(projectId, item.id, status, project.question_revision));
      setClaims((current) => current.map((entry) => entry.id === updated.id ? updated : entry));
    } catch (cause) { setError(failure(cause)); }
    finally { setChangingId(null); }
  }

  async function onClaimGrounding(item: Claim, status: GroundingStatus) {
    if (!project) return;
    setChangingId(item.id); setError("");
    try {
      const updated = await mutate(() => reviewClaimGrounding(projectId, item.id, status, project.question_revision));
      setClaims((current) => current.map((entry) => entry.id === updated.id ? updated : entry));
      setNotice("人工依据判断已记录。该判断只针对当前提供的证据。");
    } catch (cause) { setError(failure(cause)); }
    finally { setChangingId(null); }
  }

  async function onClaimRevision(item: Claim) {
    if (!project) return;
    setChangingId(item.id); setError("");
    try {
      await mutate(() => reviseClaim(projectId, item.id, {
        text: editText, uncertainty: editUncertainty, claim_type: item.claim_type,
        confidence: item.confidence,
        evidence_for_ids: item.evidence_for_ids, evidence_against_ids: item.evidence_against_ids,
        expected_question_revision: project.question_revision,
      }));
      await refresh();
      setEditingClaimId(null);
      setNotice("人工修订已创建新版本；请重新核查并选择。旧版本仍可查看。");
    } catch (cause) { setError(failure(cause)); }
    finally { setChangingId(null); }
  }

  async function onGenerateLandscape() {
    if (!project) return;
    const ids = claims.filter((item) => item.eligible_for_generation).map((item) => item.id);
    setGeneratingLandscape(true); setError(""); setNotice("");
    try {
      const result = await mutate(() => generateLandscape(projectId, ids, project.question_revision));
      setLandscapes((current) => [...current.filter(item => item.id !== result.landscape.id), result.landscape]);
      setNotice("研究全景已生成。请检查每个条目的主张与原始证据。");
    } catch (cause) {
      if (!recoverGenerationTimeout(cause, async () => setLandscapes(await listLandscapes(projectId)), setError)) {
        setError(failure(cause));
      }
    }
    finally { setGeneratingLandscape(false); }
  }

  async function onLandscapeReview(item: Landscape, status: "accepted" | "rejected") {
    if (!project) return;
    setChangingId(item.id); setError("");
    try {
      const updated = await mutate(() => reviewLandscape(projectId, item.id, status, project.question_revision));
      setLandscapes((current) => current.map((entry) => entry.id === updated.id ? updated : entry));
      setNotice(status === "accepted" ? "已接受这份工作性综合；这不代表领域内普遍科学真理。" : "已拒绝这份研究全景。");
    } catch (cause) { setError(failure(cause)); }
    finally { setChangingId(null); }
  }

  function sourceLinks(ids: string[]) {
    return <EvidenceReferences ids={ids} evidence={evidence} />;
  }

  const searchable = papers.some((paper) => paper.parse_status === "indexed");
  return <ManuscriptFrame project={project} evidence={evidence} claims={claims}>
    {loading ? <p className="muted" role="status">正在加载项目、论文与证据…</p> : error && !project ? <div className="alert" role="alert">{error} <button onClick={() => void refresh()}>重试</button></div> : project && <>
      {error && <p className="alert" role="alert">{error} <button onClick={() => void refresh()}>刷新</button></p>}
      {notice && <p className="notice" role="status">{notice}</p>}
      <StagePanel stage="literature">
      <section className="workspace-section papers-section" aria-labelledby="papers-title">
        <div className="section-heading"><h2 id="papers-title">论文原文</h2><span>{papers.length} 篇</span></div>
        <div className="upload-row"><div><strong>添加 PDF</strong><p>保留原始文件；页面编号以 PDF 的物理页序为准。</p></div><label className="upload-button">{uploading ? "上传中…" : "选择 PDF"}<input type="file" accept=".pdf,application/pdf" onChange={(event) => void onFileChange(event)} disabled={uploading} /></label></div>
        {papers.length === 0 ? <p className="empty">尚未上传论文。选择一份 PDF 作为可核查的原始来源。</p> : <ul className="paper-list">{papers.map((paper) => <li key={paper.id}><div><strong>{paper.title || paper.source_filename}</strong><p>{paper.page_count ?? "?"} 页 · <span className={`paper-status status-${paper.parse_status}`}>{indexingId === paper.id ? "索引中…" : statusLabels[paper.parse_status]}</span></p>{paper.quality_note && <p className="quality-note">{paper.quality_note}</p>}</div><div className="paper-actions"><a href={getPaperFileUrl(projectId, paper.id)} target="_blank" rel="noopener noreferrer">打开原始 PDF</a>{(["uploaded", "parsed", "embedding_failed"] as ParseStatus[]).includes(paper.parse_status) && <button className="small-button" onClick={() => void onIndex(paper)} disabled={indexingId !== null}>{paper.parse_status === "embedding_failed" ? "重试嵌入" : "索引论文"}</button>}</div></li>)}</ul>}
      </section>
      <section className="workspace-section m2-section" aria-labelledby="retrieval-title">
        <div className="section-heading"><h2 id="retrieval-title">检索候选片段</h2><span>相关度只表示检索匹配，不代表科学可信度</span></div>
        <form className="retrieval-form" onSubmit={(event) => void onSearch(event)}><label htmlFor="retrieval-query">检索问题</label><input id="retrieval-query" value={query} onChange={(event) => setQuery(event.target.value)} placeholder={project.research_question} /><button className="primary-button" disabled={searching || !searchable}>{searching ? "检索中…" : "检索论文"}</button></form>
        {!searchable && <p className="empty">先索引至少一篇论文，再检索候选片段。</p>}
        {results.length > 0 && <div className="result-list"><p className="muted">“{effectiveQuery}” 找到 {results.length} 个候选片段。逐条核查后再保存为证据。</p>{results.map((result) => <Candidate key={result.chunk_id} result={result} projectId={projectId} filename={papers.find((paper) => paper.id === result.paper_id)?.source_filename ?? "原始论文"} save={onSave} />)}</div>}
      </section>
      <section className="workspace-section m2-section" aria-labelledby="evidence-title">
        <div className="section-heading"><h2 id="evidence-title">已保存证据</h2><span>{evidence.length} 条</span></div>
        {evidence.length === 0 ? <p className="empty">尚无证据。从检索候选中明确选择片段后，它才会出现在这里。</p> : <div className="result-list">{evidence.map((item) => <article className="source-card" id={`evidence-${item.id}`} key={item.id}><div className="source-card-meta"><strong>{item.paper_title || item.source_filename}</strong><span>PDF 第 {item.page_number} 页</span><span>{evidenceTypeLabels[item.evidence_type]}</span><span className={item.selection_status === "selected" ? "selected" : "rejected"}>{item.selection_status === "selected" ? "已选择" : "已排除"}</span></div><blockquote>{item.source_text}</blockquote>{item.relevance_note && <p className="muted">相关说明：{item.relevance_note}</p>}<div className="source-card-actions"><a href={`${getPaperFileUrl(projectId, item.paper_id)}#page=${item.page_number}`} target="_blank" rel="noopener noreferrer">查看原始页</a><span>问题修订 {item.question_revision}</span><button className="small-button" disabled={changingId === item.id || item.question_revision !== project.question_revision} onClick={() => void onSelection(item)}>{changingId === item.id ? "更新中…" : item.selection_status === "selected" ? "排除证据" : "重新选择"}</button></div></article>)}</div>}
      </section>
      </StagePanel>
      <StagePanel stage="landscape">
      <section className="workspace-section m2-section" aria-labelledby="claims-title">
        <div className="section-heading"><h2 id="claims-title">科学主张</h2><span>已选证据 → 人工审核</span></div>
        <p className="muted">仅当前修订且已选择的证据会发送给 Qwen。生成结果默认是候选，不会自动进入研究全景。</p>
        <button className="primary-button" disabled={generatingClaims || !evidence.some((item) => item.selection_status === "selected" && item.question_revision === project.question_revision)} onClick={() => void onGenerateClaims()}>{generatingClaims ? "正在生成科学主张…" : "用已选证据生成科学主张"}</button>
        {claims.length === 0 ? <p className="empty">尚无科学主张。先核查并选择证据，再显式请求生成。</p> : <div className="result-list">{claims.map((item) => <article className="source-card" id={`claim-${item.id}`} key={item.id}>
          <div className="source-card-meta"><strong>{claimTypeLabels[item.claim_type]}</strong><span>来源：{item.origin === "model" ? "Qwen 生成" : "人工修订"}</span><span>AI 评估：{item.ai_grounding_assessment ? aiGroundingLabels[item.ai_grounding_assessment] : "未评估"}</span><span>人工依据审核：{groundingLabels[item.human_grounding_status]}</span><span>信心：{confidenceLabels[item.confidence]}</span><span>选择：{item.selection_status === "selected" ? "已选择" : item.selection_status === "rejected" ? "已排除" : "候选"}</span></div>
          <p className="scientific-text">{item.text}</p><p className="muted">不确定性：{item.uncertainty}</p>
          <InspectSources note={{ title: "科学主张的证据", evidenceRefs: [...item.evidence_for_ids.map(id => ({ id, role: "支持证据" })), ...item.evidence_against_ids.map(id => ({ id, role: "质疑证据" }))] }} />
          <details className="assessment-details"><summary>AI 参考评估与核查摘录</summary>
          {item.ai_justification && <p className="muted">AI 评估理由：{item.ai_justification}</p>}
          {!!item.ai_risk_flags?.length && <p className="rejected">AI 风险标记：{item.ai_risk_flags.join("、")}</p>}
          {!!item.ai_support_excerpts?.length && <div className="source-links"><strong>AI 引用的原文摘录</strong>{item.ai_support_excerpts.map((excerpt, index) => <blockquote key={`${excerpt.evidence_id}-${index}`}>{excerpt.excerpt}</blockquote>)}</div>}
          </details>
          <div className="source-groups"><div><strong>支持证据</strong><div className="source-links">{sourceLinks(item.evidence_for_ids)}</div></div><div><strong>质疑证据</strong><div className="source-links">{item.evidence_against_ids.length ? sourceLinks(item.evidence_against_ids) : <span>无</span>}</div></div></div>
          {!item.eligible_for_generation && <p className="rejected">该主张 尚不能用于研究全景；需人工确认依据并选择，且来源保持有效。</p>}
          <div className="source-card-actions"><span>人工依据判断：</span>{(["supported", "inferred", "unsupported", "insufficient_evidence"] as GroundingStatus[]).map((status) => <button key={status} className="small-button" disabled={changingId === item.id || item.question_revision !== project.question_revision || claims.some((other) => other.supersedes_id === item.id)} onClick={() => void onClaimGrounding(item, status)}>{groundingLabels[status]}</button>)}</div>
          <div className="source-card-actions"><span>问题修订 {item.question_revision}</span>{item.supersedes_id && <span>人工修订自 {item.supersedes_id}</span>}<button className="small-button" disabled={changingId === item.id || item.question_revision !== project.question_revision || claims.some((other) => other.supersedes_id === item.id)} onClick={() => void onClaimSelection(item, "selected")}>选择主张</button><button className="small-button" disabled={changingId === item.id || item.question_revision !== project.question_revision || claims.some((other) => other.supersedes_id === item.id)} onClick={() => void onClaimSelection(item, "rejected")}>排除主张</button><button className="small-button" disabled={changingId === item.id || item.question_revision !== project.question_revision || claims.some((other) => other.supersedes_id === item.id)} onClick={() => { setEditingClaimId(item.id); setEditText(item.text); setEditUncertainty(item.uncertainty); }}>创建修订</button></div>
          {editingClaimId === item.id && <div className="revision-form"><label>修订文本<textarea value={editText} onChange={(event) => setEditText(event.target.value)} rows={3} /></label><label>不确定性<textarea value={editUncertainty} onChange={(event) => setEditUncertainty(event.target.value)} rows={2} /></label><p className="muted">类型与来源沿用本版本；新版本的人工依据判断重置为待审核，需重新判断并选择。</p><button className="small-button" disabled={changingId === item.id || !editText.trim() || !editUncertainty.trim()} onClick={() => void onClaimRevision(item)}>保存新版本</button><button className="small-button" onClick={() => setEditingClaimId(null)}>取消</button></div>}
        </article>)}</div>}
      </section>
      <section className="workspace-section m2-section" aria-labelledby="landscape-title">
        <div className="section-heading"><h2 id="landscape-title">研究全景</h2><span>仅使用人工确认依据且选定的科学主张</span></div>
        <p className="muted">先逐条判断科学主张 的证据支持程度并选择，再请求 Qwen 综合。AI 的摘要与冲突评估仅供参考；研究全景需人工接受后才能供后续科学阶段使用。</p>
        <button className="primary-button" disabled={generatingLandscape || !claims.some((item) => item.eligible_for_generation)} onClick={() => void onGenerateLandscape()}>{generatingLandscape ? "正在生成全景…" : "用已选主张 生成研究全景"}</button>
        {landscapes.length === 0 ? <p className="empty">尚无研究全景。</p> : <div className="result-list">{landscapes.map((landscape) => <article className="source-card" key={landscape.id}>
          <div className="source-card-meta"><strong>研究全景 · 问题修订 {landscape.question_revision}</strong><span>{landscape.origin === "model" ? "Qwen 综合" : "人工修订"}</span><span>人工审核：{landscape.review_status === "accepted" ? "已接受" : landscape.review_status === "rejected" ? "已拒绝" : "待审核"}</span><span>后续可用：{landscape.eligible_for_downstream ? "是" : "否"}</span></div>
          <p className="scientific-text">{landscape.summary}</p><p className="muted">整体不确定性：{landscape.uncertainty}</p>
          {landscape.ai_summary_assessments?.map((assessment, index) => <div className="muted" key={index}><p>AI 摘要评估 {index + 1}：{String(assessment.verdict ?? "未知")}</p><LandscapeSourceAssessment assessment={assessment} />{Array.isArray(assessment.flagged_statements) && assessment.flagged_statements.length > 0 && <p>标记的陈述：{assessment.flagged_statements.join("；")}</p>}{typeof assessment.justification === "string" && <p>理由：{assessment.justification}</p>}</div>)}
          <div className="source-card-actions"><button className="small-button" disabled={changingId === landscape.id || landscape.question_revision !== project.question_revision || landscapes.some((other) => other.supersedes_id === landscape.id)} onClick={() => void onLandscapeReview(landscape, "accepted")}>人工接受</button><button className="small-button" disabled={changingId === landscape.id || landscape.question_revision !== project.question_revision || landscapes.some((other) => other.supersedes_id === landscape.id)} onClick={() => void onLandscapeReview(landscape, "rejected")}>人工拒绝</button></div>
          {landscape.items.map((item) => <div id={`landscape-item-${item.id}`} className={`landscape-item ${item.category === "conflict" ? "conflict-item" : ""}`} key={item.id}><h3>{categoryLabels[item.category]}</h3><p>{item.text}</p><p className="muted">不确定性：{item.uncertainty}</p>{item.ai_conflict_assessment && <div className="muted"><p>AI 冲突评估：{String(item.ai_conflict_assessment.verdict ?? "未知")}</p><LandscapeSourceAssessment assessment={item.ai_conflict_assessment} />{item.ai_conflict_assessment.asserts_direct_conflict === true && item.ai_conflict_assessment.server_direct_conflict_role === false && <p>服务端结构校验：此条目未构成研究间直接冲突陈述。</p>}{typeof item.ai_conflict_assessment.dimensions === "object" && item.ai_conflict_assessment.dimensions !== null && <p>可比条件：{Object.entries(item.ai_conflict_assessment.dimensions as Record<string, string>).map(([name, value]) => `${comparisonLabels[name] ?? name} ${comparisonValueLabels[value] ?? value}`).join("；")}</p>}{typeof item.ai_conflict_assessment.justification === "string" && <p>评估理由：{item.ai_conflict_assessment.justification}</p>}</div>}<div className="source-groups"><div><strong>来源主张</strong><div className="source-links">{item.claim_ids.map((id) => <a key={id} href={`#claim-${id}`}>{claims.find((entry) => entry.id === id)?.text ?? id}</a>)}</div></div><div><strong>原始证据</strong><div className="source-links">{sourceLinks(item.evidence_ids)}</div></div></div></div>)}
        </article>)}</div>}
      </section>
      </StagePanel>
      {(visited.has("directions") || visited.has("experiment")) && <div hidden={stage !== "directions" && stage !== "experiment"}><M4Workspace projectId={projectId} revision={project.question_revision} landscapes={landscapes} claims={claims} evidence={evidence} /></div>}
      {visited.has("experiment") && <StagePanel stage="experiment"><M5Workspace projectId={projectId} revision={project.question_revision} /></StagePanel>}
      {visited.has("report") && <StagePanel stage="report"><M6Workspace projectId={projectId} revision={project.question_revision} /></StagePanel>}
    </>}
  </ManuscriptFrame>;
}
