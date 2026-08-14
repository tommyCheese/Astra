## Why

Astra 已有插件化工具调用、Effect 分析、统一授权、Run 事件与恢复机制，但缺少一个受治理的生命周期 Hook 契约；目前用户若要在提示提交、模型调用、工具执行、压缩、子 Agent 或完成阶段接入审计、策略检查和自动化，只能修改核心运行时，或退化为不可审计且可能绕过权限边界的脚本。业界 Agent Hook 已开始形成跨产品的事件命名与 JSON 输入输出惯例，现在适合在 Astra 内建立原生、可恢复且 fail-closed 的实现。

## Current Implementation Baseline (2026-08-12)

- 当前代码没有 Hook manifest、catalog、dispatcher、execution record 或 delivery outbox；本变更仍是未实现提案。
- Agent Runtime 已收敛为 `application/agent_runtime/contracts.py`、`composition.py` 与 `loop.py` 的固定 capability slots；Run 生命周期属于 `application/run_management/`，并行计划属于 `application/planning/`，Subagent 与压缩各有独立 application package。
- Hook 不新增任意 middleware slot，也不把分支散布进 canonical Loop。运行时观察通过现有受信 `LifecycleObserver` slot 投影；会改变 admission 的 Hook 只接入 Run/prompt、ActionBoundary/InvocationPipeline、context compaction、Subagent delegation 与 completion 等明确 application boundary。
- AG-UI 与 Hook 都只能从 canonical RunEvent/Run snapshot 派生各自投影；两者不得互相消费对方的 outbox。Hook 管理前端后续应复用 AG-UI 提案形成的 transport-neutral store/component 边界，但不以 AG-UI 成为默认传输为前置条件。

## What Changes

- 引入版本化的 Agent Hook manifest、事件 envelope、matcher、handler、结果与执行记录，覆盖 Run、prompt、model、tool、approval、compaction、subagent 和 completion 生命周期。
- 区分同步 admission hook 与异步 observation hook：前者可拒绝、要求审批、提出受限输入补丁或附加受标记上下文；后者用于审计、通知、指标和外部集成，不阻塞主执行路径。
- 建立确定性的 Hook Registry、来源层级、优先级、冲突规则、Run 级快照和恢复校验，并复用受管插件的来源、摘要、启停与健康治理基础。
- 为命令、HTTP 和宿主管理 handler 提供有界执行后端；第三方命令默认在隔离 runtime 中运行，HTTP handler 使用受限目标、凭据引用、超时、输出上限和幂等键。
- 将 Hook 结果与 Permission Engine 合成：Hook 只能维持或收紧权限，不能通过 `allow` 绕过平台 deny、Effect 策略、Grant、Sandbox、预算或敏感数据规则。
- 对工具输入补丁执行 JSON Schema 校验、重新 Effect 分析、Effect Plan 冻结和重新授权；禁止“批准原调用、执行修改后调用”。
- 为 post/observation hook 增加事务 outbox、至少一次投递、幂等重试、死信状态和手动重放；同步 hook 不在数据库事务内调用外部代码。
- 增加 Hook Catalog、配置校验、启停、试运行、执行历史、延迟/失败诊断和安全审计 API，并在前端提供管理与 Run 时间线视图。
- 提供 Claude Code/Copilot 风格常用事件与 command hook 配置的显式导入适配器；导入只生成待审核 manifest，不自动信任或执行 Workspace 中的 Hook。
- 第一阶段不开放模型判定型或 Agent 型 handler，也不允许 Hook 任意改写模型响应、规范状态、权限记录或完成证明。

## Capabilities

### New Capabilities

- `governed-agent-hook-system`: 定义 Hook 生命周期事件、注册与匹配、同步 admission、异步 delivery、执行后端、组合语义、恢复、可观察性和管理体验。

### Modified Capabilities

- `extension-trust-and-delegation`: 将 Hook manifest、handler 代码、HTTP 目标和配置纳入来源、摘要、管理员策略、Workspace 隔离及变更后重新授权要求。
- `agent-permission-control`: 要求所有 Hook 副作用使用独立 Hook principal 进入统一授权入口，并禁止 Hook 扩权、自批或覆盖平台拒绝。
- `policy-driven-tool-runtime`: 在工具调用管线加入受治理的 pre/post Hook admission 点，并要求任何输入补丁重新分析 Effect、冻结身份和授权。

## Impact

- 后端：新增 `application/hooks/` 契约与编排；接入 `application/run_management/`、`application/agent_runtime/services/tooling/action_boundary.py`、`application/agent_runtime/services/execution/tool_action.py`、`application/context_compaction/`、`application/subagents/` 和 completion boundary；持久化/handler/API 分别落在 `infrastructure/` 与 `interfaces/`。
- 数据：新增 Hook definition/version、Run binding snapshot、execution/delivery/outbox/dead-letter 与 audit 记录及迁移。
- 运行时：新增隔离 command runner、受限 HTTP transport、幂等/超时/取消/输出截断和 Hook principal。
- 前端：新增 Hook 管理、导入预览、试运行、健康/延迟诊断和 Run 级 Hook 执行时间线。
- 兼容性：不改变现有 Tool Provider Plugin、Skill 或 Run API 的默认行为；未配置 Hook 时运行路径应保持等价。
- 测试与运维：增加组合顺序、fail-open/fail-closed、补丁重授权、重启恢复、outbox 重放、恶意 Workspace Hook、SSRF/命令注入、递归与资源耗尽测试。
