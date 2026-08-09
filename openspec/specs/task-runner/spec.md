# task-runner Specification

## Purpose
TBD - created by archiving change implement-web-data-query-task-runner. Update Purpose after archive.
## Requirements
### Requirement: 用户可以创建任务运行
系统 SHALL 允许用户从 Web App 提交一个目标，并为该目标创建一个持久化任务运行。

#### Scenario: 成功创建 run
- **WHEN** 用户提交非空目标
- **THEN** 系统创建 Task 记录，创建关联到该 Task 的 Run 记录，并向客户端返回 Run 标识符

#### Scenario: 拒绝空目标
- **WHEN** 用户提交空字符串或只包含空白字符的目标
- **THEN** 系统拒绝请求，并且不创建 Task 或 Run

### Requirement: Run 状态被持久化
系统 SHALL 持久化每一次 run 状态转换，让客户端刷新或后端重启后仍能检查当前状态。

#### Scenario: 记录状态转换
- **WHEN** 一个 run 从 planning 进入 executing
- **THEN** Run 记录反映新状态，并包含更新后的时间戳

#### Scenario: 可以重新加载 run 状态
- **WHEN** 客户端通过标识符请求一个已存在的 run
- **THEN** 系统返回该 run 的持久化状态、steps、tool calls、artifacts，以及可用的最终结果

### Requirement: Runner 创建可审计步骤
系统 SHALL 将 trusted Run 的规划和执行表示为关联到 Run 的规范 PlanNode 记录，并 SHALL 通过 Run View 投影为可审计步骤。standard Run SHALL 不创建 PlanNode 或步骤占位。

#### Scenario: 可信计划创建节点
- **WHEN** 模型为 trusted Run 生成有效完整计划
- **THEN** 系统创建带依赖、意图、状态、预期结果和成功准则的 PlanNode 记录

#### Scenario: 可信节点完成时记录证据
- **WHEN** trusted PlanNode 完成
- **THEN** 系统更新节点状态并存储相关工具调用、产物或验证证据

#### Scenario: 快速响应调用工具
- **WHEN** standard Run 调用一个或多个工具
- **THEN** 系统记录 ToolCall 和过程事件
- **THEN** Run View 的计划步骤保持为空

### Requirement: 工具调用被类型化并记录

系统 SHALL 将每一次工具调用记录为 ToolCall，包含 input、output、status、permission、副作用等级、时间信息、规范 PlanNode 标识，以及适用时的错误详情。

#### Scenario: 记录成功工具调用

- **WHEN** runner 为活动 PlanNode 成功执行一个工具
- **THEN** 系统存储工具名称、版本、输入、输出、状态、权限、副作用等级、开始时间戳和完成时间戳
- **THEN** ToolCall 关联到触发该行动的规范 PlanNode

#### Scenario: 记录失败工具调用

- **WHEN** 一个工具返回错误或超时
- **THEN** 系统存储 failed ToolCall 记录，包含错误详情，并将其关联到相关 Run 和 PlanNode
- **THEN** 工具失败不会通过创建或猜测另一个步骤来改变计划结构

### Requirement: Run timeline 流式推送给客户端
系统 SHALL 在 run 活跃期间向 Web App 流式推送运行进度事件。

#### Scenario: 客户端接收实时更新
- **WHEN** 客户端订阅一个 run 的事件流
- **THEN** 系统发送 run 状态变化、step 更新、工具调用开始、工具调用完成和最终结果可用等事件

#### Scenario: 客户端重连
- **WHEN** 客户端在事件流断开后重新连接
- **THEN** 系统允许客户端获取该 run 当前持久化的 timeline 状态

### Requirement: 最终结果包含证据
系统 SHALL 生成最终结果，用于总结答案并标识支撑该答案的证据。

#### Scenario: 已完成 run 的结果
- **WHEN** 一个 run 成功完成
- **THEN** 最终结果包含摘要、发现、来源引用或产物引用、限制说明和验证备注

#### Scenario: 带警告完成
- **WHEN** 一个 run 生成了答案，但部分来源、工具或验证检查失败
- **THEN** 最终结果包含答案，并清晰标识警告或限制

### Requirement: 模型配置外部化
系统 SHALL 从后端配置加载模型提供方凭据和模型设置，而不是将其硬编码。

#### Scenario: 缺少模型凭据
- **WHEN** 必需的模型凭据未配置
- **THEN** 系统让模型支撑的 run 执行以清晰的配置错误失败，并且不暴露 secret 值

#### Scenario: 已配置模型客户端
- **WHEN** 存在有效的模型配置
- **THEN** runner 可以从配置的模型提供方请求结构化规划和综合输出

### Requirement: Trusted Run 通过版本绑定确认开始计划执行
系统 SHALL 在 trusted Run 选择确认行为时持久化完整 Plan 和 continuation request，并 SHALL 仅通过共享续跑协议激活用户确认的精确 Plan 版本。系统 MUST NOT 恢复旧的独立 plan-only 激活 API。

#### Scenario: 客户端请求旧激活端点
- **WHEN** 客户端请求已删除的 `/runs/{run_id}/activate-plan` 端点
- **THEN** API 不匹配该路由
- **THEN** Run 状态不发生变化

#### Scenario: 确认当前计划版本
- **WHEN** waiting_user Run 收到匹配的一次性 Plan 确认
- **THEN** 系统激活确认的 Plan 并恢复 Agent Loop
- **THEN** Run 不创建第二份计划

#### Scenario: 拒绝过期计划确认
- **WHEN** Plan 确认引用的版本不是 Run 当前等待的版本
- **THEN** 系统拒绝确认并保持 Run 不执行

### Requirement: 活动旧模式 Run 在升级时终止
系统 SHALL 在单向迁移中取消包含删除模式值的非终态 Run，并 SHALL 不允许其在新运行时恢复。

#### Scenario: 迁移等待激活的 plan-only Run
- **WHEN** 数据迁移发现 plan-only、planning、executing 或 waiting_user 状态且 Profile 包含删除值的 Run
- **THEN** 迁移将该 Run 标记为 cancelled
- **THEN** 终止原因标识模式升级

### Requirement: Runner 管理 SubagentSupervisor 的完整生命周期
系统 SHALL 在 eligible trusted Run 执行期间启动一个 Run-scoped SubagentSupervisor，并 SHALL 在正常完成、等待、取消、失败和进程恢复路径中协调 worker、heartbeat、fencing、Join reconciliation 和结构化关闭。

#### Scenario: Run 正常完成
- **WHEN** 根 Agent 满足完成门且所有 mandatory child 工作已消费
- **THEN** Runner 停止接受新的委派、等待 Supervisor 完成持久化协调并关闭进程内 worker
- **THEN** Run 终态与 child、Join、预算和事件状态一致

#### Scenario: 用户取消整个 Run
- **WHEN** 多个 child 正在 queued、running 或 waiting
- **THEN** Runner 先持久化 Run/child cancellation epochs 和 fencing
- **THEN** Supervisor descendant-first 取消可中断工作并保留 immutable-effect 与 result-unknown 报告

#### Scenario: Kill switch 在运行中开启
- **WHEN** kill switch 阻止新的 fan-out 且已有 child 尚未终态
- **THEN** Runner 不创建新 child
- **THEN** 已有 child 根据 drain/cancel 策略进入受控终态而不丢失 lineage

### Requirement: Runner 记录可信自动化触发来源
系统 SHALL 允许可信的内部调度服务幂等创建 Run，并在 execution profile 和 timeline 中持久化不可由 prompt 伪造的触发元数据。

#### Scenario: 定时任务创建 Run
- **WHEN** 调度服务使用已领取的 schedule run 创建 Run
- **THEN** Run 记录触发类型、job id、schedule run id、逻辑计划时间和内部 principal

#### Scenario: 重复派发同一 schedule run
- **WHEN** 调度服务因恢复而重复派发同一个 schedule run id
- **THEN** Runner 返回原有关联 Run 而不创建第二个 Run

### Requirement: Run 持久化独立 Agent execution 树
系统 SHALL 为每个 Run 持久化一个 root AgentExecution 及零个或多个 descendant executions，并将相关 Plan、NodeExecution、Turn、ToolCall、Approval、Artifact 和 usage 关联到实际执行 Agent。

#### Scenario: 兼容现有单 Agent Run
- **WHEN** 一个 Run 未启用或未选择子 Agent
- **THEN** 所有执行记录归属 root execution，现有 API 行为和终态保持兼容

#### Scenario: 重新加载多 Agent Run
- **WHEN** 客户端或恢复器按标识读取含 children 的 Run
- **THEN** 系统返回持久化的 Agent 树、各自状态/checkpoint、Plan/Node 状态、结果和 lineage

### Requirement: Runner 分层协调 Agent 和节点并发
系统 SHALL 先在 Run 与部署限制内调度 AgentExecution 槽，再在每个 execution 的 allowance 内调度 Plan nodes，并统一应用 provider、工具、资源租约和预算背压。

#### Scenario: Agent 与节点槽竞争
- **WHEN** 多个 children 及其内部节点同时 ready
- **THEN** Runner 不超过任何 Run、provider、Agent 或 node 并发上限，并持久化等待原因

