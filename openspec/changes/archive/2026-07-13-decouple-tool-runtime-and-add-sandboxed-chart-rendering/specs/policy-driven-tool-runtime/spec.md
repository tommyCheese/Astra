## ADDED Requirements

### Requirement: Composable tool registration
系统 SHALL 支持从独立工具提供者组合 Tool Registry，且 Agent Runtime 不得依赖 Web 专用 Registry 构建函数。

#### Scenario: Register Web and chart tools together
- **WHEN** 部署启用 Web 与图表能力
- **THEN** Registry 同时暴露 `web_search`、`web_fetch` 和 `chart.render`，无需修改 AgentLoop

### Requirement: Policy-driven tool resolution
Tool Router SHALL 根据 manifest capability、权限、风险、execution backend、当前 Run policy 和预算解析工具，而非通过硬编码工具名称 allowlist 决定。

#### Scenario: Resolve an allowed sandboxed chart tool
- **WHEN** `chart.render` 已注册且 Run policy 允许 `sandboxed_compute` 与 `artifact_write`
- **THEN** Router 校验输入和预算后返回该工具

#### Scenario: Reject a disallowed capability
- **WHEN** 工具已注册但其 capability 未被当前 Run policy 允许
- **THEN** Router 返回可审计的 `tool_not_allowed` 或 `permission_denied`，且不执行工具

### Requirement: Only eligible manifests enter model context
Context assembler SHALL 只向模型暴露当前 Run 可实际调用的工具 manifest。

#### Scenario: Sandbox backend unavailable
- **WHEN** chart capability 已配置但 Sandbox Executor 当前不可用
- **THEN** 模型上下文不包含 `chart.render`，并记录 capability 不可用原因

### Requirement: Generic tool result envelope
所有工具执行结果 SHALL 转换为统一 envelope，至少包含状态、结构化数据、warnings、metrics 和 Artifact 引用，AgentLoop 不得按具体工具名解释原始输出。

#### Scenario: Process a chart result
- **WHEN** `chart.render` 成功生成 PNG Artifact
- **THEN** AgentLoop 接收通用成功 observation 和 ArtifactRef，而无需 Matplotlib、Seaborn 或 ECharts 专用分支

### Requirement: Auditable tool execution context
Agent Runtime SHALL 为每次工具调用构造 `ToolExecutionContext`，包含 Run、ToolCall、Step、trace 及已授权 Artifact/Sandbox service；工具不得通过全局数据库状态推断这些关联。

#### Scenario: Execute a sandboxed chart tool
- **WHEN** AgentLoop 已创建 `chart.render` ToolCall 并开始执行工具
- **THEN** 工具收到包含该 ToolCall ID 的 context，且其 SandboxJob 和输出 Artifact 均关联同一 Run 与 ToolCall

#### Scenario: Execute a legacy Web tool
- **WHEN** AgentLoop 调用不需要运行服务的只读 Web 工具
- **THEN** 工具可忽略 execution context，并保持既有输入输出行为

### Requirement: Domain-specific processing remains pluggable
系统 SHALL 通过可注册 processor 和 validator 处理 Web Evidence Pack、图表验证及其他领域规则，并由通用完成门控汇总结论。

#### Scenario: Complete a non-Web chart task
- **WHEN** 图表 Artifact 验证成功且任务不要求外部来源
- **THEN** 完成门控不得因缺少 Web fetched sources 而阻塞 Run

### Requirement: Preserve existing Web tool behavior during migration
系统 MUST 在迁移期间保持 `web_search`、`web_fetch` 的名称、输入契约、审计记录和 Web 证据验证语义。

#### Scenario: Execute an existing Web query
- **WHEN** 用户发起此前由 Web Agent 支持的查询
- **THEN** 通用工具路径产生与既有路径等价的搜索、抓取、Evidence Pack 和验证结果
