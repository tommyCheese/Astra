## MODIFIED Requirements

### Requirement: Agent 创建可验证的任务契约

系统 SHALL 为 trusted 模式创建可持久化的完整 TaskContract；standard 模式 SHALL 不创建 TaskContract、规范计划或 AgentState，并 SHALL 直接进入共享 AgentLoop 的极速回答分支。

#### Scenario: 可信模式明确目标生成完整任务契约

* **WHEN** trusted 模式提交的目标已具备足够信息，可安全开始执行
* **THEN** 任务契约识别所有强制交付物及可验证的成功准则
* **THEN** 后续规划步骤和完整验证均可通过对应标识符引用这些成功准则

#### Scenario: 快速回答跳过契约和计划

* **WHEN** standard 模式创建新 Run
* **THEN** 系统不生成 TaskContract、Plan 或 AgentState
* **THEN** 系统立即开始回答或工具选择模型调用

#### Scenario: 可信模式关键歧义需要澄清

* **WHEN** trusted 模式缺失的信息将实质性影响交付结果、安全性或外部影响
* **THEN** 完整任务契约记录该歧义
* **THEN** 运行进入 `waiting_user`，而不是自行假设缺失的信息

### Requirement: 运行时控制 Agent Loop 的节点顺序

系统 SHALL 通过同一个 AgentLoop 执行 standard 和 trusted 模式；standard 可以跳过可信状态评估节点，但 **MUST NOT** 允许模型输出跳过 ToolRouter、工具参数校验、权限门或执行安全边界。

#### Scenario: 模型提出非法状态转换

* **WHEN** trusted 模式的模型决策尝试从行动选择直接绕过完整完成处理
* **THEN** 运行时拒绝该转换
* **THEN** 最终状态仍由 CompletionGate 判定

#### Scenario: 正常行动轮次完成

* **WHEN** standard 模式的已授权行动返回结果
* **THEN** 运行时归一化工具结果并立即进入下一次回答或工具选择
* **THEN** 不执行 ObservationEvaluator、反思、Memory 写入或任务完成校验
