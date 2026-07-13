## Why

当前 `web_search` 在显式选择 Google 但未配置凭据时只能失败，而 Astra 已具备 Bing RSS 和 DuckDuckGo HTML 两条无密钥搜索路径，却缺少一个可审计的自动选择与降级策略。为了让本地开发和个人部署开箱即可完成真实 Web 查询，同时避免静默掩盖显式 provider 配置错误，需要增加明确的无密钥模式及其运行记录。

## What Changes

- 新增 `WEB_SEARCH_PROVIDER=auto`，根据可用凭据选择正式 API provider；没有搜索凭据时按 Bing RSS、DuckDuckGo HTML 的顺序执行无密钥搜索。
- `auto` 模式在首选无密钥 provider 网络失败、解析失败或没有候选来源时尝试下一 provider，并在工具输出中记录每次尝试、回退原因和 degraded 状态。
- 保持显式 `google`、`brave`、`bing`、`duckduckgo` 的严格语义；显式 provider 配置错误仍然失败，不静默切换。
- 将默认和示例配置调整为 `auto`，并在文档中明确无密钥路径适用于本地开发与个人部署，不承诺商业生产搜索 SLA。
- 为 provider 选择、回退、失败聚合、sandbox 配置传递和凭据脱敏补充测试。

## Capabilities

### New Capabilities

- `keyless-web-search-fallback`：定义自动 provider 选择、无密钥搜索回退、降级状态和审计输出。

### Modified Capabilities

- `google-web-search`：明确显式 Google 配置仍采用严格凭据校验，不因新增自动模式而静默回退。
- `web-data-query`：搜索候选来源输出增加实际 provider、provider 尝试记录和降级 warning 语义。

## Impact

- 后端 `WebSearchTool` provider 路由和统一搜索输出结构。
- Web sandbox 的显式配置注入及隔离运行时默认值。
- `backend/.env.example`、README 和本地 `backend/.env` 的搜索 provider 配置。
- Web 工具单元测试、sandbox 配置测试及可选真实网络 smoke test。
- 不新增第三方依赖，不改变 `web_search` 工具名称、输入 schema、网络读取权限或只读副作用等级。
