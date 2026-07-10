## Context

Astra 当前已经有一条 Web 数据查询纵向切片：用户提交目标，后端创建 run，模型规划，工具执行，最终生成带证据的结果。但当前 `web_search` 主要是 mock 路径，`web_fetch` 只是基础 HTTP 抓取，无法可靠完成真实 Web 搜索、页面正文抽取和总结任务。

本变更将这条切片推进到真实信息获取：使用 Google 搜索 API 发现来源，用自适应爬虫提取页面主要内容，并用已记录的证据完成一次总结任务。

## Goals / Non-Goals

**Goals:**
- 使用 Google Programmable Search JSON API 实现真实 `web_search`。
- 将搜索结果标准化为候选来源，包含标题、摘要、URL、排名、显示域名和检索时间。
- 实现自适应 `web_fetch`：根据 URL、content type、HTML 结构和搜索摘要选择提取策略。
- 由模型生成“抓取策略描述”，但只允许选择受控策略和 CSS selector，不允许执行模型生成代码。
- 支持一次完整总结任务：搜索多个来源、去重、抓取、筛选证据、综合总结并引用来源。
- 记录抓取质量、失败原因、来源覆盖和验证警告。

**Non-Goals:**
- 不抓取 Google 搜索结果 HTML 页面。
- 不绕过 CAPTCHA、登录墙、付费墙、robots 限制或安全拦截页。
- 不执行模型生成的任意 Python/JavaScript 代码。
- 不引入无边界浏览器自动化。
- 不实现长期爬虫队列、站点地图遍历或大规模索引系统。
- 不做复杂反爬规避。

## Decisions

### 将搜索和爬虫作为 Agent 工具实现

Google 搜索和自适应爬虫都必须实现统一 `Tool` 接口，并注册到 tool registry。run engine 只能通过工具名称、结构化输入和 registry 调用它们，不能直接 import provider 逻辑或写死总结任务专用分支。

每个工具都应暴露：
- `name`
- `version`
- `description`
- `input_schema`
- `output_schema`
- `permission`
- `side_effect_level`
- `timeout`
- `retry_policy`
- `error_categories`

这条约束是为后续继续添加 Agent 工具打地基。未来新增工具，例如数据库查询、文档读取、表格处理、浏览器动作或业务 API 调用，都应沿用相同注册、审计和权限模型。

考虑过的替代方案：
- 在 run engine 中直接调用 Google provider 和爬虫函数：实现快，但会让工具边界消失，后续扩展会变成一堆特殊分支。
- 为每个任务类型单独写工具调用逻辑：短期清楚，长期不可维护。

### 使用 Google Programmable Search JSON API

`web_search` 的 Google provider 将调用 Google Programmable Search JSON API。配置项包括 `GOOGLE_SEARCH_API_KEY`、`GOOGLE_SEARCH_ENGINE_ID`、结果数量、语言、地区和安全搜索参数。

考虑过的替代方案：
- 抓取 Google 搜索结果页：不稳定，容易触发 CAPTCHA，也可能违反服务条款，因此不采用。
- 使用第三方 SERP API：可作为后续 provider，但第一版以 Google 官方 API 为基准。
- 继续使用 mock：适合测试，但不能满足真实搜索任务。

### 保持 provider 架构

`web_search` 作为工具保留 provider 模式：
- `mock`：本地测试和确定性验证。
- `google`：真实 Google 搜索。

这样测试不依赖外部网络和配额，真实运行可以通过环境变量切换。

### 自适应爬虫使用受控策略

`web_fetch` 作为工具按固定流水线工作：

```text
URL + search snippet
  -> fetch HTML
  -> inspect metadata/content type
  -> choose extraction strategy
  -> extract main content
  -> score quality
  -> store artifact + tool output
```

支持的策略：
- `readability`：面向文章/新闻/博客页面的正文抽取。
- `metadata_first`：优先使用 OpenGraph、Twitter Card、schema.org 和 meta description。
- `selector_extract`：使用模型建议的受控 CSS selector 提取候选正文。
- `plain_text`：用于纯文本或简单 HTML。
- `fallback_snippet`：页面无法抓取或正文质量低时使用搜索摘要作为弱证据，并标记 warning。

模型可以生成 `CrawlerPlan`，但只能包含策略枚举、允许的 selector 列表、排除 selector 和提取目标。后端必须验证 schema，不执行任意代码。

### 总结任务基于证据包

run engine 将为总结任务构造 Evidence Pack：
- 搜索查询。
- 搜索候选来源。
- 实际抓取成功的来源。
- 每个来源的正文片段、标题、发布时间、域名和质量分。
- 失败来源和失败原因。
- 去重与筛选结果。

模型最终总结只能基于 Evidence Pack 输出。最终答案需要包含摘要、要点、来源引用、冲突、限制和验证备注。

run engine 的职责是编排工具，而不是实现搜索或爬虫本身。总结任务应通过工具调用记录来构造 Evidence Pack，确保后续更多工具也能以同样方式进入 Agent 证据链。

### 抓取质量可审计

`web_fetch` 输出必须包含：
- `extraction_strategy`
- `quality_score`
- `content_length`
- `source_type`
- `warnings`
- `retrieved_at`

这让 UI 和后续审查 Agent 能判断结果可信度，而不是只看到一段模型总结。

## Risks / Trade-offs

- Google API 配额耗尽或配置缺失 -> 保留 mock provider，真实 provider 缺配置时返回清晰配置错误。
- Google 搜索结果质量不足 -> 支持查询改写和多查询计划，但限制总请求数。
- 页面阻止抓取或需要登录 -> 记录 failed ToolCall，不绕过限制，用其他来源或带警告完成。
- 页面正文抽取质量差 -> 使用质量评分、最小正文长度、去噪规则和 fallback_snippet warning。
- 模型生成 selector 不可靠 -> selector 必须受 schema 验证，提取失败时回退到 readability/metadata。
- 多来源互相冲突 -> 最终总结必须显式列出冲突和来源差异。
- 真实网络测试不稳定 -> 单元测试使用 fixture HTML 和 mock Google 响应；少量集成测试可用环境变量开启。

## Migration Plan

1. 增加 Google 搜索配置项和 `.env.example` 文档。
2. 实现 Google search provider，并保留 mock provider。
3. 增加 `CrawlerPlan`、`ExtractedSource`、`EvidencePack` 和总结输出 schema。
4. 实现 HTML 抓取、metadata 提取、正文抽取、selector 提取和质量评分。
5. 更新 run engine：搜索、筛选、抓取、证据包构造、总结和验证。
6. 更新 UI 展示：真实来源、抓取策略、质量评分、失败来源和总结限制。
7. 增加 mock Google 响应、fixture HTML、失败页面和总结任务测试。
8. 更新 README，说明 Google API 配置和真实网络读取行为。

## Open Questions

- 第一版是否只支持 Google Programmable Search，还是同时保留 Brave provider？
- 是否需要 UI 暴露搜索语言/地区参数，还是先只通过后端配置控制？
- 抓取正文最大长度应默认限制为多少，才能兼顾模型上下文和证据完整性？
- 是否需要为 robots.txt 检查单独建一个显式工具/审计字段？
