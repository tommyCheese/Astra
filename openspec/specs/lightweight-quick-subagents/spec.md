# lightweight-quick-subagents Specification

## Purpose
TBD - created by archiving change add-lightweight-quick-subagents. Update Purpose after archive.
## Requirements
### Requirement: 快速运行可以使用受治理的轻量 Subagent
系统 SHALL 允许 eligible standard 根 Agent 在不创建 TaskContract、Plan、AgentState 或规范 DAG 的情况下调用现有 `swarm` built-in，并 MUST 通过现有 SubagentSupervisor 创建和管理 child。

#### Scenario: 快速根 Agent 选择并发委派
- **WHEN** standard Run 的冻结 Subagent policy、实时 Swarm 开关和 rollout 策略均允许执行，且根 Agent 选择有效 `swarm` 请求
- **THEN** 系统通过共享 Supervisor 原子创建 group、children 和 Join
- **THEN** Run 不创建可信 Plan 或 DAG 占位

#### Scenario: 快速运行不具备委派资格
- **WHEN** Swarm 已关闭、kill switch 生效、rollout 不可执行或冻结策略禁用 Subagent
- **THEN** standard 根 Agent 的 Tool Catalog 不包含 `swarm`
- **THEN**构造的委派请求仍被运行时拒绝

### Requirement: 快速与可信模式共享子 Agent 治理运行时
系统 MUST 为 standard 和 trusted Run 复用同一套 AgentExecution、Delegation、SubagentSupervisor、child executor、Join reconciliation、预算、取消与恢复实现，并 MUST NOT 维护语义重复的快速专用执行器。

#### Scenario: 快速 child 执行和汇合
- **WHEN** standard Run 创建的 child 完成或失败
- **THEN**共享 reconciliation 路径验证结果并推进 Join
- **THEN**根 Agent 仅消费经过净化和验证的 Observation

#### Scenario: 快速 Run 在 child 活跃时恢复
- **WHEN**进程在 standard Run 的 child 或 Join 尚未终结时重启
- **THEN**共享恢复路径根据持久化状态继续监督或 fence 执行
- **THEN**系统不依赖进程内 Task 作为权威状态

### Requirement: 快速 Subagent 使用保守边界
系统 SHALL 对 standard Run 的 Subagent policy 应用不宽于 trusted policy 的 children、parallelism、depth、wall-time、round-trip、token、call、cost、provider、tool 和 capability 上限，并 MUST 保持首发 child authority 为 read-only 且 `max_depth = 1`。

#### Scenario: 快速策略请求超过上限
- **WHEN**客户端或模型请求超过冻结快速策略的 fan-out、预算、权限或深度
- **THEN**运行时拒绝或收紧请求
- **THEN**不得使用 trusted 上限扩大快速 child 权限

### Requirement: 快速完成必须等待共享 Subagent 门
系统 SHALL 在 standard Run 输出最终答案前等待 pending children、完成 Join reconciliation 并消费必需 Join；`subagent_mode = required` 的 Run MUST 至少创建一个 governed Swarm group 才能成功完成。

#### Scenario: Required 快速 Run 未创建 child group
- **WHEN**standard required-subagent Run 尝试 finalize 且根 execution 不存在 Join
- **THEN**运行时拒绝 finalize 并要求调用 `swarm`

#### Scenario: 快速 Run 等待并发结果
- **WHEN**standard 根 Agent 尝试 finalize 但 child 或 Join 仍处于 pending
- **THEN**运行时等待、reconcile 并将结果作为 Observation 返回下一轮
- **THEN**Run 不执行可信 Plan 节点评估或 Completion Gate

