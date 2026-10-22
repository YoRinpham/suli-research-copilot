"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";

import { createProject, listProjects, type Project } from "../lib/api/projects";
import { AppBrand } from "../components/manuscript";
import { ResearchVignette } from "../components/research-vignette";

export default function HomePage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [title, setTitle] = useState("");
  const [question, setQuestion] = useState("");
  const [creating, setCreating] = useState(false);
  const createOpen = creating || (!loading && !error && projects.length === 0);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setProjects(await listProjects());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "项目加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setCreating(true);
    if (!title.trim() || !question.trim()) {
      setError("请填写项目名称和研究问题。");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const created = await createProject({ title: title.trim(), research_question: question.trim() });
      setProjects((current) => [...current, created]);
      setTitle("");
      setQuestion("");
      setCreating(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "创建项目失败");
    } finally {
      setSaving(false);
    }
  }

  return (
    <main className="home-shell">
      <a className="skip-link" href="#projects-title">跳到研究项目</a>
      <header className="manuscript-masthead"><AppBrand showLeaf={false} /><nav className="home-links" aria-label="首页导航"><Link href="/guide">使用指引</Link><span className="home-edition">我的研究手稿</span></nav></header>
      <section className="home-intro">
        <div className="home-intro-copy"><p className="section-label" lang="zh-Hant">從一個值得追問的問題開始</p>
        <h1 lang="zh-Hant">每一項研究，<br />都有它的來處。</h1>
        <button className="primary-button" type="button" aria-expanded={createOpen} aria-controls="create-project-panel" onClick={() => { setCreating(value => !value); }}>＋ 新建研究项目</button></div>
        <ResearchVignette />
      </section>
      {error && <div className="alert" role="alert">{error} <button type="button" onClick={() => void refresh()}>重新加载</button></div>}
      <div className={`home-workspace ${createOpen ? "with-create" : ""}`}>
        <section className="workspace-section project-directory" aria-labelledby="projects-title">
          <div className="section-heading directory-heading"><h2 id="projects-title">研究中的问题</h2><span>{projects.length} 份手稿</span></div>
          {loading ? <p className="muted" role="status">正在打开研究目录…</p> : projects.length === 0 ? <div className="empty"><strong>把第一个问题写下来。</strong><p>为研究命名，记录你想探索的问题，再从原始文献开始。</p></div> : (
            <ul className="project-list">{[...projects].sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at)).map((project) => (
              <li key={project.id}><Link href={`/projects/${project.id}`}><div className="project-list-heading"><strong title={project.title}>{project.title}</strong><span aria-hidden="true">↗</span></div><span className="project-question">{project.research_question}</span><small>修订 {project.question_revision}<span>最近更新 {Number.isNaN(Date.parse(project.updated_at)) ? "暂不可用" : new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "long", day: "numeric", timeZone: "Asia/Shanghai" }).format(new Date(project.updated_at))}</span></small></Link></li>
            ))}</ul>
          )}
        </section>
        <section id="create-project-panel" className="workspace-section create-panel" aria-labelledby="create-title" hidden={!createOpen}>
          <div className="section-heading"><h2 id="create-title">开始一份研究手稿</h2>{projects.length > 0 && <button className="text-button" type="button" onClick={() => setCreating(false)}>收起</button>}</div>
          <form onSubmit={(event) => void submit(event)}>
            <label htmlFor="project-title">项目名称</label>
            <input id="project-title" value={title} onChange={(event) => setTitle(event.target.value)} maxLength={255} placeholder="例如：电池材料稳定性" required />
            <label htmlFor="research-question">研究问题</label>
            <textarea id="research-question" value={question} onChange={(event) => setQuestion(event.target.value)} rows={5} maxLength={10000} placeholder="明确你希望从文献中探索的问题" required />
            <button className="primary-button" type="submit" disabled={saving}>{saving ? "正在创建…" : "创建项目"}</button>
          </form>
        </section>
      </div>
      <footer className="manuscript-footer"><span>溯理 SULI</span><span>文献有来源，思考有脉络。</span></footer>
    </main>
  );
}
