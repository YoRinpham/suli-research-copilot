import type { Metadata } from "next";
import Link from "next/link";
import { AppBrand } from "../../components/manuscript";
import { WORKSPACE_STAGES, type WorkspaceStage } from "../../lib/workspace-navigation";

export const metadata: Metadata = {
  title: "使用指引 · 溯理 SULI",
  description: "从研究问题、原始文献与证据，到假设、实验和报告的操作指引。",
};

type GuideStep = { title: string; action: string; checkpoint: string; decision?: string };
const chapters: Record<WorkspaceStage, { introduction: string; steps: GuideStep[] }> = {
  literature: {
    introduction: "先留下问题，再找到可以回看的原始依据。",
    steps: [
      { title: "写下研究问题", action: "在首页点击「新建研究项目」，填写项目名称和研究问题，再创建项目。", checkpoint: "进入这份项目手稿，后续文献与证据都归于同一个问题。" },
      { title: "上传并索引论文", action: "在「文献」中选择 PDF，上传后逐篇点击「索引论文」，等待状态变为已索引。", checkpoint: "上传只保存原始文件；解析失败或文本质量不足时，先处理对应论文。" },
      { title: "检索候选片段", action: "输入想查找的问题，点击「检索论文」，阅读返回的原文片段并打开原始页核对。", checkpoint: "相关度表示问题与片段的匹配程度，不代表科学可信度。" },
      { title: "选入研究证据", action: "核对片段，选择证据类型并按需填写相关说明，再点击「保存为证据」；已保存证据可以排除或重新选择。", checkpoint: "证据保留完整原文、论文与 PDF 物理页，供后续推理追溯。", decision: "人工核查与选择" },
    ],
  },
  landscape: {
    introduction: "从一段段原文中提炼主张，再比较它们的关系。",
    steps: [
      { title: "生成并审核科学主张", action: "用已选证据生成科学主张，点击「核查来源」检查支持与质疑证据。先判断依据是否直接支持或属于推断，再单独选择主张。", checkpoint: "人工依据判断与「选择主张」是两步操作；符合当前资格的已选主张才能进入全景。", decision: "判断依据，再选择主张" },
      { title: "综合研究全景", action: "用已选主张生成研究全景，阅读摘要、方法与发现，检查冲突和不确定性，再决定接受或拒绝。", checkpoint: "只有人工接受且来源仍有效的全景，才能用于生成候选研究空白。", decision: "人工接受全景" },
    ],
  },
  directions: {
    introduction: "把文献中的未知，转化为可以继续检验的问题。",
    steps: [
      { title: "选择候选研究空白", action: "根据已接受的全景生成空白，核对来源、研究价值和前提，再点击「选择空白」。", checkpoint: "空白只针对已提供的文献，不表示它已经获得整个领域的新颖性证明。", decision: "人工选择研究方向" },
      { title: "选择可检验假设", action: "基于已选空白生成假设，检查比较因素、结果量、控制条件与证伪条件，再选择要研究的假设。", checkpoint: "提出假设的文献依据属于启发证据，不表示假设已经成立。", decision: "人工选择假设" },
    ],
  },
  experiment: {
    introduction: "让假设落到明确的研究设计和可核对的数据上。",
    steps: [
      { title: "审核实验计划", action: "为已选假设生成实验计划，在「实验」阶段检查实验组、基线、数据条件、指标、过程与证伪标准，再接受或拒绝。", checkpoint: "接受计划只表示认可研究设计；系统不会自动执行真实实验。", decision: "人工接受计划" },
      { title: "上传数据并批准分析", action: "为当前已接受的计划上传 UTF-8 CSV，检查数据画像。需要清洗时先审查并批准方案；也可以直接使用原始数据版本。再选择或请求分析建议，人工批准后执行统计。", checkpoint: "清洗与分析都需要显式确认；计算结果来自代码，原始数据与版本记录保留。", decision: "人工批准清洗与分析" },
      { title: "查看图表并审核解释", action: "根据真实数据选择图表类型、列和关联分析，生成图表；请求 AI 解释后，核对统计值、局限与其他解释，再接受或拒绝。", checkpoint: "接受解释不表示假设已被证明。报告使用当前人工接受的结果解释。", decision: "人工审核结果解释" },
    ],
  },
  report: {
    introduction: "把过程整理成手稿，也把每一步的来处留下。",
    steps: [
      { title: "组装并核查草稿", action: "选择当前已接受的结果解释，生成报告草稿，或先预览确定性内容。核对文献来源、原始页、统计值和图表。", checkpoint: "草稿需要人工审核。修改文本会创建新版本，并重新进入待审核状态。" },
      { title: "接受并导出报告", action: "核查后人工接受报告，再导出 Markdown 或查看、打印 HTML。历史报告仍可阅读，草稿导出会标明身份。", checkpoint: "正式导出要求报告已接受、未被替代且来源仍有效；来源过期时应重新核查并生成、审核当前版本。", decision: "人工接受，正式导出" },
    ],
  },
};

export default function GuidePage() {
  return <main className="home-shell guide-shell">
    <a className="skip-link" href="#guide-workflow">跳到使用流程</a>
    <header className="manuscript-masthead"><AppBrand /><nav className="home-links" aria-label="指引导航"><Link href="/guide" aria-current="page">使用指引</Link><Link href="/">返回首页</Link></nav></header>
    <section className="guide-intro">
      <p className="section-label">使用指引</p><h1>把研究，一步步展开。</h1>
      <p>沿着下面的流程，从原始文献走向可检验的假设与研究报告。点击阶段名称，可以直接阅读对应的操作。</p>
    </section>
    <nav className="guide-overview" aria-label="完整研究流程"><ol>{WORKSPACE_STAGES.map((stage, index) => <li key={stage.id}>
      <a href={`#guide-${stage.id}`}><span className="guide-overview-index">{String(index + 1).padStart(2, "0")}</span><strong>{stage.label}</strong><span>{chapters[stage.id].steps.length} 个操作节点</span></a>
      {index < WORKSPACE_STAGES.length - 1 && <span className="guide-overview-arrow" aria-hidden="true">→</span>}
    </li>)}</ol></nav>
    <p className="guide-reading-note">箭头表示操作顺序。每一步的生成、选择、审核与执行，都由你明确触发；可以随时回到前面的阶段核查来源。</p>
    <div className="guide-workflow" id="guide-workflow">{WORKSPACE_STAGES.map((stage, index) => <section className="guide-phase" id={`guide-${stage.id}`} key={stage.id} aria-labelledby={`guide-${stage.id}-title`}>
      <header className="guide-phase-heading"><span className="guide-phase-number">{String(index + 1).padStart(2, "0")}</span><h2 id={`guide-${stage.id}-title`}>{stage.label}</h2><p>{chapters[stage.id].introduction}</p></header>
      <ol className="guide-steps" aria-label={`${stage.label}操作链`}>{chapters[stage.id].steps.map((step, stepIndex) => <li key={step.title}>
        <span className="guide-step-node" aria-hidden="true">{index + 1}.{stepIndex + 1}</span>
        <div className="guide-step-body"><h3>{step.title}</h3><p>{step.action}</p><p className="guide-checkpoint">{step.checkpoint}</p>{step.decision && <span className="guide-decision">{step.decision}</span>}</div>
      </li>)}</ol>
    </section>)}</div>
    <section className="guide-closing"><div><h2>准备好写下第一个问题了吗？</h2><p>从一份项目手稿开始，逐步留下你的文献、证据与判断。</p></div><Link className="primary-button" href="/">回到项目目录</Link></section>
    <footer className="manuscript-footer"><span>溯理 SULI</span><span>文献有来源，思考有脉络。</span></footer>
  </main>;
}
