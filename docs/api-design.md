# 溯理 SULI MVP API 设计

> Status: Accepted for MVP implementation
> Version: 1.0

产品展示名称为「溯理 SULI」，API 文档标题为「溯理 SULI API」。此次命名同步不改变端点、JSON 字段、测量模式 `research_copilot` 或现有数据契约。

## 1. 通用约定

- 基础路径 `/api/v1`，JSON 使用 `snake_case` 字段；上传使用 `multipart/form-data`，PDF 下载使用 `application/pdf`。
- ID 为服务端生成的不透明字符串。客户端和 Qwen 只能引用已返回的 ID，不构造 ID。时间为 ISO 8601 UTC。页码从 1 开始，表示 PDF 阅读器中的物理页序，不保证等于论文印刷页码。
- 所有项目下资源按 `project_id` 隔离；跨项目 ID、过期修订号和不存在的引用不能参与生成。列表查询按项目限定。生成内容及其来源关系创建后不改写；用户编辑产生带 `supersedes_id` 的新对象。Claim 的 `eligible_for_generation` 与 Landscape 的 `eligible_for_downstream` 分别由当前修订、是否被替代、来源有效性和人工审核状态计算，不靠级联写入 `stale` 字段。历史对象和原出处仍可查看。
- 普通创建返回 `201`，生成操作返回 `201` 并包含持久化结果；查询/更新返回 `200`。逐篇索引为有界同步操作，成功返回 `200`。无隐式后台作业语义。
- 生成端点只在前端用户显式操作后调用。API 不允许一次请求自动从 Gap 连续推进至实验计划。
- `X-Request-ID` 随请求或由服务端生成并在响应回显，供错误定位。项目编辑和选择使用短事务；生成按架构文档的 GenerationRun 分阶段短事务处理，并检查当前修订号。

### 1.1 共用枚举与校验

| 字段 | 取值与含义 |
|---|---|
| `parse_status` | `uploaded`, `parsing`, `parsed`, `embedding`, `parse_failed`, `low_quality`, `embedding_failed`, `indexed`。`parsed` 表示 PDF 文本抽取与 PaperChunk 持久化已完成；正常路径为 `uploaded` → `parsing` → `parsed` → `embedding` → `indexed`。`embedding` 有内部 `embedding_started_at`，未过期时拒绝重入，超时后可重新认领；NULL 时间戳不视作过期。`low_quality` 不进入检索，需由用户更换 PDF。 |
| `selection_status` | Evidence：`selected`, `rejected`；Claim、Gap、Hypothesis：`candidate`, `selected`, `rejected`。Landscape 与 ExperimentPlan 无此字段。 |
| `human_grounding_status` | `pending_review`, `supported`, `inferred`, `unsupported`, `insufficient_evidence`。只有人工确认的前两种有效状态且带真实来源时可供后续推理。AI 的 `ai_grounding_assessment` 单独返回，不授予权限。 |
| `ai_grounding_assessment` | `directly_supported`, `inferentially_supported`, `unsupported`, `contradicted`, `insufficient_evidence`；旧记录及人工修订可为 `null`。 |
| `ResearchLandscape.review_status` | `pending_review`, `accepted`, `rejected`；初始为 `pending_review`，与 Claim 的选择状态不同。 |
| `evidence_role` | `supports`, `challenges`。同一 Claim 与 Evidence 只允许一个角色。 |
| `hypothesis_evidence_role` | `motivates`, `challenges`。`motivates` 表示提出假设的依据，不表示假设已经得到证实。 |
| `GenerationRun.stage` | `claim`, `landscape`, `gap`, `hypothesis`, `experiment_plan`。 |
| `GenerationRun.status` | `started`, `succeeded`, `failed`；有限重试合并为同一运行并增加 `attempt_count`。 |
| `evidence_type` | `reported_result`, `method_description`, `dataset_detail`, `limitation`, `author_claim`, `other`；类型描述片段内容，不等于可信度。 |
| `claim_type` | `finding`, `method`, `comparison`, `limitation`, `other`。 |
| `LandscapeItem.category` | `approach`, `method`, `dataset`, `metric`, `finding`, `conflict`。 |
| `gap_type` | `contradictory_findings`, `underexplored_variable`, `missing_comparison`, `limited_dataset`, `insufficient_replication`, `methodological_limitation`, `untested_interaction`, `unexplored_domain`, `unresolved_cause`, `evaluation_inconsistency`。 |

所有非空科学文本有长度上限；具体数值在实现前随配置和 schema 固定。关键结构字段缺失、空字符串、非法枚举或虚构来源 ID 均为校验失败。`confidence` 使用 `low / medium / high / unknown` 这类定性值，避免暗示统计概率。`uncertainty` 为必须给出的文字解释或明确的 `none_identified`，不得空白。

### 1.2 错误响应

隔离提案 `m3-v10 / m3-landscape-v9` 保留公开 provider 判断 schema、成功 API 对象和历史读取。错误码及原 numeric allocation 字段保持；assessment 错误另返回固定 `assessment_encoding_version=assessment-context-v1`。`measured_size` 和 `current_candidate_chars` 度量实际固定列表示的 Python `len(str(payload))`，不能与 v8 verbose 数字直接比较。stored canonical 判断及全文页码来源均不编码。详见 [版本化表示契约](landscape-assessment-representation-2026-10-12.md)；此提案尚未部署。

```json
{
  "error": {
    "code": "insufficient_evidence",
    "message": "当前已选证据不足以生成候选研究空白",
    "stage": "gap_generation",
    "request_id": "opaque-request-id",
    "details": {}
  }
}
```

| HTTP | `code` | 含义 |
|---|---|---|
| 400 | `invalid_pdf`, `parse_failed` | 文件类型/内容不合要求，或本次解析失败。 |
| 404 | `not_found` | 项目或项目内资源不存在。 |
| 409 | `stale_revision`, `invalid_state`, `superseded_resource`, `referenced_resource`, `generation_in_progress`, `generation_superseded`, `report_already_exists` | 当前问题修订不符、前置状态不满足、输入已被新版本替代、试图修改被引用原文，或报告 Run 已活动、失去提交资格、已有当前报告。 |
| 413 | `file_too_large` | 超过可配置的上传大小/页数上限。 |
| 422 | `validation_error`, `invalid_provenance`, `invalid_reference` | 请求结构、原文匹配或来源 ID 校验失败。 |
| 424 | `no_relevant_chunks`, `insufficient_evidence` | 上游输入不足；没有伪造的成功结果。 |
| 502 | `embedding_failed`, `llm_failed`, `structured_output_invalid` | 外部 provider 或模型结构输出失败。 |
| 504 | `provider_timeout`, `report_deadline_expired` | 外部 provider 的网络空闲/绝对尝试期限，或报告整体提交期限到期。Run 的 `error_type` 区分 `provider_total_timeout` 与网络阶段超时。 |
| 500 | `database_failed`, `internal_error` | 内部故障，响应不暴露栈或密钥。 |
| 503 | `database_failed` | 健康检查确认数据库不可用；响应包含 `X-Request-ID` 和通用错误结构。 |

上传、索引或生成失败不提交半成品科学对象。索引失败时 Paper 保留明确状态；已成功保存的 PDF/Chunk 不因 LLM 失败消失。输入预检查失败不创建 GenerationRun；通过预检查后，先用独立短事务提交 `started` Run，再在没有开放数据库事务或连接的情况下调用 provider。成功时整批科学对象、来源关系与 Run 的 `succeeded` 状态原子提交；provider 或返回结果校验失败时，单独提交 Run 的 `failed` 状态与有限错误信息，不留下部分科学对象。如果已有 Run，失败响应的 `error.details.generation_run_id` 用于定位。结构化输出校验失败时只记录有限诊断信息，不向客户端暴露供应商原始私有上下文。

## 2. 资源字段与来源表示

| 资源 | 响应必需字段 |
|---|---|
| `Project` | `id`, `title`, `research_question`, `question_revision`, `status`, `created_at`, `updated_at` |
| `Paper` | `id`, `project_id`, `title`（可空）, `source_filename`, `parse_status`, `quality_note`（可空）, `page_count`（可空）, `created_at`；内部保存 `parse_started_at` 和 `embedding_started_at` 用于中断恢复，不作为公开响应字段。 |
| `RetrievalResult` | `chunk_id`, `paper_id`, `paper_title`（可空）, `page_number`, `section`（可空）, `text`, `similarity_score` |
| `Evidence` | `id`, `project_id`, `paper_chunk_id`, `paper_id`, `paper_title`（可空）, `source_filename`, `page_number`, `section`（可空）, `source_text`, `evidence_type`, `relevance_note`, `selection_status`, `question_revision`, `paper_file_url` |
| `Claim` | `id`, `project_id`, `text`, `claim_type`, `legacy_grounding_status?`, `ai_grounding_assessment?`, `ai_risk_flags?`, `ai_justification?`, `ai_evidence_refs?`, `ai_support_excerpts?`, `human_grounding_status`, `human_reviewed_at?`, `confidence`, `uncertainty`, `evidence_for_ids[]`, `evidence_against_ids[]`, `selection_status`, `question_revision`, `supersedes_id?`, `generation_run_id?`, `origin`, `eligible_for_generation` |
| `ResearchLandscape` | `id`, `project_id`, `summary`, `uncertainty`, `ai_summary_assessments?`, `review_status`, `reviewed_at?`, `items[]`, `question_revision`, `supersedes_id?`, `generation_run_id?`, `origin`, `eligible_for_downstream`, `created_at` |
| `LandscapeItem` | `id`, `category`, `text`, `uncertainty`, `claim_ids[]`, `evidence_ids[]`, `ai_conflict_assessment?` |
| `ResearchGap` | `id`, `project_id`, `landscape_id`, `title`, `description`, `gap_type`, `why_it_matters`, `uncertainty`, `assumptions[]`, `novelty_scope = supplied_literature`, `landscape_item_ids[]`, `claim_ids[]`, `evidence_for_ids[]`, `evidence_against_ids[]`, `ai_assessment?`, `selection_status`, `question_revision`, `supersedes_id?`, `generation_run_id?`, `origin`, `eligible_for_generation` |
| `Hypothesis` | `id`, `project_id`, `research_gap_id`, `statement`, `rationale`, `independent_variable`, `dependent_variable`, `control_variables[]`, `falsification_condition`, `assumptions[]`, `evidence_motivating_ids[]`, `evidence_challenging_ids[]`, `claim_ids[]`, `selection_status`, `question_revision`, `supersedes_id?`, `generation_run_id?`, `origin`, `eligible_for_generation` |
| `ExperimentPlan` | `id`, `hypothesis_id`, `objective`, `independent_variable`, `dependent_variable`, `control_variables[]`, `experimental_groups[]`, `baseline`, `dataset_requirements`, `metrics[]`, `procedure`, `analysis_approach`, `expected_observations[]`, `falsification_criteria`, `confounders[]`, `limitations[]`, `assumptions[]`, `uncertainty`, `ai_alignment_assessment?`, `review_status`, `reviewed_at?`, `question_revision`, `supersedes_id?`, `generation_run_id?`, `origin`, `eligible_for_downstream` |
| `GenerationRun` | `id`, `project_id`, `stage`, `status`, `provider`, `model`, `prompt_name`, `prompt_version`, `prompt_hash`, `schema_version`, `research_question_snapshot`, `question_revision`, `input_manifest[]`, `input_digest`, `attempt_count`, `error_type?`, `created_at`, `completed_at?` |

`Evidence.paper_id`、标题、页码、章节和 `paper_file_url` 是从固定的 Chunk/Paper 关系展开的只读响应字段，不由用户或模型回写。`paper_file_url` 指向 PDF 原文件；前端根据 `page_number` 打开对应页。前端只提交非空 `source_text`；后端要求它是所选 Chunk 文本的精确子串，并可在内部记录首次匹配位置。`similarity_score` 为余弦相似度，只用于候选排序，不表示科学可信度。科学条目显示其证据 ID；没有证据的解释性条目需显式标为推断或不充分。

`origin` 为 `model` 或 `human_revision`。模型创建的对象必须指向同项目、同阶段且状态为 `succeeded` 的 `generation_run_id`；人工修订对象以非空 `supersedes_id` 指向上一版本，`generation_run_id` 必须为空。GenerationRun 的 `input_manifest[]` 只包含已校验的对象类型、ID、内容摘要和调用时选择状态；其研究问题快照、Prompt 版本/摘要和模型名用于追查输入，不保存整篇 PDF、密钥或模型原始输出。ExperimentPlan 无 `selection_status`，但有独立的 `review_status` 和只读 `eligible_for_downstream`；仅人工接受的当前版本具备未来 M5 下游资格。

Landscape `m3-landscape-v4` 的 provider 核查只返回科学判断和授权 Evidence ID，不返回引文。每个条目的 `ai_conflict_assessment` 同时保留独立 `support_verdict`、`flagged_statements`、`qualifications` 及原冲突维度；summary 独立核查。新评估的 `support_excerpts` 由服务端绑定完整 Evidence 原文，`source_binding_origin=server_bound_canonical_source_text` 标识其来源，附版本/source hash/现有物理页出处。原有 API 路径和响应 JSON 容器兼容；历史评估不回填、不改变原引文。有效 ID 与真实原文不能替代科学支持判断或人工接受；未知/错误引用和未支持命题仍按受限修复策略失败，不保存半成品。

M3 模型生成 Claim 的结构和来源由服务端验证；AI 语义核查只返回 verdict、风险标记、理由及 Evidence ID。`ai_support_excerpts` 仍单独保存在 Claim 响应和数据库中，但只由服务端从已选且经最终复核的 Evidence 原文生成，核查模型不返回摘录。`human_grounding_status` 初始为 `pending_review`，AI 的 `directly_supported` 也不能改变它。研究者明确判断依据并选择 Claim 后才可进入 Landscape。模型候选 `conflict` 须核查可比性：任务、指标和实验条件均需明确可比，数据集或人群至少一项明确可比；`different` 必拒绝，未知不可充当可比。直接冲突至少关联两条 Claim；若 AI 把单条 Claim 的普通发现误判为研究间冲突，服务端记录原 AI 判断及 `server_direct_conflict_role=false`，不把单条发现当作直接冲突。所有条目须校验文字是否仍主张直接冲突，禁止通过改类别保留被否定的冲突断言。Landscape summary 须由 AI 评估重大科学断言，但评估通过后仍为 `pending_review`，只有人工接受才具备下游资格。`GenerationRun.prompt_hash` 是静态 Prompt bundle 摘要；`attempt_count` 累加实际 provider 调用。人工 Claim 修订新版本也从待审核开始。`supported` 只表示人确认其受当前语料支持，不表示普遍科学真理。

生成对象的 `/revisions` 和 `/selection` 操作只接受当前问题修订下尚未被替代的版本；Evidence 的 `/selection` 只接受当前问题修订。同一生成版本只允许一个直接后继，重复修订或并发修订返回 409 `superseded_resource`。这些限制不影响旧版本的只读访问。

## 3. 项目、论文、索引与检索

| 方法与路径 | 请求关键字段 | 成功响应 | 约束 |
|---|---|---|---|
| `POST /projects` | `title`, `research_question` | `201 Project`，初始修订号 `1` | 两字段非空。 |
| `GET /projects` | 无 | `200 Project[]` | MVP 单用户工作区。 |
| `GET /projects/{project_id}` | 无 | `200 Project` | 展示当前问题与修订。 |
| `PATCH /projects/{project_id}` | `title?`, `research_question?`, `expected_question_revision` | `200 Project` | 问题文字变化时修订号递增；旧产物保持原样，新一轮生成不能使用旧修订；并发版本冲突为 409。 |
| `POST /projects/{project_id}/papers` | multipart `file` | `201 Paper`，状态 `uploaded` | PDF 签名、扩展名、大小和页数上限校验；保留原文件。 |
| `GET /projects/{project_id}/papers` | 无 | `200 Paper[]` | 含解析/嵌入状态及质量说明。 |
| `GET /projects/{project_id}/papers/{paper_id}` | 无 | `200 Paper` | 只返回同项目论文。 |
| `POST /projects/{project_id}/papers/{paper_id}/index` | 空体 | `200 Paper`，状态 `indexed` | 按页解析、切块并嵌入；失败保留状态并可在修复外部故障后重试。已有下游引用的 Chunk 不可覆盖。 |
| `GET /projects/{project_id}/papers/{paper_id}/file` | 无 | `200 application/pdf` | 供证据页跳转；只允许受控存储键。 |
| `POST /projects/{project_id}/retrievals` | `query?`, `top_k?`, `expected_question_revision` | `200 {query, results: RetrievalResult[]}` | 直接返回候选片段；缺省 query 为当前研究问题，仅在已索引 Chunk 中检索。无命中返回 424 `no_relevant_chunks`；结果不持久化为 Evidence。 |

M1 仅在服务层完成解析和单页切块，停在 `parsed`；公开的 `/index` 契约包含嵌入和 `indexed` 结果，端点由 M2 实现。`embedding` 是 M2 增加的最小状态澄清。索引仅在短事务内认领和保存结果；外部嵌入调用时不持有数据库事务。成功时整批向量及 `indexed` 状态原子提交，失败则保留 Chunk 并设为 `embedding_failed`。

索引重复调用仅在已索引 Paper 的所有 Chunk 均有当前模型 1024 维向量时直接返回；旧模型 Paper 复用已有 Chunk 重嵌入。若 `embedding_failed`，可利用已有有效 Chunk 重试嵌入。若解析阶段失败或低质量，需重新上传新 Paper；被引用 Paper 不进行就地重解析。检索 `top_k` 取配置默认值并设上限，不让客户端触发无界返回。所有过滤先按项目与可用索引状态进行。`RetrievalResult` 不是持久化的独立 API 资源；前端可暂存这次返回的候选结果供检查，只有用户明确选中的片段才通过 Evidence 接口持久化。

M2 使用 DashScope 原生 `qwen3.7-text-embedding` 的 1024 维稠密向量：文档请求 `text_type=document`，查询请求 `text_type=query` 并附上集中定义的科研论文段落检索指令（见 `backend/app/providers/embeddings.py`）；两者均指定 `dimension=1024`、`output_type=dense`。检索只比较当前配置模型标识与维度完全一致的向量；旧 `text-embedding-v4` 向量即使同维也不兼容。`/index` 对全部 Chunk 已属当前嵌入空间的 Paper 幂等；旧模型的 `indexed` Paper 可在不重传 PDF、不重建 Chunk 的条件下进入 `embedding`，成功时原子替换完整向量批，失败时保持原 Chunk/Evidence 来源并进入 `embedding_failed`。维度改变必须另行数据库迁移和重建；1024 与 2560 的质量比较留待后续基准。检索使用 pgvector 精确余弦 Top-K，`similarity_score = 1 - cosine_distance`，仅表示相关度。`top_k` 默认 8，上限默认 20；无合格的已索引同模型 Chunk 时返回 424，不设置隐含相似度阈值。

## 4. Evidence 与 Claim

| 方法与路径 | 请求关键字段 | 成功响应 | 约束 |
|---|---|---|---|
| `POST /projects/{project_id}/evidence` | `paper_chunk_id`, `source_text`, `evidence_type`, `relevance_note?`, `expected_question_revision` | `201 Evidence` | 必须由用户选择；后端检查原文是 Chunk 的精确子串并自行定位，项目/页关联由后端展开。 |
| `GET /projects/{project_id}/evidence` | `selection_status?` | `200 Evidence[]` | 供研究者核查及选入。 |
| `GET /projects/{project_id}/evidence/{evidence_id}` | 无 | `200 Evidence` | 包含 PDF 页定位信息。 |
| `PATCH /projects/{project_id}/evidence/{evidence_id}/selection` | `selection_status`, `expected_question_revision` | `200 Evidence` | 只改变用户选择；原文和说明保持不变，旧下游对象不改写，撤销选择会阻止其用于新生成。 |
| `POST /projects/{project_id}/claims/generate` | `evidence_ids[]`, `expected_question_revision` | `201 {generation_run_id, claims: Claim[]}` | ID 必须为当前已选 Evidence；空或不足时返回 424。只保存通过结构及引用校验的结果。 |
| `GET /projects/{project_id}/claims` | `selection_status?` | `200 Claim[]` | 每条含支持与质疑 Evidence ID。 |
| `GET /projects/{project_id}/claims/{claim_id}` | 无 | `200 Claim` | 支持来源跳转。 |
| `PATCH /projects/{project_id}/claims/{claim_id}/selection` | `selection_status`, `expected_question_revision` | `200 Claim` | Claim 还需人工确认依据为 `supported` 或 `inferred`；选择变更不改写已有 Landscape。 |
| `PATCH /projects/{project_id}/claims/{claim_id}/grounding-review` | `human_grounding_status`, `expected_question_revision` | `200 Claim` | 明确记录研究者的依据判断；`supported` 需正向 Evidence，不自动选择 Claim。 |
| `POST /projects/{project_id}/claims/{claim_id}/revisions` | 完整新 `text`, `claim_type`, `confidence`, `uncertainty`, `evidence_for_ids[]`, `evidence_against_ids[]`, `expected_question_revision` | `201 Claim` | 新对象初始 `candidate` 且人工依据审核 `pending_review`；旧对象及来源保持不变。 |

Evidence 选择 PATCH 对不存在或不属于请求项目的 Evidence 返回 `404 not_found`；同项目 Evidence 已存在但问题修订或业务状态不允许操作时返回 409。错误响应保留结构化格式与请求 ID。

模型提议的 `Claim` 初始为 `candidate` 且人工依据审核为 `pending_review`；研究者必须分别作出依据判断和选择。人工修订 Claim 的新版本也需重新判断并选择。被替代版本不能作为新输入或再次更改选择状态；旧 Landscape 若引用旧 Claim，继续作为历史结果展示，不级联修改。

## 5. Landscape、Gap、Hypothesis 与 ExperimentPlan

| 方法与路径 | 请求关键字段 | 成功响应 | 约束 |
|---|---|---|---|
| `POST /projects/{project_id}/landscapes/generate` | `claim_ids[]`, `expected_question_revision` | `201 {generation_run_id, landscape: ResearchLandscape}` | Claim 均属当前项目和修订、未被替代、已人工确认依据且为 `selected`；重要条目须有 Claim/Evidence 引用，冲突保留来源。生成结果为 `pending_review`。 |
| `GET /projects/{project_id}/landscapes` | 无 | `200 ResearchLandscape[]` | 历史快照可见，旧版本与当前版本通过 `supersedes_id` 区分。 |
| `GET /projects/{project_id}/landscapes/{landscape_id}` | 无 | `200 ResearchLandscape` | 条目包含来源 ID。 |
| `PATCH /projects/{project_id}/landscapes/{landscape_id}/review` | `review_status` (`accepted` 或 `rejected`), `expected_question_revision` | `200 ResearchLandscape` | 人工接受或拒绝工作性综合；仅已接受且上游有效的 Landscape 具备后续资格。 |
| `POST /projects/{project_id}/landscapes/{landscape_id}/revisions` | 完整新 `summary`, `uncertainty`, `items[]`, `expected_question_revision` | `201 ResearchLandscape` | 重新校验条目及来源并创建新版本；旧 Landscape 和 Gap 保留，旧版本不用于新生成。 |
| `POST /projects/{project_id}/gaps/generate` | `landscape_id`, `expected_question_revision` | `201 {generation_run_id, gaps: ResearchGap[]}` | 指定当前可用的 Landscape；所有 Gap 仅宣称在上传文献内的候选空白，且每条至少关联一条当前项目、当前来源上下文中的支持 Evidence；质疑 Evidence 可为空。无有效支持来源的候选不可持久化。 |
| `GET /projects/{project_id}/gaps` | 无 | `200 ResearchGap[]` | 含支持/质疑 Evidence。 |
| `GET /projects/{project_id}/gaps/{gap_id}` | 无 | `200 ResearchGap` | 可展示依据与假设。 |
| `PATCH /projects/{project_id}/gaps/{gap_id}/selection` | `selection_status`, `expected_question_revision` | `200 ResearchGap` | Gap 必须由用户选为 `selected` 才可生成 Hypothesis。 |
| `POST /projects/{project_id}/gaps/{gap_id}/revisions` | 完整新 `title`, `description`, `gap_type`, `why_it_matters`, `uncertainty`, `assumptions[]`, `novelty_scope`, `landscape_item_ids[]`, `claim_ids[]`, `evidence_for_ids[]`, `evidence_against_ids[]`, `expected_question_revision` | `201 ResearchGap` | 新版本为 `candidate`；`evidence_for_ids` 至少一条且须通过项目与来源关系校验，`evidence_against_ids` 可为空；保留原版本及其下游引用。 |
| `POST /projects/{project_id}/gaps/{gap_id}/hypotheses/generate` | `count?`, `expected_question_revision` | `201 {generation_run_id, hypotheses: Hypothesis[]}` | Gap 必须为当前未被替代的 `selected` 版本；`count` 默认 3、允许 1–5，是有效候选数的请求目标及上限，不保证恰好返回该数量；可返回较少但至少 1 条，0 条按现有无效结构输出处理，绝不超过请求数量。各条须有非空证伪条件并附 AI 可证伪性评估，科学有效性仍由人判断。 |
| `GET /projects/{project_id}/hypotheses` | `gap_id?` | `200 Hypothesis[]` | 按项目查看候选。 |
| `GET /projects/{project_id}/hypotheses/{hypothesis_id}` | 无 | `200 Hypothesis` | 可向 Gap 和 Evidence 回溯。 |
| `PATCH /projects/{project_id}/hypotheses/{hypothesis_id}/selection` | `selection_status`, `expected_question_revision` | `200 Hypothesis` | 选择变更不修改已有实验计划。 |
| `POST /projects/{project_id}/hypotheses/{hypothesis_id}/revisions` | 完整新 `statement`, `rationale`, `independent_variable`, `dependent_variable`, `control_variables[]`, `falsification_condition`, `assumptions[]`, `evidence_motivating_ids[]`, `evidence_challenging_ids[]`, `claim_ids[]`, `expected_question_revision` | `201 Hypothesis` | 新版本为 `candidate`；`motivates` 只说明假设来源，证伪条件必须非空。 |
| `POST /projects/{project_id}/hypotheses/{hypothesis_id}/experiment-plans/generate` | `expected_question_revision` | `201 {generation_run_id, plan: ExperimentPlan}` | Hypothesis 必须为当前未被替代的 `selected` 版本；基线、指标、证伪标准必须存在。 |
| `GET /projects/{project_id}/experiment-plans` | `hypothesis_id?` | `200 ExperimentPlan[]` | 计划经 Hypothesis 回溯 Gap/Evidence。 |
| `GET /projects/{project_id}/experiment-plans/{plan_id}` | 无 | `200 ExperimentPlan` | 明确标识初始建议。 |
| `PATCH /projects/{project_id}/experiment-plans/{plan_id}/review` | `review_status` (`accepted` 或 `rejected`), `expected_question_revision` | `200 ExperimentPlan` | 人工审核；接受仅表示决定采用计划，不表示实验已执行或结果有效。 |
| `POST /projects/{project_id}/experiment-plans/{plan_id}/revisions` | 完整新 `objective`, `independent_variable`, `dependent_variable`, `control_variables[]`, `experimental_groups[]`, `baseline`, `dataset_requirements`, `metrics[]`, `procedure`, `analysis_approach`, `expected_observations[]`, `falsification_criteria`, `confounders[]`, `limitations[]`, `assumptions[]`, `uncertainty`, `expected_question_revision` | `201 ExperimentPlan` | 创建内容与来源固定的新版本并回到 `pending_review`，不表示实验已执行。 |
| `GET /projects/{project_id}/generation-runs/{run_id}` | 无 | `200 GenerationRun` | 仅显示模型/Prompt/输入 lineage 和有限诊断，不返回密钥、全文或原始模型输出。 |

`gaps/generate` 只能引用当前可用 Landscape 所使用的来源。Hypothesis 的 Evidence/Claim 引用必须可从所选 Gap 的来源关系解析，不能转向其他项目；其正向 Evidence 字段为 `evidence_motivating_ids[]`。ExperimentPlan 以所选 Hypothesis 为输入，必须包含变量等结构字段；AI 对语义对齐给出建议，研究者在接受前核查，修订计划时保留假设链接。所有生成接口按一次成功事务保存一批结果，批内有一项关键字段或引用无效则整批失败。GenerationRun 可记录失败，但不得留下半成品科学对象。

M4 正常修订界面可编辑 Gap 的前提、Hypothesis 的控制条件与前提，以及 Plan 的全部科学内容（含变量、比较参照、数据要求、实验组、指标、预期观察、混杂因素、局限和前提）。列表逐行编辑，允许为空的列表可清空；实验组和指标至少一项。来源关联和父对象链接保持原版本的关系，由已有服务端契约重新校验。保存仍创建待选择/待审核新版本，不继承人工接受或选择状态。上游被修订时须从该新版本重走后续阶段，不能将旧 Hypothesis/Plan 重新绑定到新父对象。

## 6. 前端演示调用顺序与验收

```text
POST project → POST/PATCH research question
→ POST paper × N → POST index × N → POST retrievals
→ 检查返回的 RetrievalResult[] 并选择候选片段 → POST evidence × K
→ POST claims/generate → 检查/选择 Claim
→ POST landscapes/generate → 查看冲突及来源
→ POST gaps/generate → PATCH Gap selection
→ POST hypotheses/generate → PATCH Hypothesis selection
→ POST experiment-plans/generate → PATCH ExperimentPlan review
→ GET Claim/Evidence → GET PDF file，打开对应页
```

前端每一步都显示当前状态、失败原因和重新发起入口，不把检索、证据判定、推理合成一个不可见请求。演示验收至少覆盖：只有 selected Claim 进入 Landscape、一条 Claim 的支持和质疑 Evidence、一个有来源且范围限定为已提供文献的 Gap、有证伪条件及 motivating Evidence 的 Hypothesis、一个待人工审核的 Plan，以及从它们返回具体 PDF 页。还需验证伪造 Evidence ID、Qwen 非法 JSON、供应商超时、研究问题修改后的新生成拒绝，以及失败 GenerationRun 可定位。M4 到人工接受的 ExperimentPlan 为止；实验执行与结果分析不在此里程碑。

## 7. M5 实验数据与分析 API

下列路径均以前缀 `/api/v1/projects/{project_id}` 开始。跨项目对象 ID 返回项目范围内的 `404 not_found`。新操作要求当前问题修订和当前人工已接受的 ExperimentPlan；历史 GET 仍可读取旧结果。所有修改请求均需 `expected_question_revision`，上传时作为查询参数，其余作为 JSON 字段。

| 路径 | 输入/响应要点 | 审核与来源约束 |
|---|---|---|
| `POST /experiment-plans/{plan_id}/datasets` | multipart `file`、查询参数修订号 → `Dataset` 含 raw `DatasetVersion` 与画像 | 仅当前已接受 Plan；UTF-8 CSV；UUID 路径、SHA-256、大小/行/列上限 |
| `GET /datasets`, `GET /datasets/{id}`, `GET /dataset-versions/{id}`, `GET /dataset-versions/{id}/file` | 元数据或 CSV 文件 | 按项目授权，不公开服务器绝对路径 |
| `POST /dataset-versions/{id}/cleaning-plans`, `.../suggest` | 人工白名单操作或 Qwen 建议 → `CleaningPlan` | 初始 `pending_review`；建议不执行清洗 |
| `GET /cleaning-plans`, `PATCH /cleaning-plans/{id}/review`, `POST /cleaning-plans/{id}/execute`, `GET /cleaning-runs` | 审核、确定性执行、行数影响及新版本 | 仅人工接受后可执行；失败不生成有效版本 |
| `POST /dataset-versions/{id}/analysis-specs`, `.../suggest` | 方法、列映射、参数、理由 → `AnalysisSpec` | 白名单方法；人工审核前不得执行 |
| `GET /analysis-specs`, `PATCH /analysis-specs/{id}/review`, `POST /analysis-specs/{id}/execute` | 审核并执行 → `AnalysisRun` + `StatisticalResult` | 数值来自精确 DatasetVersion，不来自 Qwen；失败无结果批次 |
| `GET /analysis-runs`, `GET /analysis-runs/{id}`, `GET /statistical-results/{id}` | 全部历史执行与结果 | 保留不利与非显著结果，不按 p 值筛选 |
| `POST /dataset-versions/{id}/figures`, `GET /figures`, `GET /figures/{id}`, `GET /figures/{id}/file` | 白名单图表参数 → PNG FigureArtifact | 只从真实数据/结果渲染，记录图表、版本、文件哈希 |
| `POST /figures/{id}/delete` | `expected_question_revision` → `deleted_figure_id` | 人工明确确认后永久删除本项目指定FigureArtifact及受控PNG；不删除数据/统计或静默修订下游来源 |
| `POST /analysis-runs/{id}/interpretations/generate` | Qwen 解释与独立谨慎性评估 → `ResultInterpretation` | 输入完整相关结果集与有界元数据；不发送原始行、不复制数值到权威结果 |
| `GET /result-interpretations`, `GET /result-interpretations/{id}`, `PATCH /result-interpretations/{id}/review`, `POST /result-interpretations/{id}/revisions` | 查看、接受/拒绝、创建人工修订 | 新解释待审核；修订不得更换统计/图表来源；未来报告资格仅为只读状态 |

`AnalysisSpec.method` 可选 `descriptive`、`grouped_descriptive`、`welch_t`、`paired_t`、`pearson`、`spearman`。配对检验要求受试者 ID 和两个完整条件；两个比较组需要明确列值顺序。默认缺失政策为显式 `reject`，不隐式删行或插补；`alpha` 与置信水平只有请求明确给出才记录/使用。已执行方法及参数保持可审计。`GenerationRun` 的 M5 阶段只有 `cleaning_plan`、`analysis_spec`、`result_interpretation`；清洗、统计与图表使用各自确定性执行记录。错误继续使用结构化 `error` 与 `X-Request-ID`。

图表 renderer `m5-matplotlib-v2` 为每张图从已安装字体中选择覆盖实际标题、坐标轴与组别标签字形的字体，不修改全局字体设置或统计方法。若没有字体能覆盖全部标签，返回显式 `figure_failed`，不保存缺字方框图。旧图像、哈希和 renderer 版本不覆盖；修复后通过正常创建图表入口从相同数据/结果创建新产物。

永久删除先锁定项目/当前问题修订与本项目图表，再删除figures受控目录中的PNG及FigureArtifact行；跨项目404、过期问题修订409。文件删除失败返回 `figure_delete_failed` 且不删行；文件已删而数据库提交失败时明确返回需重试清理，重试允许文件已经不存在。保留VisualizationSpec执行记录。已有解释的完整图表集合检查会失效，相关报告不再current/可正式导出；历史快照不被改写，当前输出需经正常生成及人工审核重建。UI在操作前说明不可恢复及下游影响；原始CSV、StatisticalResult和Evidence不删除。

解释提示 `m5-v3` 明确所有解释文本仅使用定性语言，完整来源 ID 只放 `statistical_result_ids` 与 `figure_artifact_ids`，不在正文重复含数字的 UUID、版本或结果；权威数值由已存 `StatisticalResult` 单独显示。提示同时说明保守科学表述校验会匹配否定句或引用中的高风险词，要求直接说明实际设计边界、不确定性及结果与计划的关系，不强制有利方向或改变真实研究范围。此澄清不放宽既有数字正文、科学结论或完整来源校验。解释生成及独立评估记录分层诊断与固定规则代码，不记录候选正文；成功解释仍须人工审核。

正常UI的解释人工修订允许编辑摘要、假设关系、不确定性、局限及其他解释；两个列表每行一项。修订沿用原计算结果和图表来源，创建新待审核版本，保存不等于接受。

## 8. M6 报告 API

报告提示 `m6-v3` 保留原数值、来源与科学表述校验，明确保守词匹配也会拦截否定句中的触发词，改用实际接受设计范围内的定性短句表达边界。合成文件示例仅适用于该设计，不改变真实数据的标签、接受解释的结论或人工审核责任。报告生成复用分层诊断与固定校验代码，不记录候选正文；提示生成和修复文本均纳入版本摘要。

报告导出API以实际后端请求base URL生成Markdown/HTML内的完整图像与PDF地址，保留物理页锚点，便于下载后打开。资源仍依赖该地址下的后端、数据库和存储；不保证独立离线使用。纯内部确定性预览可保持相对资源路径。导出表示不修改已存来源快照、统计值或人工审核状态。

Post-M6 Landscape 生成新增可见错误 `landscape_context_budget_exceeded`（422）。details 包含阶段、适用 `size_budget`、`measured_size`、`context_chars`、实际 `generated_output_chars`、`repair_attempted` 和 `persistence_occurred=false`；预检另记录 `reserved_context_chars`，生成正文贡献为零。Run 已开始时附带 `generation_run_id` 并独立保存失败 Run；预检拒绝不创建 Run。不改变生成/读取公开科学对象形状，旧记录保持可读；所有成功候选仍为 `pending_review`。

所有路径仍以前缀 `/api/v1/projects/{project_id}` 开始，并返回现有结构化错误与 `X-Request-ID`。

| 路径 | 输入 | 行为 |
|---|---|---|
| `POST /reports/generate` | `interpretation_id`, `expected_question_revision` | 仅当前人工接受的解释；短事务 Run、事务外 Qwen、来源哈希复核。默认 60 秒绝对 provider 尝试期限、390 秒报告提交期限、420 秒前端等待期限和 450 秒活动遗留恢复阈值；最终事务须锁定原 Run 并条件性完成成功状态与待审 ResearchReport。过期或被恢复的 Run 返回错误且不能保存报告。 |
| `GET /reports/preview` | `interpretation_id`, `expected_question_revision` | 不调用 Qwen、不创建 Run，仅返回当前来源的确定性草稿预览。 |
| `GET /reports`, `GET /reports/{id}` | 无 | 历史版本与 `current_source_bundle`、`final_export_eligible`。 |
| `PATCH /reports/{id}/review` | `review_status=accepted/rejected`, `expected_question_revision` | 只审核未过期、未被替代的待审版本。 |
| `POST /reports/{id}/revisions` | `title`, `blocks[]`, `expected_question_revision` | 人工文本修订创建新版本；来源不变，状态重新待审。 |
| `GET /reports/{id}/export` | `format=markdown/html`, `draft=false/true` | 无 Qwen 调用；最终版仅当前人工已接受报告，草稿显著标记。 |

`blocks[]` 每项含 `section`、`text`、`source_refs[]`。来源令牌由服务器分配。数字与引文由快照中的 StatisticalResult、Evidence/Paper、FigureArtifact 生成。Benchmark 采用离线、版本化 JSON 会话与 CLI 报告，预检采用 `backend/evaluation/m6_preflight.py`；二者不会伪装成科学对象或 GenerationRun。

`GET /api/v1/benchmark` 从 `backend/evaluation/manual-session.json` 和 `copilot-session.json` 这两个受控路径读取并校验实际会话，返回匿名化摘要（含 `complete`、`missing_stages`、`invalid_stages`）。只有与 m6-v1 场景七阶段精确匹配的两次完整成功会话才产生效率百分比；没有人工基线或会话不完整时 `comparison=NOT MEASURED`，差值与百分比为空；未知/重复/乱序阶段等无效记录返回 422。报告 HTML 用浏览器内联显示以便打印及访问受控图像/物理页链接；Markdown 作为附件下载。前端共享 API 请求期限为 240 秒；这不是整个多次生成/独立核查流水线的服务端完成期限，报告使用前述独立期限。

M3 Claim/Landscape 请求发生客户端超时时，页面只读刷新对应的已保存对象列表，并明确提示服务器可能仍在处理、不要重复生成。该刷新不延长等待状态、不自动重发生成、不改变人工审核/选择。若首次读回时仍未完成，用户须稍后刷新页面；读回失败也明确提示。M3 当前没有同输入活动 Run 的服务端去重保证，不能将报告的活动 Run 防重入契约推广到这些阶段。

**LLM does not calculate statistical results. Computed statistics are produced by deterministic code from immutable dataset versions. AI interpretation is advisory until human reviewed.**


Post-M6 corrected-scope budgeting uses Landscape prompt `m3-v7` and GenerationRun contract `m3-landscape-v6`. The internal summary source representation is lossless and does not change public requests, responses, source authority, historical readability or human acceptance. Generated-output allowance reserves both full-source item verification and summary/repair contexts. The same explicit 422 budget failure remains; no context guard or provider configuration changes.
# Proposed Landscape allocation diagnostics

The review-only `m3-landscape-v8` contract preserves the existing `landscape_context_budget_exceeded` response and cumulative `size_budget` / `measured_size`. Assessment quota errors additionally report numeric `cumulative_cap`, `immutable_prior_chars`, nullable `current_candidate_chars`, `server_overhead_chars`, `list_overhead_chars`, `later_reservation_chars`, `current_response_allowance`, `minimum_response_chars`, `minimum_required_chars`, `remaining_items`, and `required_reduction_chars`. A current-quota rejection may occur below the cumulative cap because later space is reserved. Preflight has no editable candidate and never counts a nonexistent provider attempt. Scientific-repair history remains reflected in `repair_attempted`; `persistence_occurred` remains false. Public/historical scientific objects and successful pending-review behavior are unchanged. See [the allocation contract](landscape-assessment-allocation-2026-10-11.md); this proposal is not deployed.
