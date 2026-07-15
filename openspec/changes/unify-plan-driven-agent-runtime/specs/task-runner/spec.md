## MODIFIED Requirements

### Requirement: Runner 创建可审计步骤

系统 SHALL 将规划和执行工作表示为关联到 Run 和版本化 Plan 的规范 PlanNode 记录，并 SHALL 使用同一节点标识关联依赖、状态、ToolCall、Observation、Evaluation、Evidence 和 timeline。

#### Scenario: 计划创建规范节点

- **WHEN** planner 或本地策略为一个 Run 生成有效计划
- **THEN** 系统创建 PlanNode 和 PlanEdge，包含稳定节点键、标题、意图、状态、依赖、能力、预期结果和成功准则引用
- **THEN** Run View 中的 steps 从这些规范节点投影

#### Scenario: 节点完成时记录证据

- **WHEN** 一个节点的预期结果经 Evaluation 验证完成
- **THEN** 系统更新该 PlanNode 状态，并存储相关工具调用、产物、观察或验证结果的证据引用
- **THEN** 不存在独立 Step 状态与 PlanNode 状态相互冲突

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

