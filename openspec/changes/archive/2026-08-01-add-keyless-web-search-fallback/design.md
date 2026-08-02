## Context

`WebSearchTool` 当前根据 `WEB_SEARCH_PROVIDER` 直接分发到 Bing RSS、DuckDuckGo HTML、Google Programmable Search 或 Brave Search。代码默认值和示例配置是 `bing`，但实际 `.env` 很容易保留为 `google`，导致没有 Google 凭据的本地运行在工具执行阶段失败。Web 工具运行在隔离容器内，宿主只通过一次性配置文件传入显式白名单配置，因此 provider 解析必须在工具内部完成，且输出要能沿现有 ToolCall、Observation 和 Evidence Pack 链路被审计。

无密钥公共搜索入口具有结构变化、限流和使用条款约束，不能与正式搜索 API 宣称相同的生产保证。本设计把它定义成显式的降级能力，而不是透明伪装成 Google 搜索。

## Goals / Non-Goals

**Goals:**

- 让没有搜索 API 凭据的本地和个人部署通过 `auto` 完成真实候选来源发现。
- 在无密钥模式下以 Bing RSS 为首选、DuckDuckGo HTML 为回退，并对空结果和可恢复 provider 错误执行有限回退。
- 在输出中记录实际 provider、逐次尝试、回退原因与 degraded 状态，供 ToolCall 和 Evidence Pack 审计。
- 保持显式 provider 的严格、可预测语义和现有工具契约。
- 不把 secret 写入工具输入、输出、warning 或错误详情。

**Non-Goals:**

- 不承诺无密钥 provider 的商业使用许可、可用性、结果质量或吞吐 SLA。
- 不引入 SearXNG、自建搜索索引、浏览器自动化或模型厂商原生 Web Search。
- 不在本次 change 中增加运行时 UI provider 选择器或持久化 provider 设置。
- 不对显式 `google`、`brave`、`bing`、`duckduckgo` 自动回退。

## Decisions

### 1. 仅 `auto` 具有 provider 选择和回退语义

显式 provider 继续直接执行：`google` 缺少凭据仍返回 `missing_credentials`，其他 provider 失败也不切换。这使配置意图与 ToolCall 行为一致，避免静默掩盖部署错误。

`auto` 的选择顺序为：

1. 同时配置专用 `GOOGLE_SEARCH_API_KEY` 与 `GOOGLE_SEARCH_ENGINE_ID` 时，仅选择 Google API；
2. 否则配置 `WEB_SEARCH_API_KEY` 时，仅选择 Brave API；
3. 否则进入无密钥链路 `bing` → `duckduckgo`。

自动识别 Google 时只接受专用 Google Key，避免无法判断通用 `WEB_SEARCH_API_KEY` 究竟属于 Google 还是 Brave；显式 `google` 保留既有通用 Key 兼容行为。正式 API 已配置但调用失败时不自动降级到公共页面，以免部署在不知情的情况下跨越许可、质量和数据路径边界。

### 2. 无密钥回退只处理可恢复搜索失败

在无密钥链路中，Bing 返回候选项即停止；Bing 返回空候选、`search_failed` 或解析后无结果时继续 DuckDuckGo。非法用户输入等非 provider 可用性错误不触发回退。DuckDuckGo 成功但仍为空时返回结构化空结果；两个 provider 都因网络或解析异常失败时，抛出一个脱敏的聚合 `search_failed`。

不在单次 provider 内增加额外重试，避免与 AgentLoop 现有重试预算叠加导致不可控请求放大。

### 3. 扩展成功输出而不改变工具输入契约

所有成功搜索输出继续包含 `query`、`provider`、`parameters`、`candidate_count`、`warnings` 和 `candidates`，并新增：

- `provider_mode`：`explicit` 或 `auto`；
- `provider_attempts`：按执行顺序记录 `provider`、`status`、`candidate_count`，失败时仅记录安全的 `error_category`；
- `degraded`：是否使用无密钥公共搜索链路。

`auto` 的无密钥结果增加一条稳定 warning，说明结果来自不保证 SLA 的公共搜索入口；发生 provider 回退时再增加具体回退 warning。现有通用工具 envelope 会保留这些字段，无需修改 AgentLoop 的工具名分支。

### 4. 宿主和容器默认统一为 `auto`

`Settings`、Web runtime settings、`.env.example` 和 README 统一使用 `auto`。宿主仍通过现有 `_web_runtime_config` 传递 provider 与凭据白名单，不增加宿主环境透传。开发者现有显式配置保持原义；本地 `.env` 从 `google` 改为 `auto` 后需要重启后端以刷新缓存设置。

## Risks / Trade-offs

- [Bing RSS 或 DuckDuckGo HTML 改版、限流或停止服务] → 保持两个独立解析器、有限回退、结构化空结果和真实网络 smoke test；文档明确其降级定位。
- [无密钥搜索 warning 使成功 Run 进入 `completed_with_warnings`] → 这是有意的诚实状态表达，避免把无 SLA 来源报告成完全健康的生产能力。
- [配置了通用 Key 但原意是 Google] → `auto` 将其解释为 Brave；文档要求自动 Google 使用专用 Key，显式 `google` 继续兼容旧配置。
- [错误聚合丢失逐次成功输出] → 聚合错误只包含 provider 和错误类别；成功或空结果通过 `provider_attempts` 提供完整尝试轨迹。
- [真实网络测试产生波动] → 单元测试使用确定性 mock；真实 smoke test只做人工或显式集成验证，不成为默认 CI 阻塞项。

## Migration Plan

1. 发布支持 `auto` 的后端和 Web runtime 镜像。
2. 将示例配置和本地开发配置切换为 `auto`，重启后端。
3. 运行 provider 路由、回退、sandbox 配置和脱敏测试。
4. 用无凭据配置执行一次真实搜索 smoke test，确认实际 provider、尝试轨迹与 degraded warning。
5. 如需回滚，将 `WEB_SEARCH_PROVIDER` 显式设置为 `bing`，无需数据库迁移。

## Open Questions

- 后续是否在工具能力接口或管理 UI 中展示“无密钥降级模式”，而不仅依赖 ToolCall 输出与 warning。
- 商业部署未来选择正式搜索 API、用户授权浏览器搜索还是自建索引，需要独立 change 评估许可和成本。
