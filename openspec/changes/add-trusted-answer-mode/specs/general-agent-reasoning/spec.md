## MODIFIED Requirements

### Requirement: Agent 创建可验证的任务契约

系统 SHALL 为所有运行创建可持久化的 TaskContract 基础结构；trusted 模式 SHALL 生成包含交付物、约束、禁止操作、假设、风险、验证要求及稳定成功准则标识符的完整契约，standard 模式 SHALL 使用不触发额外模型调用的最小系统契约。

#### Scenario: 可信模式明确目标生成完整任务契约

* **WHEN** trusted 模式提交的目标已具备足够信息，可安全开始执行
* **THEN** 任务契约识别所有强制交付物及可验证的成功准则
* **THEN** 后续规划步骤和完整验证均可通过对应标识符引用这些成功准则

#### Scenario: 快速回答创建最小契约

* **WHEN** standard 模式创建新 Run
* **THEN** 系统直接生成用于共享状态和计划基础设施的最小契约
* **THEN** 系统不为生成该契约增加一次模型调用

#### Scenario: 可信模式关键歧义需要澄清

* **WHEN** trusted 模式缺失的信息将实质性影响交付结果、安全性或外部影响
* **THEN** 完整任务契约记录该歧义
* **THEN** 运行进入 `waiting_user`，而不是自行假设缺失的信息

### Requirement: 运行时控制 Agent Loop 的节点顺序

系统 SHALL 通过同一个运行时节点转换图执行 standard 和 trusted 模式，并且 **MUST NOT** 允许模型输出跳过权限门、观察归一化、状态持久化或该模式要求的完成处理。

#### Scenario: 模型提出非法状态转换

* **WHEN** 任一模式的模型决策尝试从行动选择直接绕过运行时进入 completed
* **THEN** 运行时拒绝该转换
* **THEN** 最终状态仍由共享 finalization 路径按生效 profile 判定

#### Scenario: 正常行动轮次完成

* **WHEN** 任一模式的已授权行动返回结果
* **THEN** 运行时按顺序执行观察归一化、结果评估、状态持久化以及该 profile 允许的反思与完成处理
