## MODIFIED Requirements

### Requirement: Agent 创建可验证的任务契约

系统 SHALL 仅为 trusted Run 创建可持久化的完整 TaskContract；standard Run SHALL 不创建 TaskContract、规范计划或 AgentState，并 SHALL 直接进入共享 Agent Loop 的快速分支。

#### Scenario: 可信执行生成完整任务契约

* **WHEN** trusted 模式提交的目标已具备足够信息，可安全开始执行
* **THEN** 任务契约识别所有强制交付物及可验证的成功准则
* **THEN** 完整 DAG 和最终验证均通过稳定标识符引用这些成功准则

#### Scenario: 快速响应跳过契约和计划

* **WHEN** standard 模式创建新 Run
* **THEN** 系统不生成 TaskContract、Plan 或 AgentState
* **THEN** 系统立即开始回答或工具选择

#### Scenario: 可信执行关键歧义需要澄清

* **WHEN** trusted 模式缺失的信息将实质性影响交付结果、安全性或外部影响
* **THEN** 完整任务契约记录该歧义
* **THEN** 运行进入 `waiting_user`，而不是自行假设缺失的信息

### Requirement: Agent 状态具有统一规范且支持版本管理

系统 SHALL 为 trusted Run 维护统一且版本化的 AgentState；standard Run SHALL 不创建可信 AgentState。trusted AgentState 包含任务契约、Profile 引用、计划版本、成功准则状态、观察结果、失败指纹、预算和终止意图。

#### Scenario: 可信执行下一轮接收更新状态

* **WHEN** trusted 行动产生新的观察与评估
* **THEN** 系统在下一次决策前持久化状态更新
* **THEN** 下一次决策接收新的状态版本

#### Scenario: 快速响应工具调用完成

* **WHEN** standard 工具调用返回规范化结果
* **THEN** 系统进入下一次快速回答或工具选择
* **THEN** 系统不创建用于模拟可信生命周期的 AgentState

### Requirement: 计划采用可执行且可修订的图结构

系统 SHALL 为每个 trusted Run 将完整计划表示为带版本的 DAG，并 SHALL 在首次外部行动前持久化全部初始节点和依赖。standard Run SHALL 不创建 DAG。

#### Scenario: 可信依赖阻塞节点执行

* **WHEN** trusted 计划节点存在尚未满足的必要依赖
* **THEN** 控制器不会执行该节点
* **THEN** 控制器选择 ready 节点、受限重规划、请求用户协助或进入阻塞状态

#### Scenario: 可信重新规划保留有效成果

* **WHEN** 计划级反思替换了 trusted DAG 的失效分支
* **THEN** 系统创建经过完整校验的新计划版本
* **THEN** 不受影响的已完成节点及其证据继续有效

#### Scenario: 快速响应不创建占位图

* **WHEN** standard Run 产生一个或多个工具调用
* **THEN** 工具调用保持关联到 Run
* **THEN** 系统不创建占位 Plan 节点或依赖边

### Requirement: 每一次行动决策均以成功准则为导向

系统 SHALL 要求 trusted 可执行决策引用活动 DAG 节点、预期观察和成功准则。standard 决策 SHALL 使用快速决策协议，并仍须通过工具输入与安全策略校验。

#### Scenario: 可信工具决策信息完整

* **WHEN** trusted 控制器选择执行工具
* **THEN** 决策指定工具输入、活动节点、预期结果及相关成功准则
* **THEN** 执行前由策略门验证该决策

#### Scenario: 快速工具决策通过安全边界

* **WHEN** standard 控制器选择执行工具
* **THEN** 决策提供合法工具输入并通过共享策略门
* **THEN** 决策无需引用不存在的 Plan 节点

### Requirement: 观察结果统一归一化并依据预期进行评估

系统 SHALL 归一化所有模式的工具结果与失败。trusted Run SHALL 针对活动节点预期生成 Evaluation；standard Run SHALL 将规范化结果直接提供给下一次快速决策而不创建可信 Evaluation。

#### Scenario: 可信工具成功但未满足节点意图

* **WHEN** trusted 工具调用成功但观察未满足活动节点预期
* **THEN** Evaluation 为 mismatch、partial 或 inconclusive
* **THEN** 活动节点不被标记为完成

#### Scenario: 快速工具结果返回循环

* **WHEN** standard 工具调用产生规范化结果
* **THEN** 结果返回共享 Agent Loop 的快速决策上下文
* **THEN** 系统不运行节点完成评估

### Requirement: 运行时控制 Agent Loop 的节点顺序

系统 SHALL 通过同一个 Agent Loop 执行 standard 和 trusted 模式。trusted MUST 经过 DAG 调度、状态持久化、节点评估和 CompletionGate；standard 可以跳过这些可信节点，但 MUST NOT 跳过 ToolRouter、输入校验、权限门或执行安全边界。

#### Scenario: 可信模型尝试跳过完成处理

* **WHEN** trusted 模型决策尝试从行动选择直接进入 completed
* **THEN** 运行时拒绝该转换
* **THEN** 最终状态仍由节点状态和 CompletionGate 判定

#### Scenario: 快速行动轮次完成

* **WHEN** standard 已授权行动返回结果
* **THEN** 运行时归一化工具结果并进入下一次回答或工具选择
* **THEN** 不执行 DAG 节点评估或可信完成验证
