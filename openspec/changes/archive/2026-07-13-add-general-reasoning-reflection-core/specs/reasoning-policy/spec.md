## ADDED Requirements

### Requirement: 运行保存请求策略与生效推理策略

系统 SHALL 在创建运行时接受用户的推理偏好，并 SHALL 持久化保存用户请求的策略（requested policy）以及该次运行实际采用的不可变生效策略快照（effective immutable policy snapshot）。

#### Scenario: 编译默认策略

* **WHEN** 用户启动一次运行且未覆盖默认推理偏好
* **THEN** 系统记录平衡推理（balanced reasoning）、自适应规划（adaptive planning）、自适应反思（adaptive reflection）、请求审批（request approval）以及标准验证（standard verification）作为默认请求策略
* **THEN** 系统记录根据这些默认配置推导出的生效预算及限制

#### Scenario: 运行期间设置发生变化

* **WHEN** 用户在运行开始后修改工作区的推理设置
* **THEN** 当前运行继续使用其已持久化的生效策略快照
* **THEN** 后续新建运行可以使用新的请求策略

### Requirement: 策略编译器应用正确性与安全下限

系统 SHALL 根据任务风险、任务复杂度、工具权限以及系统策略对用户请求的偏好进行编译，并且 **MUST NOT** 允许用户偏好关闭任何强制性的安全检查、基础失败处理或完成验证。

#### Scenario: 高风险任务请求快速直接执行

* **WHEN** 某个高风险任务请求采用快速推理（fast reasoning）和直接规划（direct planning）
* **THEN** 策略编译器根据系统策略提升实际所需的规划、审批或验证等级
* **THEN** 每一次调整均记录对应规则及用户可审计的调整原因

#### Scenario: 关闭反思功能

* **WHEN** 用户禁用模型驱动反思（model-driven reflection）
* **THEN** 运行时不再调用可选的模型反思
* **THEN** Schema 校验、有界重试、权限门控、重复操作防护以及完成验证仍保持启用

### Requirement: 推理强度控制有界思考资源

系统 SHALL 将快速（fast）、平衡（balanced）和深度（deep）推理映射为明确的计划深度、候选策略数量、模型思考预算、反思预算以及验证覆盖范围等资源限制。

#### Scenario: 快速推理执行简单任务

* **WHEN** 一个低风险的单步骤任务采用快速推理
* **THEN** 生效策略允许采用最小化计划及较低的思考预算
* **THEN** 强制完成准则和安全检查保持不变

#### Scenario: 深度推理评估多个方案

* **WHEN** 一个复杂任务采用深度推理
* **THEN** 生效策略允许生成多个候选策略、执行假设检查，并提供更高但仍受限的反思覆盖范围
* **THEN** 所有新增的模型调用均计入该运行记录的预算

### Requirement: 规划策略控制规划时机与细粒度程度

系统 SHALL 支持直接规划（direct）、自适应规划（adaptive）和先规划后执行（plan-first）三种规划策略，并为其提供不同的运行时行为。

#### Scenario: 直接规划立即执行动作

* **WHEN** 一个明确且复杂度较低的任务采用直接规划
* **THEN** 控制器可以仅创建当前可执行步骤后立即执行
* **THEN** 该步骤仍需引用任务成功准则及预期观察结果

#### Scenario: 自适应规划修订粗粒度计划

* **WHEN** 在自适应规划过程中，新的观察结果使某项依赖失效
* **THEN** 系统可以仅修订受影响的计划部分，而无需重新生成未受影响的已完成步骤

#### Scenario: 先规划后执行策略

* **WHEN** 某次运行采用先规划后执行（plan-first）策略
* **THEN** 系统在执行首次外部操作之前，持久化完整的初始计划、依赖关系、风险以及验证步骤

### Requirement: 执行审批模式由策略门统一执行

系统 SHALL 在工具执行前应用仅规划（plan-only）、请求审批（request-approval）和自动审批（auto-approval）三种审批模式，同时在所有模式下始终保留工具限制、沙箱限制以及系统限制等硬性约束。

#### Scenario: 请求审批暂停受控操作

* **WHEN** 某项操作在请求审批模式下需要获得审批
* **THEN** 运行进入 `waiting_user`，并生成结构化审批请求
* **THEN** 在审批结果记录之前，不执行该操作

#### Scenario: 自动审批遇到被禁止的操作

* **WHEN** 自动审批模式请求执行一项被系统策略禁止的操作
* **THEN** 策略门拒绝该操作
* **THEN** 自动审批不会绕过该禁止规则
