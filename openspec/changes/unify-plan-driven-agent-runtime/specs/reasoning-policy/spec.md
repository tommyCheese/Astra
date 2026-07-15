## MODIFIED Requirements

### Requirement: 规划策略控制规划时机与细粒度程度

系统 SHALL 支持直接规划（direct）、自适应规划（adaptive）和先规划后执行（plan-first）三种规划策略，并 SHALL 让所有策略使用相同的规范 Plan 生命周期、DAG 校验、节点调度、策略门和完成门，仅改变计划生成时机、粒度及允许的修订预算。

#### Scenario: 直接规划立即执行动作

* **WHEN** 一个明确且复杂度较低的任务采用直接规划
* **THEN** 控制器创建一个满足统一节点 Schema 的当前可执行节点且无需模型 planner 调用
* **THEN** 该节点仍需引用任务成功准则、预期观察结果并由 PlanScheduler 激活

#### Scenario: 自适应规划修订粗粒度计划

* **WHEN** 自适应规划启动或新的观察结果使某项依赖失效
* **THEN** 系统使用粗粒度规范计划并可通过版本化 PlanPatch 修订受影响部分
* **THEN** 未受影响的已完成节点和证据不会被重新生成或丢失

#### Scenario: 先规划后执行策略

* **WHEN** 某次运行采用先规划后执行（plan-first）策略
* **THEN** 系统在执行首次外部操作之前，持久化并校验完整的初始计划、依赖关系、风险以及验证节点
* **THEN** 后续执行仍由相同 PlanScheduler 选择 ready node

#### Scenario: 仅规划模式生成正式计划

* **WHEN** 执行模式为 plan-only
* **THEN** 系统生成、校验并持久化状态为 planned 的正式 Plan
* **THEN** 系统不激活节点或执行工具，并允许后续批准激活同一计划版本

