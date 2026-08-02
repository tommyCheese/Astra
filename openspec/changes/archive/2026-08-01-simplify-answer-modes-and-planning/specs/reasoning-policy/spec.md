## MODIFIED Requirements

### Requirement: 运行保存请求策略与生效推理策略

系统 SHALL 在创建运行时接受回答模式、可信计划执行选择、推理资源偏好、反思偏好和审批行为，并 SHALL 持久化该次运行严格版本化且不可变的生效 Profile。系统 MUST NOT 接受或持久化用户可选的规划策略。

#### Scenario: 编译快速响应 Profile

* **WHEN** 用户以 `standard` 模式启动一次运行
* **THEN** 系统记录快速推理、无 TaskContract、无 Plan DAG、关闭模型反思和基础保障作为生效 Profile
* **THEN** 系统不读取或持久化规划策略

#### Scenario: 编译可信执行 Profile

* **WHEN** 用户以 `trusted` 模式启动一次运行
* **THEN** 系统记录执行前完整规划、规范 DAG 调度、允许的有界反思与重规划以及完整验证作为生效 Profile
* **THEN** 系统记录计划生成后自动执行或等待版本绑定确认的选择
* **THEN** 系统不提供自适应初始规划选项

#### Scenario: 运行期间设置发生变化

* **WHEN** 用户在运行开始后修改回答模式或允许的可信策略
* **THEN** 当前运行继续使用其已持久化的生效 Profile
* **THEN** 后续新建运行可以使用新的模式或策略

### Requirement: 策略编译器应用正确性与安全下限

系统 SHALL 根据回答模式、任务风险、任务复杂度、工具权限以及系统策略编译运行 Profile，并且 **MUST NOT** 允许任一模式关闭强制性的权限检查、工具输入 Schema、执行安全、基础失败处理或 Artifact 引用清洗。

#### Scenario: 高风险任务使用快速响应

* **WHEN** 某个高风险任务以 `standard` 模式请求执行工具
* **THEN** 策略编译器仍根据系统策略提升审批或安全限制
* **THEN** 快速响应不会绕过被禁止操作或部署硬上限

#### Scenario: 可信执行关闭可选反思

* **WHEN** trusted 用户策略禁用模型驱动反思
* **THEN** 运行时不再调用可选的模型反思
* **THEN** DAG 校验、权限门控、重复操作防护、节点评估以及完成验证仍保持启用

### Requirement: 推理强度控制有界思考资源

系统 SHALL 在 trusted 模式中将快速、平衡和深度推理映射为明确的模型思考、工具、反思、重规划和验证预算；standard 模式 SHALL 使用固定快速 Profile，而不把可信推理强度表现为可选规划行为。

#### Scenario: 快速响应执行简单任务

* **WHEN** 一个任务采用 `standard` 模式
* **THEN** 生效 Profile 使用低思考预算并跳过规范计划
* **THEN** 强制安全检查保持不变

#### Scenario: 可信深度推理执行复杂任务

* **WHEN** 一个 trusted Run 采用深度推理
* **THEN** 生效 Profile允许更高但仍受限的工具、反思和 DAG 修订预算
* **THEN** 完整初始 DAG 仍在首次外部行动前持久化

### Requirement: 规划策略控制规划时机与细粒度程度

系统 SHALL 根据回答模式固定规划行为：`standard` 不创建计划，`trusted` 在执行前创建完整规范 Plan DAG。系统 MUST NOT 提供 direct、adaptive 或 plan-first 用户策略字段。

#### Scenario: 快速响应立即选择行动

* **WHEN** `standard` Run 开始执行
* **THEN** 控制器立即选择回答、工具、询问用户或阻塞
* **THEN** 控制器不创建合成单节点计划

#### Scenario: 可信执行先生成完整计划

* **WHEN** `trusted` Run 开始执行
* **THEN** 系统在首次外部操作之前持久化完整初始计划、依赖关系、风险和验证步骤
* **THEN** 只有满足依赖的节点可被调度

#### Scenario: 可信执行修订失效分支

* **WHEN** 新观察或失败使 trusted DAG 的未完成分支失效
* **THEN** 系统可以在重规划预算内创建经过校验的新计划版本
* **THEN** 该行为不作为用户可选的 adaptive 模式暴露

### Requirement: 执行审批模式由策略门统一执行

系统 SHALL 在工具执行前应用请求审批和自动审批两种审批行为，并 SHALL 在所有回答模式下始终保留工具限制、沙箱限制以及系统限制等硬性约束。系统 MUST NOT 支持 plan-only 审批行为。

#### Scenario: 请求审批暂停受控操作

* **WHEN** 某项操作在请求审批行为下需要获得审批
* **THEN** 运行进入 `waiting_user`，并生成结构化审批请求
* **THEN** 在审批结果记录之前，不执行该操作

#### Scenario: 自动审批遇到被禁止的操作

* **WHEN** 自动审批行为请求执行一项被系统策略禁止的操作
* **THEN** 策略门拒绝该操作
* **THEN** 自动审批不会绕过该禁止规则
