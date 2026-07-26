## MODIFIED Requirements

### Requirement: Run 状态被持久化
系统 SHALL 持久化每一次 run 状态转换，以及 trusted Run 的当前 Plan、活动 NodeExecution 集合、并发槽位、预算预留和等待原因，让客户端刷新或后端重启后仍能恢复当前状态。

#### Scenario: 记录状态转换
- **WHEN** 一个 run 从 planning 进入 executing
- **THEN** Run 记录反映新状态，并包含更新后的时间戳

#### Scenario: 可以重新加载 run 状态
- **WHEN** 客户端通过标识符请求一个已存在的 run
- **THEN** 系统返回该 run 的持久化状态、steps、tool calls、artifacts，以及可用的最终结果

#### Scenario: 重新加载并行 trusted Run
- **WHEN** 客户端重新加载包含多个活动节点的 trusted Run
- **THEN** 系统返回每个活动 execution 的 PlanNode、attempt、阶段、时间和等待原因
- **THEN** 恢复结果不依赖原进程中的协程对象

### Requirement: Runner 创建可审计步骤
系统 SHALL 将规划和执行工作表示为关联到 Run 的 PlanNode、NodeExecution 和实际行动记录，并 SHALL 通过稳定标识保留并行分支、attempt、工具、证据和状态转换之间的关联。

#### Scenario: 计划创建 steps
- **WHEN** 模型为一个 run 生成有效计划
- **THEN** 系统创建有序 Step 或 PlanNode 记录，包含标题、意图、状态，以及可用的成功标准

#### Scenario: Step 完成时记录证据
- **WHEN** 一个 step 完成
- **THEN** 系统更新 Step 状态，并存储描述相关工具调用、产物或验证结果的证据

#### Scenario: 并行节点分别完成
- **WHEN** 两个 NodeExecution 以不同顺序完成
- **THEN** 每个终态和证据提交到其对应 PlanNode
- **THEN** 完成顺序不会改变规范 DAG 的依赖或节点 index

### Requirement: 工具调用被类型化并记录
系统 SHALL 将每一次工具调用记录为 ToolCall，包含 input、output、status、permission、副作用等级、时间信息、PlanNode 与 NodeExecution 关联，以及适用时的错误详情。

#### Scenario: 记录成功工具调用
- **WHEN** runner 成功执行一个工具
- **THEN** 系统存储工具名称、版本、输入、输出、状态、权限、副作用等级、开始时间戳和完成时间戳

#### Scenario: 记录失败工具调用
- **WHEN** 一个工具返回错误或超时
- **THEN** 系统存储 failed ToolCall 记录，包含错误详情，并将其关联到相关 Run 和 Step

#### Scenario: 并行工具调用
- **WHEN** 不同 NodeExecution 同时调用工具
- **THEN** 每个 ToolCall 记录独立 execution attempt、幂等键和时间区间
- **THEN** 审计记录可以证明调用是否真实重叠且不会混淆输出归属

### Requirement: Run timeline 流式推送给客户端
系统 SHALL 在 run 活跃期间按 Run 级持久化序列向 Web App 流式推送运行进度、并行调度和节点 execution 事件。

#### Scenario: 客户端接收实时更新
- **WHEN** 客户端订阅一个 run 的事件流
- **THEN** 系统发送 run 状态变化、step 更新、工具调用开始、工具调用完成和最终结果可用等事件

#### Scenario: 客户端重连
- **WHEN** 客户端在事件流断开后重新连接
- **THEN** 系统允许客户端获取该 run 当前持久化的 timeline 状态

#### Scenario: 并行节点产生事件
- **WHEN** 多个 Worker 在重叠时间内更新状态
- **THEN** 每个事件携带 Plan 版本、PlanNode、execution attempt 和调度批次标识
- **THEN** Run 级序列可重放且不以事件写入顺序伪造执行依赖
