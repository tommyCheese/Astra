## MODIFIED Requirements

### Requirement: Run 状态被持久化
系统 SHALL 持久化每一次 Run 状态转换以及 trusted Run 的当前 Plan 引用、Plan 版本摘要和规范图谱，使客户端刷新或后端重启后仍能恢复当前状态。

#### Scenario: 记录状态转换
- **WHEN** 一个 Run 从 planning 进入 executing
- **THEN** Run 记录反映新状态，并包含更新后的时间戳

#### Scenario: 可以重新加载 standard Run 状态
- **WHEN** 客户端通过标识符请求一个已存在的 standard Run
- **THEN** 系统返回该 Run 的持久化状态、tool calls、artifacts 和可用的最终结果
- **THEN** 系统不返回虚构的 Plan 图谱

#### Scenario: 可以重新加载 trusted Run 状态
- **WHEN** 客户端通过标识符请求一个已存在的 trusted Run
- **THEN** 系统返回持久化状态、当前规范 Plan 图谱、版本摘要、steps、tool calls、artifacts，以及可用的最终结果
- **THEN** 图谱节点状态与持久化 PlanNode 记录一致

### Requirement: Runner 创建可审计步骤
系统 SHALL 将 trusted Run 的规划和执行表示为关联到 Run 的版本化 PlanNode 与 PlanEdge 记录，并 SHALL 将实际执行、证据和版本 lineage 关联到稳定节点标识；standard Run SHALL 不创建计划步骤占位。

#### Scenario: 可信计划创建图谱
- **WHEN** 模型为 trusted Run 生成有效计划
- **THEN** 系统创建完整 Plan、PlanNode 和 PlanEdge 记录
- **THEN** 每个节点包含标题、意图、状态、预期结果、成功准则、能力、风险和可选性

#### Scenario: PlanNode 完成时记录证据
- **WHEN** 一个 PlanNode 完成
- **THEN** 系统更新节点状态和时间信息
- **THEN** 系统存储并关联相关工具调用、产物、Evaluation 或验证证据

#### Scenario: 新版本保留节点 lineage
- **WHEN** 重规划以新 Plan 版本替换旧版本
- **THEN** 新版本节点记录其可验证的前序节点 lineage
- **THEN** 被继承的完成状态和证据保持稳定关联

#### Scenario: 快速响应调用工具
- **WHEN** standard Run 调用一个或多个工具
- **THEN** 系统记录真实 ToolCall 和过程事件
- **THEN** 系统不创建 PlanNode 或 PlanEdge

### Requirement: Run timeline 流式推送给客户端
系统 SHALL 在 Run 活跃期间向 Web App 流式推送运行进度、规范 Plan 和节点变化事件，并 SHALL 保留事件顺序和断线恢复能力。

#### Scenario: 客户端接收可信图谱更新
- **WHEN** trusted Run 创建、激活、修订 Plan 或更新节点状态
- **THEN** 系统发送带 Run、Plan、版本和节点稳定标识的类型化事件
- **THEN** 客户端可以增量更新图谱而无需为每个事件重新加载完整 RunView

#### Scenario: 客户端接收普通运行更新
- **WHEN** Run 状态、工具调用或最终结果发生变化
- **THEN** 系统继续发送现有类型化进度事件

#### Scenario: 客户端重连
- **WHEN** 客户端在事件流断开后重新连接
- **THEN** 系统允许客户端按事件 ID 重放缺失事件
- **THEN** 客户端可以获取当前权威 Run 和 Plan 快照进行校正
