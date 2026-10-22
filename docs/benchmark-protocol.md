# 溯理 SULI · M6 基准测量协议（m6-v1）

## 固定场景与输入

使用 `backend/evaluation/m6_scenario.json`。其任务、阶段、两篇合成 PDF、合成 CSV、逐文件 SHA-256 均在计时前固定；CLI 在启动计时前复核文件哈希。PDF 与 CSV 均为测试材料，不能作为真实研究结论。修改场景或输入必须升版本并重新测量两种模式；不得在看见结果后改阶段定义。

研究问题：在这组**合成、限定的文献**中，方法 A 与 B 的结果对照是否充分？为一个可证伪的比较方案选择证据与假设，对合成 CSV 的 `group` 与 `score` 执行受控分析，审核解释并完成报告。研究空白只限于给定语料。人工模式可以使用常规 PDF 阅读器、表格软件及文字工具，但不能使用 溯理 SULI 或额外 AI；溯理模式使用本项目。两种模式用同一份场景与文件，人工均需记录真实决策。

## 阶段与停止规则

阶段依次为证据识别、Claim/全景审核、Gap/假设/计划审核、数据准备、确定性分析及首张图、解释审核、报告审核。每阶段在操作者开始实际工作时按回车计时，达到可供下一阶段使用的产物时按回车结束。失败必须标记，不得把失败会话当作完成。全部阶段完成后，`elapsed` 为首阶段开始至末阶段结束的墙钟时间；空档、模型等待和人工阅读都计入。可以记录耗时原因，但不得事后删除慢阶段。人工模式同样按该规则执行。溯理模式额外从数据库中读取在计时窗口内创建的 GenerationRun、CleaningRun、AnalysisRun 与图表执行计数，以及已记录的 provider 尝试数；这些计数不替代墙钟时间。

`m6_scenario.json` 的 `stages` 是 m6-v1 唯一的必需阶段契约，顺序和 ID 如下：

1. `evidence_identification`
2. `claim_and_landscape_review`
3. `gap_hypothesis_plan_review`
4. `dataset_preparation`
5. `deterministic_analysis_and_figure`
6. `interpretation_review`
7. `report_review`

两种模式都必须按此顺序各记录一次，每阶段成功且有正数耗时。漏阶段、失败或零耗时会标为不完整；未知、重复或乱序阶段属于无效记录。一个成功阶段绝不代表完整会话。会话的场景 ID、任务、协议版本、完整场景文件 SHA-256 必须与当前版本化场景相符。场景文件包含 PDF/CSV 清单和各文件哈希；计时前 CLI 校验实际文件。若更改任何阶段或输入，必须升协议/场景版本并重新测量两方。

`human_decisions` 是明确的科学选择或审核次数，包括选择 Evidence、审核并选择 Claim、接受 Landscape、选择 Gap/Hypothesis、接受 ExperimentPlan、批准 CleaningPlan/AnalysisSpec、接受 ResultInterpretation 和 ResearchReport。一次可审计决定计一次；普通导航、打开链接和滚动不计，不能称为“点击次数”。操作者每阶段记录真实次数，会话总数从各阶段求和并校验。

## 记录命令

在 `backend` 目录运行（PowerShell）：

```powershell
..\.venv\Scripts\python.exe evaluation/m6_benchmark.py record --scenario evaluation/m6_scenario.json --mode manual --output evaluation/manual-session.json
..\.venv\Scripts\python.exe evaluation/m6_benchmark.py record --scenario evaluation/m6_scenario.json --mode research_copilot --project-id <项目ID> --output evaluation/copilot-session.json
..\.venv\Scripts\python.exe evaluation/m6_benchmark.py summarize --manual evaluation/manual-session.json --copilot evaluation/copilot-session.json --json evaluation/benchmark-result.json --markdown evaluation/benchmark-result.md
```

会话 JSON 包括场景/输入哈希、协议版本、模式、UTC 起止时间、逐阶段成功与耗时、人工决策以及 溯理持久化计数。原始会话文件为事实来源；生成的 JSON/Markdown 是派生报告。建议把私人真实测量放在非版本控制目录，提交前确认没有隐私数据。CLI 不覆盖已有会话文件。

当前项目工作区的简洁基准面板调用 `GET /api/v1/benchmark`，只读取上述 `evaluation/manual-session.json` 与 `evaluation/copilot-session.json` 固定文件名的校验摘要。操作者使用其他位置记录时，可继续用 CLI 生成报告；要在工作区展示，请将无隐私的对应原始会话文件放到这两个受控路径。API 不返回原始备注。

## 算法与限制

仅在人工与 溯理会话都含上述**精确七阶段成功集合**、顺序正确、场景 ID、输入哈希、协议版本和模式匹配时比较。`time_saved_seconds = manual_seconds - copilot_seconds`，`percentage_reduction = time_saved_seconds / manual_seconds × 100`。人工基线必须大于零；缺失、不完整、失败或不匹配会话的比较为 `NOT MEASURED`，差值与百分比为空。JSON 和 Markdown 摘要显示 `complete`、`missing_stages`、`invalid_stages`；无效阶段记录拒绝读取。允许负值，它表示 溯理在实测中更慢。当前仓库**没有人工基线实测**，因此没有效率提升数值。统计黄金测试、来源链测试与人类语义评分是独立的质量证据，不与效率混成单一分数。
