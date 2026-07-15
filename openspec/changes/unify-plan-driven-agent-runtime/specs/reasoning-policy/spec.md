## MODIFIED Requirements

### Requirement: 规划策略控制规划时机与细粒度程度

系统 SHALL 为新 Run 支持自适应规划（adaptive）和先规划后执行（plan-first）两种规划策略，并 SHALL 让它们使用相同的规范 Plan 生命周期、DAG 校验、节点调度、策略门和完成门，仅改变计划生成时机、粒度及允许的修订预算。系统 MUST NOT 接受 `direct` 作为新请求策略。

#### Scenario: 旧 direct 偏好被迁移

* **WHEN** 持久化对话偏好仍包含历史值 `direct`
* **THEN** 系统将其归一化并持久化为 `adaptive`
* **THEN** 后续新 Run 不再产生 direct 策略快照

#### Scenario: 历史 direct Run 保持可读

* **WHEN** 系统读取一个已经保存 direct 生效策略的历史 Run
* **THEN** 兼容 Schema 仍能解析并展示其不可变执行事实
* **THEN** 该兼容值不会重新暴露为新 Run 或偏好 API 的可选项

#### Scenario: 自适应规划修订粗粒度计划

* **WHEN** 自适应规划启动或新的观察结果使某项依赖失效
* **THEN** 系统使用粗粒度规范计划并可通过版本化 PlanPatch 修订受影响部分
* **THEN** 未受影响的已完成节点和证据不会被重新生成或丢失
* **THEN** 有效补丁在生效重规划预算内创建并激活新计划版本

#### Scenario: 先规划后执行策略

* **WHEN** 某次运行采用先规划后执行（plan-first）策略
* **THEN** 系统在执行首次外部操作之前，持久化并校验完整的初始计划、依赖关系、风险以及验证节点
* **THEN** 后续执行仍由相同 PlanScheduler 选择 ready node

#### Scenario: 仅规划模式生成正式计划

* **WHEN** 执行模式为 plan-only
* **THEN** 系统生成、校验并持久化状态为 planned 的正式 Plan
* **THEN** 系统不激活节点或执行工具，并允许后续批准激活同一计划版本
