## Why

当前 `web_search` / `web_fetch` 仍以 mock 路径为主，不能完成真实 Web 信息查询和总结任务。Astra 需要接入真实 Google 搜索，并让抓取器能根据搜索结果页面类型自适应提取主要内容，才能证明“通用数据查询任务”不只是演示闭环，而是能处理真实网页证据。

## What Changes

- 将 `web_search` 扩展为真实 Google 搜索实现，优先使用 Google Programmable Search JSON API，而不是抓取 Google 搜索结果页。
- 增加 Google 搜索配置：API Key、Search Engine ID、结果数量、语言/地区参数和安全搜索参数。
- 将 `web_fetch` 扩展为自适应内容爬虫，能够从搜索结果 URL 抓取网页主要内容、标题、描述、发布时间和正文片段。
- 将 Google 搜索和自适应爬虫都作为 Agent 工具实现，挂载到统一 tool registry，而不是写成总结流程的内置专用逻辑。
- 增加由模型辅助生成的抓取策略：根据 URL、content type、页面结构和搜索摘要决定正文提取方式。
- 增加总结任务流程：用户输入一个主题，系统搜索、筛选、抓取多个来源，综合摘要，并输出来源、引用、冲突和限制。
- 增强工具审计：记录 Google 搜索请求参数、抓取策略、提取质量、失败原因和来源去重结果。
- 保留 mock provider 作为测试路径，但默认真实运行应支持 Google 搜索和真实网页抓取。

## Capabilities

### New Capabilities
- `google-web-search`：通过 Google 搜索 API 发现候选来源，并返回结构化搜索结果。
- `adaptive-web-crawler`：根据搜索结果和页面结构动态选择抓取策略，提取网页主要内容。
- `source-summary-task`：基于搜索与抓取证据完成一次 Web 总结任务，并输出有来源支撑的总结。

### Modified Capabilities
- 无。

## Impact

- 影响后端工具运行时：`web_search`、`web_fetch`、工具 registry、配置加载和 ToolCall 审计输出。
- 影响工具扩展机制：需要让工具暴露统一 manifest、输入 schema、输出 schema、权限、副作用等级、超时和错误分类，方便后续继续为 Agent 增加工具。
- 影响 run engine：需要支持搜索结果筛选、来源去重、多 URL 抓取、抓取质量判断和总结任务状态。
- 影响模型客户端：需要为抓取策略生成和总结输出增加结构化 schema。
- 影响测试：需要 mock Google 搜索响应、mock HTML 页面、抓取失败/低质量页面和多来源总结结果。
- 影响文档：需要说明 Google API 配置、搜索配额、真实网络读取限制和本地 mock 验证方式。
