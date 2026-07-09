## ADDED Requirements

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
系统 SHALL 将规划和执行的工作表示为关联到 Run 的有序 Step 记录。

#### Scenario: 计划创建 steps
- **WHEN** 模型为一个 run 生成有效计划
- **THEN** 系统创建有序 Step 记录，包含标题、意图、状态，以及可用的成功标准

#### Scenario: Step 完成时记录证据
- **WHEN** 一个 step 完成
- **THEN** 系统更新 Step 状态，并存储描述相关工具调用、产物或验证结果的证据

### Requirement: 工具调用被类型化并记录
系统 SHALL 将每一次工具调用记录为 ToolCall，包含 input、output、status、permission、副作用等级、时间信息，以及适用时的错误详情。

#### Scenario: 记录成功工具调用
- **WHEN** runner 成功执行一个工具
- **THEN** 系统存储工具名称、版本、输入、输出、状态、权限、副作用等级、开始时间戳和完成时间戳

#### Scenario: 记录失败工具调用
- **WHEN** 一个工具返回错误或超时
- **THEN** 系统存储 failed ToolCall 记录，包含错误详情，并将其关联到相关 Run 和 Step

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
