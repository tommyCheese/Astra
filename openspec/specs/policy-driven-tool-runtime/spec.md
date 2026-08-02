# policy-driven-tool-runtime Specification

## Purpose
TBD - created by archiving change decouple-tool-runtime-and-add-sandboxed-chart-rendering. Update Purpose after archive.
## Requirements
### Requirement: Composable tool registration
系统 SHALL 支持从独立工具提供者组合 Tool Registry，且 Agent Runtime 不得依赖 Web 专用 Registry 构建函数。

#### Scenario: Register Web and chart tools together
- **WHEN** 部署启用 Web 与图表能力
- **THEN** Registry 同时暴露 `web_search`、`web_fetch` 和 `chart.render`，无需修改 AgentLoop

### Requirement: Policy-driven tool resolution
Tool Router and the execution-time capability selector SHALL resolve tools according to semantic task capability, manifest security capability, permissions, risk, execution backend, frozen catalog, current Run policy, and budget rather than a Plan-level concrete tool name or hardcoded tool-name allowlist.

#### Scenario: Resolve an allowed sandboxed chart tool
- **WHEN** an active node requires `data.visualize`, `chart.render` declares that semantic task capability, and the Run allows its sandboxed compute and artifact effects
- **THEN** the selector offers the manifest and Router validates its concrete invocation

#### Scenario: Reject a disallowed capability
- **WHEN** a matching tool is registered but its security capability is not allowed by the Run policy
- **THEN** the tool is excluded or Router returns an auditable `tool_not_allowed` or `permission_denied`
- **THEN** the tool is not executed

#### Scenario: Resolve equivalent provider tools
- **WHEN** multiple eligible tools declare the semantic capability required by the active node
- **THEN** the runtime exposes all matching candidates without requiring a Plan change

### Requirement: Only eligible manifests enter model context
Context assembler SHALL expose only tool manifests that are present in the Run's frozen catalog, currently eligible under Run policy and backend availability, and matched by the active node's semantic requirements; it SHALL expose safe resolution metadata separately from tool inputs and secrets.

#### Scenario: Sandbox backend unavailable
- **WHEN** a visualization tool is configured but its Sandbox Executor is unavailable
- **THEN** the model context does not contain that tool
- **THEN** resolution metadata records a safe capability-unavailable reason

#### Scenario: Active node has multiple providers
- **WHEN** multiple healthy and allowed provider tools satisfy the same active semantic requirement
- **THEN** every matching manifest enters the execution decision context in deterministic order
- **THEN** no provider credential or secret configuration enters the resolution metadata

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

### Requirement: Result processors emit canonical evidence fragments
Applicable result processors SHALL convert tool-specific output into schema-validated canonical evidence fragments without persisting directly or expanding permissions.

#### Scenario: Web read completes
- **WHEN** a Web read ToolResultEnvelope is processed
- **THEN** the processor emits source snapshot and passage fragments and the host supplies trusted invocation lineage before persistence

### Requirement: Host controls evidence persistence
Only host-managed EvidenceWriter code SHALL persist evidence fragments, and plugins MUST NOT receive unrestricted repository or database access for evidence ingestion.

#### Scenario: Plugin emits malformed evidence
- **WHEN** a result processor returns an evidence fragment that fails the canonical schema
- **THEN** the invocation fails safely before the fragment is persisted

### Requirement: Persisted permission records use the current identity model
The system SHALL read permission identities, grants, and audit records only from the current Run/Task identity schema and SHALL NOT expose compatibility projections for legacy single-lease or unscoped authorization data.

#### Scenario: Read obsolete authorization data
- **WHEN** a persisted grant or identity lacks the current binding and scope fields
- **THEN** the record is rejected or treated as unauthorized without constructing a compatibility view

