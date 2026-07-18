## Context

当前 `ToolSpec`、`Tool`、`ToolRegistry`、Tool Catalog Snapshot 和权限引擎已经形成通用调用基础，但产品装配仍是静态的：`build_tool_registry()` 导入具体工具，`AgentLoop` 创建 Web/Chart processor，`DefaultEffectAnalyzer` 按工具名分支，工具设置 API 维护固定字段。结果是新增工具可以被手工塞进 Registry，却不能在不修改核心代码的情况下完整参与 Effect、审批、结果解释、验证和完成门控。

此次改造横跨工具装配、Agent Runtime、权限、Sandbox、数据库、API 和前端设置。首要约束是插件代码不能削弱宿主权限边界：Task Workspace 中的代码不得被宿主发现或导入；第三方声明不得自行获得权限；Run 恢复时必须继续使用被冻结的组件身份。

## Goals / Non-Goals

**Goals:**

- 新增工具或 provider 无需修改 `AgentLoop`、权限引擎或静态工具设置 schema。
- Web、Chart、Bash 使用与第三方工具相同的贡献点和调用管线。
- 支持本地受管 provider、隔离进程/container provider，未来可接入 MCP adapter。
- 保持 Effect Plan、审批、Workspace、Artifact、DataFlowState 和审计的 fail-closed 属性。
- Run 冻结完整执行组件身份，并可确定性恢复。
- 支持一个 Run 中多个 processor、evidence builder 和 validator 并行适用。
- 通过兼容适配层保持现有工具名称、API 行为和历史 ToolCall 可读性。

**Non-Goals:**

- 本变更不实现公开插件市场、自动下载或在线安装。
- 本变更不允许未经审核的 Python 插件直接加载进 Astra API 进程。
- 本变更不重新设计模型决策协议、计划系统或权限策略语言。
- 本变更不要求第一阶段实现完整 MCP client；只预留隔离 provider adapter 契约。
- 本变更不允许插件替换核心 Policy Gate、Permission Engine、Catalog Freeze 或 Completion Gate。

## Decisions

### 1. 采用贡献点式 Tool Provider Plugin，而不是仅扩展 ToolRegistry

定义宿主版本化协议 `ToolProviderPlugin`，其加载结果为不可变 `PluginContribution`：

```text
PluginContribution
├── descriptor              provider/version/digest/trust/protocol
├── tools[]                 ToolSpec + executor binding
├── effect_analyzers[]      analyzer binding + analyzer identity
├── result_processors[]     applicability + processor identity
├── validators[]            applicability + blocking policy
├── approval_presenters[]   safe preview/grant matcher
├── runtime_backends[]      backend descriptor + health probe
└── configuration_schema    public fields + secret references
```

工具协议只描述单次调用，插件协议描述一个 provider 对宿主的全部贡献。这样 Effect、审批和验证不会继续散落在核心 loop。

替代方案是让每个 `Tool` 自带所有 hook。该方式简单，但会把安全分析器与可能不可信的执行器绑定，并难以共享 provider 级 runtime、配置和健康状态，因此不采用。

### 2. 将声明、可信宿主逻辑和不可信执行代码分层

插件分三层：

1. `PluginDescriptor`：纯数据 manifest，可解析但不执行。
2. `HostContribution`：受管、allowlist 且摘要固定的宿主组件，只能贡献限定接口。
3. `ToolExecutor`：实际工具执行，可位于 in-process、隔离 worker、OCI runtime 或未来 MCP transport。

外部 provider 的 schema 和 annotation 仅作为未授权输入；Effect Analyzer、approval presenter 和资源映射必须来自宿主信任域，或退化到保守的声明式 analyzer。未识别贡献点不加载，未知工具效应默认映射为高风险并要求审批。

Task Workspace、上传文件和项目本地配置永远不进入插件发现路径。

### 3. 使用确定性的 PluginCatalogBuilder 统一装配

应用启动时由 `PluginCatalogBuilder` 执行：discover → parse → verify → load contributions → health probe → resolve conflicts → freeze application catalog。

发现源第一阶段仅包括：

- Astra 内建插件包；
- 管理员明确配置的受管 Python package entry points；
- 管理员明确配置的隔离 provider descriptor。

Catalog key 使用 `(provider_id, tool_name, tool_version)`，模型可见名称仍保持唯一。重复的模型可见工具名、贡献点 ID 或 backend ID 必须启动失败，禁止后注册静默覆盖。排序基于 provider、名称和版本，确保 digest 稳定。

### 4. 将 AgentLoop 中的调用部分抽成 InvocationPipeline

新增 `InvocationPipeline.invoke(request, runtime_context)`，固定执行以下阶段：

```text
Resolve Tool
  → Validate JSON Schema
  → Select Trusted Effect Analyzer
  → Build and Freeze ActionEffectPlan
  → Authorize / Pause for Approval
  → Select Executor Backend
  → Execute with ToolExecutionContext
  → Validate ToolResultEnvelope
  → Persist ToolCall / Artifact / Workspace changes
  → Run Applicable Result Processors
  → Return InvocationOutcome
```

`AgentLoop` 只负责循环状态、模型决策、计划推进、反思和完成门控，不再判断具体工具名或解释原始输出。Pipeline 通过事件/返回值表示 `succeeded | failed | waiting_approval | blocked`，保持现有恢复语义。

### 5. 强制统一 ToolResultEnvelope

所有 executor 必须返回版本化 envelope：`status`、`data`、`warnings`、`metrics`、`artifacts`。Pipeline 在持久化成功前使用 ToolSpec 的 output schema 和宿主 envelope schema 双重验证。

兼容期使用 `LegacyResultAdapter` 包装现有 Web、Chart、Bash 输出。兼容层仅存在于内建插件，不进入 `AgentLoop`。

### 6. Effect Analyzer、Processor 和 Validator 使用显式绑定规则

贡献点包含不可变 applicability：精确 tool identity、capability、output media type 或 result kind。Catalog 构建时将规则编译成索引，运行时不通过 `if tool_name == ...` 分派。

- 每次调用必须恰好选择一个 Effect Analyzer；没有专用 analyzer 时使用宿主默认声明式 analyzer。
- 一个调用可以运行零到多个 processor；processor 只能生成 observation/evidence，不能扩大权限或再次执行工具。
- 一个 Run 可以聚合多个 validator；Completion Gate 汇总全部 blocking/non-blocking outcome。
- approval presenter 只能生成脱敏预览和授权 matcher，最终授权仍由 Permission Engine 决定。

贡献点异常默认只使相关调用失败；安全相关 analyzer、schema 或 identity 异常必须 fail closed。

### 7. 冻结完整 Run Tool Catalog

现有 Tool Catalog Snapshot 扩展为保存：

- plugin/provider identity 与 digest；
- ToolSpec 和 schema digest；
- executor backend identity；
- effect analyzer ID/version/digest；
- processor/validator ID/version/digest；
- resolved configuration revision，排除 secret value；
-最终 applicability bindings。

Run 恢复或审批后继续时，从 snapshot 重建 binding 并与当前 application catalog 比较。任何影响权限、schema、执行或验证语义的漂移都阻止执行；纯展示字段变化不影响恢复。

### 8. 动态工具设置由 Catalog 驱动

将固定 `ToolSettingsUpdate` 改为通用资源：

```text
GET /api/tool-providers
GET /api/tools
PUT /api/tool-providers/{provider_id}/state
PUT /api/tools/{tool_name}/state
PUT /api/tool-providers/{provider_id}/config
```

数据库以 provider/tool identity 保存启用状态和配置 revision。Secret 配置只保存 credential reference，不通过 Catalog API 回传。旧 `PUT /api/tools` 固定字段接口在一个版本周期内转换为新状态写入并返回弃用信息。

### 9. 内建工具先插件化，再开放外部 provider

建立三个内建 provider：

- `astra.web`：`web_search`、`web_fetch`、Web evidence processor/validator 和 web OCI backend；
- `astra.chart`：`chart.render`、Artifact processor/validator 和 data-viz backend；
- `astra.shell`：`bash_execute`、Bash effect analyzer、approval presenter 和 workspace completion signal。

`AgentLoop` 中现有 Web evidence pack、Chart 二选一验证和 Bash quick-completion 分支全部迁移为贡献点。只有在内建插件通过现有回归测试后，才启用受管外部 provider。

### 10. 插件状态机与故障隔离

应用级状态为 `discovered → verified → loaded → healthy → enabled`，并支持 `disabled`、`unhealthy`、`draining`。新 Run 只使用 enabled catalog；运行中的 Run 使用已冻结 snapshot，不因管理员禁用而切换组件，但禁用可阻止尚未获批的高风险调用并按策略进入等待状态。

隔离 provider 必须有超时、取消、最大响应、并发和健康探测。宿主进程插件仅允许平台/managed trust level，加载失败导致该 provider 不可用；内建安全组件加载失败则阻止应用启动。

## Risks / Trade-offs

- [贡献点数量增加导致架构复杂] → 固定少量版本化接口，第一阶段只开放 tools、analyzers、processors、validators、presenters 和 backends。
- [第三方插件在宿主进程执行任意代码] → 外部 provider 默认走隔离 transport；in-process 只允许内建或管理员固定摘要的 managed package。
- [插件声明虚假低风险 Effect] → 只信任宿主 allowlist analyzer；未知工具使用保守 analyzer，Permission Engine 不信任远端 annotation。
- [Catalog 或 binding 漂移破坏恢复] → Run 冻结所有行为组件摘要，恢复前逐项比较并 fail closed。
- [多 validator 导致正常 Run 被意外阻塞] → validator 显式声明 applicability 与 blocking policy，并记录每个 outcome；不存在默认 Web validator。
- [迁移期间双路径产生行为差异] → 旧工具只保留 adapter，不保留第二套执行 loop；用相同 golden ToolCall/Event/Result 测试对比。
- [动态配置泄漏凭据] → manifest 只描述 secret reference，值由 Credential Broker 注入 executor，API 和 snapshot 不保存明文。
- [插件过多增加模型上下文] → Catalog 先按 Run policy、capability、backend health 和计划节点过滤，再向模型暴露工具 manifest。

## Migration Plan

1. 新增 plugin contracts、CatalogBuilder 和只包含内建 provider 的静态发现源，不改变默认行为。
2. 新增 InvocationPipeline，以 feature flag/shadow assertion 对比旧路径生成的 Effect Plan、ToolCall 和 observation。
3. 依次迁移 Web、Chart、Bash；每迁移一个工具即删除对应核心工具名分支。
4. 将 validators 改为多贡献点聚合，并删除固定 Web evidence/Chart 二选一逻辑。
5. 扩展 Tool Catalog Snapshot 与恢复校验，完成数据库迁移和旧 snapshot 兼容读取。
6. 发布动态工具/provider API 和前端页面，保留旧设置接口一个版本周期。
7. 启用 managed package discovery，再启用隔离 provider descriptor；默认不扫描 Task Workspace。
8. 删除 legacy registry builder、静态 toggle 字段和兼容 API。

回滚时关闭外部 discovery，仅装配内建插件；已创建的新 snapshot 保持可读。数据库迁移只增加通用 provider/config/snapshot 字段，兼容阶段不删除旧字段，避免不可逆回滚。

## Open Questions

- managed Python entry point 是否第一版就开放给部署管理员，还是只实现内建插件与隔离 descriptor？
- 插件协议采用纯 Python Protocol，还是同时定义语言无关 JSON/RPC contract 供未来 MCP/sidecar 复用？建议两层并存，Python 仅作为宿主 adapter。
- 管理员禁用 provider 后，已冻结且等待审批的 Run 应直接 blocked，还是允许用户完成一次已冻结调用？需要产品安全策略确认。
- 动态配置 UI 第一版是否支持插件自定义表单 widget，还是仅支持 JSON Schema 基础控件？建议先支持基础控件。
