## ADDED Requirements

### Requirement: 计划级反思生成受限且版本化的 PlanPatch

系统 SHALL 要求计划级反思通过带 `expected_plan_version` 的结构化 PlanPatch 修改活动计划，并 SHALL 将操作限制为对未完成计划部分的授权变更。

#### Scenario: 修改当前版本的未开始分支

- **WHEN** 计划级反思提出修改尚未开始的节点或依赖，且 expected plan version 与活动计划一致
- **THEN** 系统在副本上应用补丁并执行完整 DAG、策略和预算校验
- **THEN** 校验成功后系统创建新的不可变计划版本并原子切换活动计划

#### Scenario: 补丁基于过期计划版本

- **WHEN** PlanPatch 的 expected plan version 低于当前活动版本
- **THEN** 系统拒绝补丁且不覆盖当前计划
- **THEN** `plan.patch_rejected` 记录版本冲突原因

#### Scenario: 补丁试图改写已完成节点

- **WHEN** PlanPatch 删除、回退或实质性改写已完成节点及其证据
- **THEN** 系统拒绝该操作
- **THEN** 已完成成果继续保持有效且可审计

### Requirement: Replan 决策触发真实计划修订流程

系统 SHALL 将控制器的 `replan` 决策路由到计划级反思或 planner，并 SHALL 在重规划预算范围内产生可应用 PlanPatch、聚焦澄清请求或结构化阻塞结果。

#### Scenario: 控制器请求重新规划

- **WHEN** 控制器因依赖失效返回 `replan`
- **THEN** 运行时调用计划修订路径而不是只增加计数并继续普通轮次
- **THEN** 下一次节点调度使用新计划版本或明确记录修订失败结果

