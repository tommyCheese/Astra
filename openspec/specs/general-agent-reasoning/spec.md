# general-agent-reasoning Specification

## Purpose
TBD - created by archiving change add-general-reasoning-reflection-core. Update Purpose after archive.
## Requirements
### Requirement: Agent 创建可验证的任务契约

系统 SHALL 将每次运行目标转换为持久化的 TaskContract，其中包含交付物、约束条件、禁止操作、假设、风险、验证要求以及稳定的成功准则标识符（success-criterion identifiers）。

#### Scenario: 明确目标生成可执行任务契约

* **WHEN** 提交的目标已具备足够信息，可安全开始执行
* **THEN** 任务契约识别所有强制交付物及可验证的成功准则
* **THEN** 后续规划步骤和最终验证均可通过对应标识符引用这些成功准则

#### Scenario: 关键歧义需要澄清

* **WHEN** 缺失的信息将实质性影响交付结果、安全性或外部影响
* **THEN** 任务契约记录该歧义
* **THEN** 运行进入 `waiting_user`，而不是自行假设缺失的信息

### Requirement: Agent 状态具有统一规范且支持版本管理

系统 SHALL 维护统一的 AgentState，其中包含任务契约、生效策略引用、计划版本、成功准则状态、带来源信息（provenance）的已接受事实、待解决问题、观察结果、失败指纹、预算以及终止意图。

#### Scenario: 下一轮接收更新后的状态

* **WHEN** 某项操作产生新的观察结果及评估结果
* **THEN** 在进入下一次决策前，状态更新被持久化
* **THEN** 下一次决策接收新的状态版本，而不是仅依赖原始聊天历史

#### Scenario: 过期状态更新被拒绝

* **WHEN** 某个状态补丁针对的是旧版本计划或旧版本状态
* **THEN** 系统拒绝该补丁或显式执行状态协调（reconcile）
* **THEN** 拒绝原因被记录用于审计

### Requirement: 计划采用可执行且可修订的图结构

系统 SHALL 将计划表示为带版本的有向无环图（Directed Acyclic Graph，DAG），其中每个步骤包含依赖关系、预期结果、关联成功准则、所需能力、风险以及执行状态。

#### Scenario: 依赖阻塞步骤执行

* **WHEN** 某个计划步骤存在尚未满足的必要依赖
* **THEN** 控制器不会执行该步骤
* **THEN** 控制器根据策略选择其他可执行步骤、重新规划（replan）、请求用户协助，或进入阻塞状态

#### Scenario: 重新规划保留有效成果

* **WHEN** 计划级反思替换了某条失效分支
* **THEN** 系统创建新的计划版本
* **THEN** 不受影响的已完成步骤及其证据继续保持关联并仍然有效

### Requirement: 每一次行动决策均以成功准则为导向

系统 SHALL 要求所有可执行决策包含目标步骤、简洁且适合审计的推理摘要、预期观察结果、引用的成功准则、风险等级、置信度，以及适用时的回退行为（fallback）。

#### Scenario: 工具决策信息完整

* **WHEN** 控制器选择执行某项工具操作
* **THEN** 决策明确指定工具输入、目标步骤、预期结果以及该结果推进的成功准则
* **THEN** 执行前由策略门（policy gate）验证该决策

#### Scenario: 决策缺少预期结果

* **WHEN** 某项拟议操作无法说明其期望获得何种有价值的观察结果
* **THEN** 该决策被判定为无效并拒绝执行
* **THEN** 控制器在预算范围内重新规划、请求澄清或进入阻塞状态

### Requirement: 观察结果统一归一化并依据预期进行评估

系统 SHALL 将工具结果、失败信息、用户响应、验证报告以及审批结果统一归一化为类型化的 Observation，并 SHALL 针对决策预期生成 `matched`、`partial`、`mismatch`、`conflict` 或 `inconclusive` 等 Evaluation。

#### Scenario: 工具成功执行但未满足任务意图

* **WHEN** 工具调用成功，但所得观察结果未满足预期的语义结果
* **THEN** 评估结果为 `mismatch` 或 `inconclusive`，而不是 `matched`
* **THEN** 受影响的成功准则仍保持未满足状态

#### Scenario: 观察结果与已接受事实冲突

* **WHEN** 某条具有来源信息（provenance）的观察结果与已接受事实发生矛盾
* **THEN** 评估结果记录该冲突及双方引用来源
* **THEN** 控制器不会静默覆盖任一事实

### Requirement: 推理记录保持可审计且安全

系统 SHALL 持久化简洁、可供用户审计的摘要、结构化决策、状态差异以及证据引用，并且 **MUST NOT** 要求、暴露或依赖隐藏的思维链（chain-of-thought）。

#### Scenario: 用户查看某一轮执行记录

* **WHEN** 用户打开运行审计视图
* **THEN** 当前轮次说明所选择的操作、预期结果、实际结果以及状态变化
* **THEN** 不显示隐藏的思维链，也无需依赖隐藏思维链即可复现该状态转换

### Requirement: 运行时控制 Agent Loop 的节点顺序

系统 SHALL 通过运行时拥有的节点转换图执行推理，并且 **MUST NOT** 允许模型输出跳过策略门、观察归一化、结果评估、状态持久化或完成闸门。

#### Scenario: 模型提出非法状态转换

* **WHEN** 模型决策尝试从行动选择直接跳转到 completed
* **THEN** 运行时拒绝该转换
* **THEN** 完成状态仍必须经过行动策略和完成闸门判定

#### Scenario: 正常行动轮次完成

* **WHEN** 某项已授权行动返回结果
* **THEN** 运行时按顺序执行观察归一化、结果评估、状态持久化、反思触发判断以及完成或继续判断

### Requirement: 每个节点返回类型化转换结果

系统 SHALL 要求每个循环节点返回类型化 `NodeResult`，其中包含拟议状态补丁、产生的事件、下一节点和可选的分类错误。

#### Scenario: 节点结果不合法

* **WHEN** 节点缺少必要的转换信息，或状态补丁超出该节点权限
* **THEN** 运行时在不修改规范状态的情况下拒绝结果
* **THEN** 运行时进入该错误类别对应的有界处理路径

### Requirement: 行动轮次使用可恢复检查点

系统 SHALL 在启动外部行动前持久化已验证决策、策略结果、行动阶段和稳定幂等键，并 SHALL 在行动返回后持久化结果及规范状态更新。

#### Scenario: 行动执行前运行时停止

* **WHEN** 进程在准备轮次后、行动启动前停止
* **THEN** 恢复过程可使用相同幂等键继续已准备的行动

#### Scenario: 幂等行动返回后运行时停止

* **WHEN** 行动结果已经记录，但状态应用被中断
* **THEN** 恢复过程复用已记录结果并完成状态应用，不再次执行行动

#### Scenario: 非幂等行动结果未知

* **WHEN** 恢复过程无法判断某项非幂等行动是否已经生效
* **THEN** 运行时不自动重试该行动
* **THEN** 运行进入 `waiting_user` 或 `blocked`，并记录结果不确定的行动引用

### Requirement: 等待中的运行从持久化续点恢复

系统 SHALL 在进入 `waiting_user` 时持久化暂停节点、状态版本、计划版本、未决请求和 continuation token。

#### Scenario: 用户回答澄清问题

* **WHEN** 用户为等待中的运行提供所需信息
* **THEN** 用户响应被转换为类型化 Observation
* **THEN** 运行从持久化续点恢复，而不是重新开始整个任务

#### Scenario: 用户拒绝行动批准

* **WHEN** 用户拒绝待审批行动
* **THEN** 审批结果作为 Observation 被记录
* **THEN** 控制器选择允许的替代方案、重新规划或阻塞，且不执行被拒绝的行动

### Requirement: 节点错误使用分类且确定性的出口

系统 SHALL 对模型、策略、工具、状态、验证器、预算和运行时内部错误进行分类，并 SHALL 将恢复路径限制在该类别允许的出口集合内。

#### Scenario: 工具发生永久失败

* **WHEN** 工具报告不可重试的永久失败
* **THEN** 运行时不采用临时错误重试路径
* **THEN** 根据策略和剩余能力选择替代方案、重新规划或进入阻塞状态

#### Scenario: 运行时发生内部错误

* **WHEN** 未预期的内部错误使受控 checkpoint 无法完成
* **THEN** 运行以适合审计的错误类别进入 `failed`
* **THEN** Finalizer 不得将任务呈现为成功完成

