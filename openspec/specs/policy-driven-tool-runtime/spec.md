# policy-driven-tool-runtime Specification

## Purpose
TBD - created by archiving change decouple-tool-runtime-and-add-sandboxed-chart-rendering. Update Purpose after archive.
## Requirements
### Requirement: Composable tool registration
The system SHALL compose the Tool Registry from deterministic, verified Tool Provider Plugin contributions, and the Agent Runtime SHALL NOT depend on provider-specific Registry builders or imports.

#### Scenario: Register Web and chart tools together
- **WHEN** the built-in Web and Chart providers are enabled
- **THEN** the Registry exposes `web_search`, `web_fetch`, and `chart.render` through provider contributions without modifying AgentLoop

#### Scenario: Register a managed third-party tool
- **WHEN** an administrator enables a trusted provider that contributes a uniquely named tool
- **THEN** the tool is added through the same CatalogBuilder path used by built-in tools

### Requirement: Policy-driven tool resolution
Tool Router SHALL resolve tools according to the frozen catalog binding, manifest capability, permissions, risk, execution backend, current Run policy, provider health, and budget rather than a hardcoded tool-name allowlist.

#### Scenario: Resolve an allowed sandboxed chart tool
- **WHEN** `chart.render` is registered, its provider is healthy, and Run policy allows `sandboxed_compute` and `artifact_write`
- **THEN** Router validates the invocation and returns the frozen tool and executor binding

#### Scenario: Reject a disallowed capability
- **WHEN** a registered tool requires a capability not allowed by the Run policy
- **THEN** Router returns an auditable `tool_not_allowed` or `permission_denied` outcome and does not execute the tool

#### Scenario: Provider becomes unavailable before a new Run
- **WHEN** a provider is disabled or unhealthy before Run catalog creation
- **THEN** its tools are unavailable to that Run and do not enter model context

### Requirement: Only eligible manifests enter model context
Context assembler SHALL expose only tool manifests that are present in the Run's frozen catalog and currently eligible under Run policy, plan capability requirements, budgets, and bound backend availability.

#### Scenario: Sandbox backend unavailable
- **WHEN** a chart capability is configured but its bound Sandbox Executor is unavailable before Run creation
- **THEN** model context does not include `chart.render` and records the safe capability-unavailable reason

#### Scenario: Ineligible plugin metadata contains instructions
- **WHEN** a disabled or untrusted plugin manifest contains instruction-like descriptions
- **THEN** none of that plugin's tool metadata enters model context

### Requirement: Generic tool result envelope
Every tool executor SHALL return a versioned Tool Result Envelope containing status, structured data, warnings, metrics, and Artifact references, and InvocationPipeline SHALL validate the envelope and declared output schema before producing observations. AgentLoop MUST NOT interpret raw output according to a concrete tool name.

#### Scenario: Process a chart result
- **WHEN** `chart.render` successfully generates a PNG Artifact
- **THEN** the generic pipeline validates its envelope and the registered Chart processor emits an Artifact observation without a Chart branch in AgentLoop

#### Scenario: Tool returns an invalid envelope
- **WHEN** a provider returns a result that violates the envelope or ToolSpec output schema
- **THEN** the ToolCall fails with a bounded invalid-result error and no unvalidated data enters completion context

### Requirement: Auditable tool execution context
InvocationPipeline SHALL construct a ToolExecutionContext for each call containing Run, ToolCall, Step, trace, frozen component identities, and only the authorized Artifact, Sandbox, Workspace, credential, and transport services. Tools MUST NOT infer these associations from global database state.

#### Scenario: Execute a sandboxed chart tool
- **WHEN** InvocationPipeline creates a `chart.render` ToolCall and invokes its bound executor
- **THEN** the executor receives the same ToolCall ID and its SandboxJob and output Artifacts are associated with that Run and ToolCall

#### Scenario: Execute an isolated external tool
- **WHEN** a third-party provider tool is authorized
- **THEN** its transport receives a capability-limited serialized execution context without unrestricted host service objects

### Requirement: Domain-specific processing remains pluggable
The system SHALL select zero or more registered result processors and validators through frozen applicability bindings, SHALL aggregate all applicable validation outcomes in the general Completion Gate, and SHALL NOT select validators through concrete tool-name branches in AgentLoop.

#### Scenario: Complete a non-Web chart task
- **WHEN** Chart Artifact validation succeeds and no Web tool or Web evidence requirement applies
- **THEN** Completion Gate does not require Web fetched sources and can complete the Run

#### Scenario: Run uses Web and chart tools
- **WHEN** one Run produces both Web evidence and a Chart Artifact
- **THEN** both applicable validator outcomes are recorded and aggregated rather than selecting only one domain adapter

#### Scenario: New provider contributes a validator
- **WHEN** a trusted plugin validator applies to an invocation result
- **THEN** the validator participates through its frozen binding without an AgentLoop code change

### Requirement: Preserve existing Web tool behavior during migration
The system MUST preserve `web_search` and `web_fetch` names, input contracts, audit records, Web evidence semantics, and safe output behavior while migrating them into the built-in Web provider plugin.

#### Scenario: Execute an existing Web query
- **WHEN** a user initiates a query previously handled by the Web Agent path
- **THEN** the plugin-based invocation pipeline produces equivalent search, fetch, Evidence Pack, citation validation, and safe failure behavior

#### Scenario: Read a historical Web ToolCall
- **WHEN** a client reads a ToolCall created before the plugin migration
- **THEN** the API continues to deserialize and present its existing tool identity and audit data

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

### Requirement: Tool-name-independent invocation pipeline
The system SHALL execute every tool through a fixed InvocationPipeline covering resolution, schema validation, trusted effect analysis, authorization, execution, envelope validation, persistence, processing, and validation dispatch.

#### Scenario: Built-in Bash invocation modifies workspace
- **WHEN** `bash_execute` is authorized and produces Workspace changes
- **THEN** its registered analyzer, approval presenter, processor, and completion signal handle the invocation without a Bash-specific AgentLoop branch

#### Scenario: Effect analyzer is unavailable
- **WHEN** an invocation has neither a valid bound analyzer nor a valid conservative host fallback
- **THEN** the invocation fails closed before authorization or execution

### Requirement: 工具候选按实际 Agent execution 衰减
系统 SHALL 为每个 AgentExecution 从父级冻结 Tool Catalog、显式 delegated scope、Task/Run policy 和当前基础设施状态解析候选，并 SHALL NOT 因 root Agent 可用而向 child 暴露工具。

#### Scenario: child Catalog 构建
- **WHEN** 子 Agent 启动或从 checkpoint 恢复
- **THEN** Runtime 提供带版本和 digest 的衰减 Catalog，并拒绝恢复期间发生的权限扩大

#### Scenario: child 选择非候选工具
- **WHEN** 模型提出未出现在当前 child Catalog 的工具调用
- **THEN** Tool Router 拒绝执行、记录 identity 和原因，并不把该选择转为隐式审批

### Requirement: 子 Agent 工具调用保留完整委派执行上下文
系统 SHALL 在 ToolExecutionContext 和 PermissionRequest 中包含 child identity、agent execution、delegation chain、budget envelope、Context/DataFlow state 和 Workspace scope。

#### Scenario: 授权子 Agent 工具调用
- **WHEN** child 调用一个候选工具
- **THEN** `authorize_invocation()` 基于实际 child subject 和冻结 effect plan 返回唯一 allow、ask 或 deny 决策

#### Scenario: 工具 provider 尝试使用 root 身份
- **WHEN** child invocation 的 provider 或 adapter 丢弃 child identity 并尝试以 root subject 执行
- **THEN** Runtime fail closed 并记录 execution-context integrity 错误

