"use client";

import Link from "next/link";
import { createContext, useCallback, useContext, useEffect, useRef, useState, type MouseEvent, type ReactNode } from "react";
import type { Project } from "../lib/api/projects";
import type { Evidence } from "../lib/api/evidence";
import type { Claim } from "../lib/api/claims";
import { getPaperFileUrl } from "../lib/api/papers";
import { WORKSPACE_STAGES, resolveWorkspaceLocation, stageForAnchor, workspaceHref, type WorkspaceStage } from "../lib/workspace-navigation";

export type SourceNote = {
  title: string;
  evidenceRefs?: { id: string; role?: string }[];
  claimIds?: string[];
  excerpts?: { id: string; paperId: string; title: string; page: number; text: string; role?: string }[];
  explanation?: string;
};

type ManuscriptContext = {
  stage: WorkspaceStage;
  visited: ReadonlySet<WorkspaceStage>;
  navigate: (stage: WorkspaceStage, anchor?: string) => void;
  note: SourceNote | null;
  inspect: (note: SourceNote) => void;
  closeNote: () => void;
};
const Context = createContext<ManuscriptContext | null>(null);

export function useManuscript() {
  const context = useContext(Context);
  if (!context) throw new Error("Research content must be inside ManuscriptProvider");
  return context;
}

export function ManuscriptProvider({ children }: { children: ReactNode }) {
  const [location, setLocation] = useState<{ stage: WorkspaceStage; anchor: string | null }>({ stage: "literature", anchor: null });
  const [visited, setVisited] = useState<ReadonlySet<WorkspaceStage>>(new Set(["literature"]));
  const [note, setNote] = useState<SourceNote | null>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const readLocation = useCallback(() => {
    const next = resolveWorkspaceLocation(window.location.search, window.location.hash);
    setLocation(next);
    setVisited(current => current.has(next.stage) ? current : new Set([...current, next.stage]));
  }, []);
  useEffect(() => {
    readLocation();
    window.addEventListener("popstate", readLocation);
    window.addEventListener("hashchange", readLocation);
    return () => {
      window.removeEventListener("popstate", readLocation);
      window.removeEventListener("hashchange", readLocation);
    };
  }, [readLocation]);
  const navigate = useCallback((stage: WorkspaceStage, anchor?: string) => {
    window.history.pushState(null, "", workspaceHref(window.location.href, stage, anchor));
    readLocation();
    if (!anchor) window.scrollTo({ top: 0, behavior: "instant" });
  }, [readLocation]);

  // Targets may arrive after the stage's initial request. Never scroll a hidden stage.
  useEffect(() => {
    if (!location.anchor) return;
    const reveal = () => {
      const target = document.getElementById(location.anchor!);
      if (!target || target.closest("[hidden]")) return false;
      let parent = target.parentElement;
      while (parent) {
        if (parent instanceof HTMLDetailsElement) parent.open = true;
        parent = parent.parentElement;
      }
      if (target instanceof HTMLDetailsElement) target.open = true;
      target.setAttribute("tabindex", "-1");
      target.focus({ preventScroll: true });
      target.scrollIntoView({ block: "start", behavior: "instant" });
      return true;
    };
    const observer = new MutationObserver(() => { if (reveal()) observer.disconnect(); });
    const frame = requestAnimationFrame(() => {
      if (!reveal()) observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["hidden"] });
    });
    return () => { cancelAnimationFrame(frame); observer.disconnect(); };
  }, [location]);
  const inspect = useCallback((next: SourceNote) => {
    returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setNote(next);
  }, []);
  const closeNote = useCallback(() => {
    setNote(null);
    returnFocus.current?.focus({ preventScroll: true });
  }, []);
  return <Context.Provider value={{ stage: location.stage, visited, navigate, note, inspect, closeNote }}>{children}</Context.Provider>;
}

export function AppBrand({ showLeaf = true }: { showLeaf?: boolean }) {
  return <Link className="wordmark" href="/" aria-label="溯理 SULI，所有研究项目">
    {showLeaf && <svg className="brand-leaf" viewBox="0 0 32 40" fill="none" aria-hidden="true"><path d="M10 34C4 25 5 13 22 4c3 13-1 25-12 30Z" fill="currentColor" opacity=".85"/><path d="M7 38C12 23 17 16 22 5M13 30C18 27 23 26 29 26c-2 8-9 11-16 4Z" stroke="currentColor" strokeWidth="1.2"/></svg>}
    <span>溯理 SULI</span>
  </Link>;
}

export function StagePanel({ stage, children }: { stage: WorkspaceStage; children: ReactNode }) {
  const workspace = useManuscript();
  return <div hidden={workspace.stage !== stage} data-stage-panel={stage}>{children}</div>;
}

export function InspectSources({ note, label = "核查来源" }: { note: SourceNote; label?: string }) {
  const { inspect } = useManuscript();
  return <button type="button" className="source-note-button" onClick={() => inspect(note)}><span aria-hidden="true">↗</span> {label}</button>;
}

export function EvidenceReferences({ ids, evidence, role }: { ids: string[]; evidence: Evidence[]; role?: string }) {
  return <>{ids.map(id => {
    const source = evidence.find(item => item.id === id);
    return source ? <div className="source-reference" key={id}>
      <InspectSources label={`${source.paper_title || source.source_filename} · PDF 第 ${source.page_number} 页`}
        note={{ title: role || "证据原文", evidenceRefs: [{ id, role }] }} />
    </div> : <span className="rejected" key={id}>来源 {id} 暂不可用，请刷新来源列表。</span>;
  })}</>;
}

function SourceAside({ projectId, evidence, claims }: { projectId: string; evidence: Evidence[]; claims: Claim[] }) {
  const { note, closeNote, stage, navigate } = useManuscript();
  const panel = useRef<HTMLElement>(null);
  useEffect(() => {
    if (!note) return;
    const isMobile = () => window.matchMedia("(max-width: 980px)").matches;
    const previousOverflow = document.body.style.overflow;
    const updateModal = () => {
      panel.current?.setAttribute("role", isMobile() ? "dialog" : "complementary");
      if (isMobile()) panel.current?.setAttribute("aria-modal", "true");
      else panel.current?.removeAttribute("aria-modal");
      document.body.style.overflow = isMobile() ? "hidden" : previousOverflow;
    };
    updateModal();
    panel.current?.focus({ preventScroll: true });
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); closeNote(); }
      if (event.key !== "Tab" || !isMobile()) return;
      const controls = Array.from(panel.current?.querySelectorAll<HTMLElement>("button:not(:disabled), a[href], summary, [tabindex='0']") ?? []);
      const first = controls[0], last = controls.at(-1);
      if (event.shiftKey && (document.activeElement === first || document.activeElement === panel.current)) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && (document.activeElement === last || document.activeElement === panel.current)) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", updateModal);
    return () => { document.removeEventListener("keydown", onKey); window.removeEventListener("resize", updateModal); document.body.style.overflow = previousOverflow; };
  }, [note, closeNote]);

  const excerpts = note?.excerpts ?? [];
  return <>
    {note && <div className="source-backdrop" onClick={closeNote} aria-hidden="true" />}
    <aside className={`margin-notes ${note ? "is-open" : ""}`} ref={panel} tabIndex={-1} aria-label={note ? "来源核查" : "研究页边"}>
      <div className="margin-heading"><span>{note ? "来源核查" : "研究页边"}</span>{note && <button className="text-button" onClick={closeNote} aria-label="关闭来源核查">关闭 ×</button>}</div>
      {note ? <>
        <h2>{note.title}</h2>
        {note.explanation && <p>{note.explanation}</p>}
        {note.claimIds?.map(id => {
          const claim = claims.find(item => item.id === id);
          return <div className="margin-entry" key={id}><span className="margin-label">关联科学主张</span><p>{claim?.text ?? `主张 ${id} 暂不可用`}</p>{claim && <button className="text-button" onClick={() => { closeNote(); navigate("landscape", `claim-${id}`); }}>阅读主张</button>}</div>;
        })}
        {note.evidenceRefs?.map(({ id, role }) => {
          const source = evidence.find(item => item.id === id);
          return source ? <SourceExcerpt key={`${id}-${role}`} title={source.paper_title || source.source_filename} text={source.source_text} page={source.page_number} paperId={source.paper_id} projectId={projectId} role={role}>
            <button className="text-button" onClick={() => { closeNote(); navigate("literature", `evidence-${id}`); }}>查看证据记录</button>
          </SourceExcerpt> : <p className="rejected" key={id}>来源 {id} 暂不可用，请刷新项目。不会替代或补造原文。</p>;
        })}
        {excerpts.map(source => <SourceExcerpt key={source.id} projectId={projectId} {...source} />)}
        {!note.evidenceRefs?.length && !note.claimIds?.length && !excerpts.length && <p className="muted">此对象没有可用的文献来源记录。</p>}
      </> : <>
        <h2>让思考有出处</h2><p>阅读时点击文中的来源，在这里并排核查证据、原文与关联主张。</p>
        <div className="margin-rule" /><span className="margin-label">当前章节</span>
        <p>{WORKSPACE_STAGES.find(item => item.id === stage)?.description}</p>
        <p className="margin-footnote">AI 的建议供研究者判断。每一次选择、审核与生成，仍由你决定。</p>
      </>}
    </aside>
  </>;
}

function SourceExcerpt({ title, text, page, paperId, projectId, role, children }: { title: string; text: string; page: number; paperId: string; projectId: string; role?: string; children?: ReactNode }) {
  return <div className="margin-entry">
    {role && <span className="margin-label">{role}</span>}
    <h3>{title}</h3><span className="source-page">PDF 物理第 {page} 页</span>
    <blockquote>{text}</blockquote>
    <a className="source-original" href={`${getPaperFileUrl(projectId, paperId)}#page=${page}`} target="_blank" rel="noopener noreferrer">查看原始页 ↗</a>
    {children}
  </div>;
}

export function ManuscriptFrame({ project, evidence, claims, children }: { project: Project | null; evidence: Evidence[]; claims: Claim[]; children: ReactNode }) {
  const { stage, navigate } = useManuscript();
  const frame = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const shell = frame.current;
    if (!shell) return;
    const header = shell.querySelector<HTMLElement>(".manuscript-masthead");
    const heading = shell.querySelector<HTMLElement>(".project-heading");
    const measure = () => {
      const mastheadHeight = header?.getBoundingClientRect().height ?? 80;
      shell.style.setProperty("--masthead-height", `${mastheadHeight}px`);
      shell.style.setProperty("--manuscript-offset", `${mastheadHeight + (heading?.getBoundingClientRect().height ?? 0) + 24}px`);
    };
    const observer = new ResizeObserver(measure);
    if (header) observer.observe(header);
    if (heading) observer.observe(heading);
    measure();
    return () => observer.disconnect();
  }, [project?.id]);
  const current = WORKSPACE_STAGES.find(item => item.id === stage)!;
  function followAnchor(event: MouseEvent<HTMLDivElement>) {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const anchor = (event.target as HTMLElement).closest<HTMLAnchorElement>('a[href^="#"]');
    if (!anchor) return;
    const resolved = resolveWorkspaceLocation("", anchor.hash);
    const owner = resolved.anchor && stageForAnchor(resolved.anchor);
    if (owner) { event.preventDefault(); navigate(owner, resolved.anchor!); }
  }
  return <div className="manuscript-shell" ref={frame} onClick={followAnchor}>
    <a href="#manuscript-content" className="skip-link">跳到研究内容</a>
    <header className="manuscript-masthead"><AppBrand />
      <nav className="stage-navigation" aria-label="研究阶段">{WORKSPACE_STAGES.map(item => <a key={item.id} href={`?stage=${item.id}`} aria-current={stage === item.id ? "page" : undefined} onClick={event => {
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault(); navigate(item.id);
      }}>{item.label}</a>)}</nav><Link className="all-projects" href="/">所有项目</Link>
    </header>
    {project && <div className="project-heading"><Link href="/" className="back-link">研究项目 /</Link><h1 title={project.title}>{project.title}</h1>
      <details className="research-question"><summary><span>{project.research_question}</span><small><span className="when-collapsed">展开研究问题</span><span className="when-expanded">收起研究问题</span></small></summary><p>{project.research_question}</p><span className="margin-label">研究问题修订 {project.question_revision}</span></details>
    </div>}
    <div className="manuscript-layout">
      <nav className="chapter-navigation" aria-label={`${current.label}章节`}><span className="chapter-stage">{current.label}</span>{current.chapters.map(([id, label], index) => <a key={id} href={`#${id}`}><small>{String(index + 1).padStart(2, "0")}</small><span>{label}</span></a>)}</nav>
      <main id="manuscript-content" className="manuscript-content"><div className="stage-introduction"><span className="stage-kicker">{current.label}</span><p>{current.description}</p></div>{children}</main>
      <SourceAside projectId={project?.id ?? ""} evidence={evidence} claims={claims} />
    </div>
    <footer className="manuscript-footer"><span>溯理 SULI</span><span>从文献出发，让研究有据可循。</span></footer>
  </div>;
}
