## ADDED Requirements

### Requirement: 父 Agent 按冻结策略并发创建子 Agent
系统 SHALL 允许 eligible trusted 根 Agent 在同一时刻拥有多个活跃直接子 Agent，并 MUST 通过 Run 累计数、单父累计数、单父活跃数、预算、部署、提供者、工具和能力上限共同约束并发，而不得使用固定单活规则。

#### Scenario: 两个独立子任务并发运行
- **WHEN** 冻结策略允许两个并发 child，两个委派均独立、通过治理校验且预算充足
- **THEN** 系统创建两个不同的 AgentExecution 并允许 Coordinator 同时认领执行
- **THEN** 任一 child 的身份、用量、事件、checkpoint 和结果均不会归属于另一个 child

#### Scenario: 并发达到策略上限
- **WHEN** 父执行的活跃直接子 Agent 数已经达到 `max_parallel_children`
- **THEN** 新委派以稳定的配额拒绝原因失败且不创建部分执行、预算预留或 Join 成员

### Requirement: Fan-out 组与 Join 原子持久化
系统 SHALL 对一个根决策中的全部 DelegationRequest、预算预留、child execution 和 immutable Join 进行完整预检和原子持久化，并 SHALL 使用稳定 group/request idempotency keys 防止重试产生重复 children。

#### Scenario: Fan-out 中一个请求不合法
- **WHEN** 批次中的任一请求超出权限、预算、能力目录、范围或并发上限
- **THEN** 整个 fan-out 组被拒绝
- **THEN** 其他请求不会留下可见 child、预留或不完整 Join

#### Scenario: 相同 fan-out 请求被重试
- **WHEN** 根 Agent 使用相同 group id 和相同冻结请求重试一次已提交 fan-out
- **THEN** 系统返回原有 children 和 Join
- **THEN** 不创建新的 AgentExecution 或重复扣减预算

### Requirement: Swarm 是 Astra runtime built-in
系统 SHALL 以稳定 `swarm` Tool manifest 向 eligible trusted 根 Agent 暴露并发委派，SHALL 使用 `astra.runtime` backend 和 `delegation_create` 权限执行，并 MUST NOT 依赖 Sandbox 可用性或进入 child Tool Catalog。

#### Scenario: Sandbox 不可用但 Swarm 策略允许
- **WHEN**应用 Sandbox 不可用而 trusted Run 的冻结 subagent policy 允许执行
- **THEN** `swarm` 仍可作为 Astra runtime built-in 候选出现
- **THEN** child 只能使用其自身衰减后且实际可用的只读 Catalog

#### Scenario: Swarm 工作组被接受
- **WHEN** `swarm` 请求通过治理并原子提交 group、children 和 Join
- **THEN** Swarm ToolCall 返回 accepted handles 并进入 completed
- **THEN** child AgentExecution 在后台继续且 Join 结果稍后自动注入 parent Observation

### Requirement: 并发 child 使用隔离的运行时上下文
系统 SHALL 为每个并发 child 使用独立数据库 Session、服务实例、模型执行绑定和用量记录上下文；系统 MAY 共享不可变 Catalog 和底层网络连接池，但 MUST NOT 共享可变 AgentExecution 归属。

#### Scenario: 两个 child 同时调用模型
- **WHEN** 两个已认领 child 的模型调用时间发生重叠
- **THEN** 每次模型使用、AgentTurn、事件和 checkpoint 仅关联发起调用的 agent_execution_id

### Requirement: Run 级 Supervisor 管理并发 child 生命周期
系统 SHALL 由 Run 级持久化感知的 Supervisor 调度 queued children、维护 heartbeat、协调恢复与取消并在 Run 结束时结构化关闭 worker；AgentLoop MUST NOT 以进程内 Task 作为 child 状态事实源。

#### Scenario: 根 Agent 在 child 运行时继续工作
- **WHEN** child Join 尚未 ready 且根计划存在不依赖该 Join 的 ready 节点
- **THEN** Supervisor 保持 child 执行
- **THEN** 根 Agent 可继续选择并执行无依赖工作

#### Scenario: 进程在并发执行期间重启
- **WHEN** Supervisor 进程停止且多个 child 具有 stale heartbeat
- **THEN** 恢复扫描分别根据每个 child 的 checkpoint、fencing 和 effect certainty 重新排队、完成、等待确认或 fail closed

### Requirement: Child 结果被恰好一次汇合和消费
系统 SHALL 在 parent 可消费结果前验证 child schema、completion、provenance、Artifact、Evidence 和 lineage，并 SHALL 通过 CAS 保护的 merge/consume 生命周期确保每个 Join 最多生成一个规范 parent observation。

#### Scenario: Ready Join 在 merge 后崩溃
- **WHEN** 系统已经推广验证结果但在标记 Join consumed 前重启
- **THEN** 恢复流程幂等完成消费
- **THEN** 父 AgentState 不包含重复事实、Artifact 推广或重复 Observation

### Requirement: 首次生产并发保持深度一和只读
系统 SHALL 仅向 eligible trusted 根 Agent 暴露并发委派，SHALL 保持 `max_depth = 1` 和 read-only child authority，并 MUST 拒绝 child 创建后代或调用写入型工具。

#### Scenario: Child 尝试继续委派
- **WHEN** depth-one child 生成或构造创建孙 Agent 的请求
- **THEN** 运行时不向其暴露委派能力或以深度拒绝原因拒绝请求
- **THEN** 已有 sibling 和 root 执行不受影响
