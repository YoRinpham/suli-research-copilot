# Landscape 核查可靠性例外：实现与验证

2026-10-09。依据研究者对上一份 completion-route 报告 D 节的明确批准，在 `codex/landscape-canonical-verification` 分支实施。基线为 `031c238a5f4b617ebb0fc0cf4d67b3d9ea2237da`。本批完成工程实现与确定性验证；没有执行真实生成或科学接受。

## 实际变化

- 新的 `LandscapeItemVerification`、`LandscapeSummaryVerification` 只接受科学判断与授权 Evidence ID，拒绝模型返回引文或额外字段。Landscape Prompt 为 `m3-v5`，schema 为 `m3-landscape-v4`。
- 每条目独立核查 `support_verdict`、标记的未支持陈述和来源限制，同时保留原冲突／可比性规则。摘要独立核查经过验证的条目及其来源。正向支持必须引用范围内的支持 Evidence；真实 ID 不能推翻未支持或证据不足的判断。
- 服务端从已验证快照绑定完整、未经标准化的 `Evidence.source_text`，通过原有精确子串校验。评估保留 `support_excerpts` 形状，增加 `source_binding_origin=server_bound_canonical_source_text`、版本、原文哈希和 PaperChunk／Paper／物理页来源；界面明确标注这段文字由系统绑定，仍须人工核对。历史错误引文和旧记录不被改写。
- 最终快照比较补入支持／质疑角色、Claim 类型及不确定性哈希、Paper 身份，拒绝等待期间的来源变化。原 Run 执行权校验、短事务、实际调用计数、受限修复和失败后零半成品机制保留。提供商与服务两层校验产生的未知字段名均在 Landscape 日志边界脱敏。
- 没有新端点、数据库迁移、依赖或提供商配置变化。生成结果仍为 `pending_review`；M4 仍须真实人工接受与后续逐步选择。

## 执行过的验证

| 验证 | 实际结果 |
|---|---|
| 后端全量确定性基础测试：`python -B -m pytest -q -p no:cacheprovider --tb=short --basetemp <本批唯一目录> -k 'not real_qwen'`，`RUN_DB_INTEGRATION=0` | 212 passed，167 skipped，15 deselected |
| 独立临时 PostgreSQL：正常 Alembic 升级后运行 M3 persistence／semantic／authority、canonical persistence 与 M4 数据库回归，排除真实提供商测试 | 123 passed，1 deselected |
| 前端 `tsc --noEmit --incremental false` | exit 0 |
| 前端 `npm test` | 29 passed |
| 独立代码复核及 `git diff --check` | 通过；发现的问题均已修复并重新验证 |

先观察了新协议测试的失败，再实现通过；另外逐一观察并修复了 Paper 身份、Evidence 角色、Claim 类型／不确定性漂移、摘要修复丢失限制条件，以及真实适配器模拟 HTTP 响应的日志字段泄漏测试。数据库测试只使用新建隔离库；该库已删除。适配器测试使用 `httpx.MockTransport`，没有网络请求。

测试证明原文／引用／状态和核查结果的执行边界，没有证明模型科学判断准确率或真实提供商的完成率。原来较大的上下文和超时仍可能失败。基础测试初次遇到系统临时目录访问错误，改用本批独立可写目录后通过；数据库回归初次发现单 Claim 错误冲突诊断的兼容性问题，修复后全套通过。保留现有 Starlette/httpx 弃用警告，没有增加依赖处理它。

## 完整性与后续边界

最终只读审计确认生产库全部 31 张公开表的行数和规范化哈希与前次 Run-C 完成快照一致，37 条 Evidence／PaperChunk 均匹配原 PDF 的真实物理页。314 个基线文件中仅批准的 11 个已有代码／测试／文档文件发生变化；新增测试、计划与本报告属于本次工程变更。原 PDF、`.env`、提供商配置、评测资产均未变，`measurement_started=false`。

`m6-pass` 注解对象仍为 `b5836d8126e1497eff1d1eb7fe45d0925fc6caf7`，目标仍为 `97725e6e41e33f9ed85a0ffcbeb80e60e8ba178a`。没有新增生产 GenerationRun、Landscape、接受决定或下游对象。真实提供商调用次数为 0。

详细证据保存在 `data/operator-landscape-canonical-implementation-2026-10-09/` 的 `backend-regressions.log`、`isolated-regressions.log`、`isolated-regressions.json`、`integrity-verification.json`。上一份报告及失败 Run 作为历史证据保留原样。

下一次真实部署验证需单独明确授权一个新的有限预算，并在正常应用中使用已批准的 `qwen3.8-max / low / 60s / two retries` 进程配置。真实生成成功后仍要审阅完整来源并作出单独的人工接受决定。本次工程批准不能代替这些科学决策。

自动审批曾拒绝移除一层重复日志脱敏的整理步骤，理由是可能暴露私密字段名；该保护已保留，没有阻塞未完成的工程工作。
