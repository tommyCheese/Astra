## MODIFIED Requirements

### Requirement: Agent 状态具有统一规范且支持版本管理

系统 SHALL 维护统一的 AgentState，其中包含任务契约、生效策略引用、计划版本、活动 NodeExecution 摘要集合、成功准则状态、带来源信息的已接受事实、待解决问题、观察结果、失败指纹、预算以及终止意图。

#### Scenario: 下一轮接收更新后的状态

* **WHEN** 某项操作产生新的观察结果及评估结果
* **THEN** 在进入下一次决策前，状态更新被持久化
* **THEN** 下一次决策接收新的状态版本，而不是仅依赖原始聊天历史

#### Scenario: 过期状态更新被拒绝

* **WHEN** 某个状态补丁针对的是旧版本计划、旧 execution attempt 或旧版本状态
* **THEN** 系统拒绝该补丁或显式执行状态协调
* **THEN** 拒绝原因被记录用于审计

#### Scenario: 并发节点提交状态

* **WHEN** 多个 NodeWorker 提交节点作用域结果
* **THEN** 系统以版本校验合并 Run 级状态并保留每项结果的 provenance
* **THEN** 一个 Worker 不会覆盖另一个 Worker 的活动 execution 或预算更新

### Requirement: 计划采用可执行且可修订的图结构

系统 SHALL 将计划表示为带版本的有向无环图，其中每个步骤包含依赖关系、预期结果、关联成功准则、所需能力、风险以及执行状态；运行时 SHALL 并发认领无必要依赖且通过安全策略的 ready 节点。

#### Scenario: 依赖阻塞步骤执行

* **WHEN** 某个计划步骤存在尚未满足的必要依赖
* **THEN** 控制器不会执行该步骤
* **THEN** 控制器根据策略选择其他可执行步骤、重新规划、请求用户协助，或进入阻塞状态

#### Scenario: 多个独立步骤可执行

* **WHEN** 多个步骤同时 ready 且不违反并发、资源、预算或审批约束
* **THEN** 运行时在配置上限内并发执行这些步骤
* **THEN** 每个步骤仍独立完成预期结果评估

#### Scenario: 重新规划保留有效成果

* **WHEN** 计划级反思替换了某条失效分支
* **THEN** 系统在旧版本活动 execution 排空后创建新的计划版本
* **THEN** 不受影响的已完成步骤及其证据继续保持关联并仍然有效

### Requirement: 每一次行动决策均以成功准则为导向

系统 SHALL 要求所有可执行决策包含目标 PlanNode、NodeExecution attempt、简洁且适合审计的推理摘要、预期观察结果、引用的成功准则、风险等级、置信度，以及适用时的回退行为。

#### Scenario: 工具决策信息完整

* **WHEN** NodeWorker 选择执行某项工具操作
* **THEN** 决策明确指定工具输入、目标 PlanNode、execution attempt、预期结果以及该结果推进的成功准则
* **THEN** 执行前由策略门验证该决策及其资源租约

#### Scenario: 决策指向另一个活动节点

* **WHEN** 一个 Worker 的决策引用其他并行 Worker 所属的 PlanNode 或 execution
* **THEN** 该决策被判定为无效并拒绝执行
* **THEN** 其他 Worker 的状态保持不变

#### Scenario: 决策缺少预期结果

* **WHEN** 某项拟议操作无法说明其期望获得何种有价值的观察结果
* **THEN** 该决策被判定为无效并拒绝执行
* **THEN** 控制器在预算范围内重新规划、请求澄清或进入阻塞状态

### Requirement: 行动轮次使用可恢复检查点

系统 SHALL 为每个 NodeExecution 在启动外部行动前持久化已验证决策、策略结果、行动阶段、预算预留、资源租约和稳定幂等键，并 SHALL 在行动返回后持久化结果及版本化状态更新。

#### Scenario: 行动执行前运行时停止

* **WHEN** 进程在准备轮次后、行动启动前停止
* **THEN** 恢复过程可使用相同 execution attempt 和幂等键继续已准备的行动

#### Scenario: 幂等行动返回后运行时停止

* **WHEN** 行动结果已经记录，但状态应用被中断
* **THEN** 恢复过程复用已记录结果并完成状态应用，不再次执行行动

#### Scenario: 非幂等行动结果未知

* **WHEN** 恢复过程无法判断某项非幂等行动是否已经生效
* **THEN** 对应 NodeExecution 不自动重试该行动
* **THEN** 该分支进入 `waiting_user` 或 `blocked`，其他安全分支按策略继续
