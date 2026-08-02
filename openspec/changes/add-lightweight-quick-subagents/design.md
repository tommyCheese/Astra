## Context

Astra 已有一套完整的受治理子 Agent 运行时：`swarm` built-in 负责提交结构化 fan-out，`SubagentSupervisor` 管理 durable children、heartbeat、Join、reconciliation、取消与恢复，AgentLoop 负责选择工具并消费合并后的 Observation。当前这些组件被 `answer_mode == trusted` 和 `not quick_mode` 两处硬门限制；standard profile 还会丢弃已编译的 Subagent policy。因此快速模式无法复用它们。

standard Run 已经进入同一个 AgentLoop，但不会创建 TaskContract、Plan、AgentState 或规范 DAG。轻量 Subagent 必须保留这个快速路径，不能为了委派而把 standard 包装成简化 trusted，也不能复制 Supervisor、child executor 或 Join 实现。

## Goals / Non-Goals

**Goals:**

- 让 eligible standard 根 Agent 在无规范 DAG 的情况下直接使用现有 `swarm`。
- 让 `auto` 保持机会式委派，让显式 `/subagent` 使用 `required` 并至少创建一个 Swarm group。
- 通过一个共享的 eligibility/profile 帮助函数驱动 Tool 暴露、Supervisor 创建和 Run 创建校验，消除 scattered mode checks。
- 保留现有 durable child、权限衰减、只读 Catalog、预算、Join、取消、恢复和最终答案所有权。
- 让快速模式使用更紧预算、基础验证和紧凑 UI，同时保持 trusted 的 DAG 与严格 Completion Gate 不变。

**Non-Goals:**

- 不为 standard Run 创建 TaskContract、Plan、AgentState 或 DAG。
- 不增加第二套 QuickSubagentSupervisor、数据表、事件体系或 child executor。
- 不开放递归委派、写入型 child 工具或多层 Agent 树。
- 不让所有快速请求强制创建 child；普通快速请求仍由模型和收益门决定是否委派。

## Decisions

### 1. 以冻结策略而不是 answer mode 决定运行时资格

新增共享的 Subagent eligibility 判断，输入 answer mode、冻结 policy、实时 Swarm 开关和 rollout 状态，输出是否可执行及稳定原因。AgentLoop 的 Tool Catalog、Supervisor 生命周期和 API required-mode 校验都调用同一判断。answer mode 只决定策略 profile 和验证等级，不再直接决定是否存在 Supervisor。

采用共享判断而不是在三个调用点分别放宽 `trusted` 条件，可以避免策略漂移和重复分支。

### 2. standard 与 trusted 共享同一个 Supervisor

快速 Run 创建与 trusted 相同的 root AgentExecution，并实例化现有 `SubagentSupervisor`。`swarm` 请求、child context、Join reconciliation、pending wait、取消和恢复全部走现有路径。standard 没有 active Plan node 时，委派 scope 绑定到根 Run/turn，而不是构造虚假的 PlanNode。

另建轻量 Supervisor 会复制最敏感的并发、租约、预算和恢复逻辑，因此不采用。

### 3. profile 差异集中在 PolicyCompiler 边界

standard profile 继续强制 fast reasoning、basic verification、无 Plan execution，但不再用空策略覆盖 server 编译结果。编译器为 standard 应用保守上限：depth one、read-only、较少 children/parallelism 及更低 round-trip、wall-time、token、call 和 cost 配额。trusted 保持现有策略上限。

首个实现复用现有配置值并通过 profile clamp 形成快速上限，避免立即复制一整组配置；后续只有在运营数据证明需要独立调优时再增加设置项。

### 4. `/subagent` 遵循当前回答模式

Composer 消费命令前缀并保留参数为 goal。当前为 standard 时创建 `standard + required + plan_execution = null`；当前为 trusted 时继续创建 `trusted + required + plan_execution = auto`。两者都不把 slash 原文写入对话。

这让命令始终表示“必须委派”，而可信开关只控制是否需要 DAG 与严格验证。

### 5. 完成判断分层但共享 Join 门

所有模式在 finalize 前都必须等待 pending children，reconcile Join，并消费 required/first-success Join。`required` 模式在没有任何根 Join 时拒绝 finalize。trusted 随后继续走 Plan/criteria/CompletionGate；standard 在共享 Subagent 门通过后按现有 basic finalization 完成。

### 6. UI 复用 SubagentPanel，不把快速 Run 接入 Graph Workbench

现有 `SubagentPanel` 已由 `subagent_summary.total` 驱动，与 answer mode 无关，可直接用于快速 Run。standard 的 `plan_graph` 保持空，因此不会挂载可信右侧图谱。只补充快速模式测试和必要的文案，不复制树组件。

## Risks / Trade-offs

- [快速请求因模型误用 fan-out 增加延迟和成本] → 默认 `auto`，使用紧预算和收益提示；显式命令才使用 `required`。
- [放宽 mode check 导致策略绕过] → 所有入口统一调用冻结 policy + live switch + rollout eligibility，并保留服务端二次校验。
- [standard 没有 Plan node 导致审计引用为空] → child 绑定稳定 root execution、turn 和 group；不得生成 DAG 占位。
- [共享 Supervisor 内部暗含 trusted 假设] → 增加 standard 无 canonical plan 的集成测试，修正假设而不是复制实现。
- [快速与可信 UI 混淆] → standard 仅显示紧凑 SubagentPanel，可信 DAG 仍只由 trusted plan snapshot 驳动。

## Migration Plan

1. 先放开 schema/profile 并保持快速 rollout 不可执行，验证历史 Run 兼容性。
2. 接通共享 eligibility 与 Supervisor，运行后端 unit/integration tests。
3. 更新 `/subagent` 前端路由并验证 standard 不生成 DAG。
4. 在 read-only、depth-one 小流量 cohort 开启快速 Subagent；监控委派率、延迟、成本、失败和无收益 fan-out。
5. 回滚时关闭现有 Swarm 开关或 kill switch；新 Run 不再获得资格，已创建 children 按既有冻结合同收敛。

## Open Questions

- 是否在后续版本为快速模式增加独立的用户开关；首版继续复用唯一 Swarm 开关。
- 是否需要独立 quick rollout cohort；首版可复用 executable read-only cohort并在 profile 中收紧上限。
