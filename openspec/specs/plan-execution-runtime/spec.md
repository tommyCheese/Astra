# plan-execution-runtime Specification

## Purpose
TBD - created by archiving change unify-plan-driven-agent-runtime. Update Purpose after archive.
## Requirements
### Requirement: 版本化计划图是执行计划的唯一事实源

系统 SHALL 将每次可执行计划持久化为版本化 Plan、PlanNode 和 PlanEdge，并 SHALL 从该规范记录生成模型上下文、Run View、timeline 和恢复状态，而不是维护可独立修改的重复计划。

#### Scenario: 新计划持久化为规范图

- **WHEN** planner 或本地策略生成有效 PlanDraft
- **THEN** 系统在同一事务中创建 Plan、全部 PlanNode 和 PlanEdge
- **THEN** Run 和 AgentState 引用该活动计划的标识与版本

#### Scenario: 计划视图被重新读取

- **WHEN** 客户端刷新 Run 或 Agent 开始下一轮决策
- **THEN** 系统从活动计划规范记录构造相同的节点、依赖和状态视图
- **THEN** 不从独立 Step 或 AgentState JSON 副本覆盖规范计划状态

### Requirement: 所有计划在激活前通过 DAG 与契约校验

系统 SHALL 在计划创建或修订生效前验证节点标识、依赖引用、无环性、可达性、成功准则引用、能力声明、风险字段和策略预算。

#### Scenario: Planner 返回循环依赖

- **WHEN** PlanDraft 包含 `A → B → A` 的依赖环
- **THEN** PlanValidator 拒绝激活该计划
- **THEN** 系统记录结构化校验原因并使用允许的修复、回退或阻塞路径

#### Scenario: 节点引用不存在的成功准则

- **WHEN** 节点的 `success_criteria_refs` 不存在于 TaskContract
- **THEN** 计划不会进入 active 状态
- **THEN** 无外部行动基于该非法节点执行

### Requirement: 调度器只选择依赖已满足的节点

系统 SHALL 通过 PlanScheduler 从活动计划中选择 `pending` 且全部必要依赖已完成的 ready node，并 SHALL 禁止模型或工具执行路径绕过该选择。

#### Scenario: 后继节点依赖未完成

- **WHEN** 一个 pending 节点仍依赖未完成的前驱节点
- **THEN** PlanScheduler 不选择该节点
- **THEN** 指向该节点的模型决策被拒绝并记录原因

#### Scenario: 多个节点同时 ready

- **WHEN** 多个节点的必要依赖均已完成
- **THEN** 第一阶段调度器按照稳定顺序选择一个节点执行
- **THEN** 其余 ready 节点保持 pending 且不会被错误标记完成

### Requirement: 计划节点使用受控状态机和节点级检查点

系统 SHALL 通过受控转换维护 PlanNode 的 `pending`、`running`、`completed`、`failed`、`blocked`、`skipped` 状态，并 SHALL 在外部行动前后持久化活动节点和可恢复检查点。

#### Scenario: 节点开始执行

- **WHEN** PlanScheduler 选择一个 ready node
- **THEN** 系统原子地将该节点设为 `running` 并更新 AgentState 的活动节点引用
- **THEN** 下一次工具调用和 AgentTurn 均关联该节点

#### Scenario: 非法状态转换

- **WHEN** 代码或模型尝试把 `pending` 节点直接标记为 `completed` 且没有匹配 Evaluation
- **THEN** 运行时拒绝该转换
- **THEN** 规范计划状态保持不变

### Requirement: 工具成功与节点完成具有不同语义

系统 SHALL 将工具结果先归一化并评估，且 SHALL 仅在节点预期结果和必要验证满足后将节点标记为 completed。

#### Scenario: 工具调用成功但缺少必要字段

- **WHEN** 工具返回成功，但 Observation 缺少节点 expected outcome 要求的字段
- **THEN** Evaluation 返回 `partial`、`mismatch` 或 `inconclusive`
- **THEN** 节点不进入 `completed`

#### Scenario: 节点结果验证通过

- **WHEN** Evaluation 与节点 expected outcome 匹配且不存在阻塞验证错误
- **THEN** 系统将节点设为 `completed` 并持久化证据引用
- **THEN** 调度器可以释放依赖该节点的后继节点

### Requirement: 旧 Run 保持只读兼容

系统 SHALL 允许没有规范 Plan 记录的旧 Run 继续通过兼容投影查看步骤、工具调用、事件和结果，但 MUST NOT 使用新调度器自动恢复旧 Run。

#### Scenario: 查看迁移前完成的 Run

- **WHEN** 客户端读取一个只有旧 Step 和 plan_graph JSON 的历史 Run
- **THEN** API 返回兼容的计划步骤视图
- **THEN** 系统不改写该 Run 的历史步骤标识或证据

### Requirement: 子 Agent Join 形成依赖范围内的计划屏障
系统 SHALL 将 durable Join 绑定到消费其结果的 PlanNode，并 SHALL 仅阻塞该消费节点及其依赖分支，不得因 Join waiting 而暂停不依赖该 Join 的 root 节点。

#### Scenario: Join 等待且存在无依赖节点
- **WHEN** 一个 required Join 仍在等待且另一个 pending root 节点的全部普通依赖与 Join 依赖均已满足
- **THEN** PlanScheduler 不选择 Join consumer 节点
- **THEN** PlanScheduler 可以选择该无依赖节点

#### Scenario: Required Join 被阻塞
- **WHEN** required Join 的必要 child 失败且没有安全重试或替代路径
- **THEN** consumer 节点进入 blocked 或触发受控 replan
- **THEN** 不相关且仍有效的完成节点和 Evidence 保持不变

#### Scenario: First-success Join ready
- **WHEN** first-success Join 的一个 child 产生验证成功结果
- **THEN** Join 可以进入 ready 并解除 consumer 节点屏障
- **THEN** 仅在 loser 无持久副作用或补偿风险时取消其余 child

