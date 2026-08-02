## MODIFIED Requirements

### Requirement: Runner 创建可审计步骤
系统 SHALL 将 trusted Run 的规划和执行表示为关联到 Run 的规范 PlanNode 记录，并 SHALL 通过 Run View 投影为可审计步骤。standard Run SHALL 不创建 PlanNode 或步骤占位。

#### Scenario: 可信计划创建节点
- **WHEN** 模型为 trusted Run 生成有效完整计划
- **THEN** 系统创建带依赖、意图、状态、预期结果和成功准则的 PlanNode 记录

#### Scenario: 可信节点完成时记录证据
- **WHEN** trusted PlanNode 完成
- **THEN** 系统更新节点状态并存储相关工具调用、产物或验证证据

#### Scenario: 快速响应调用工具
- **WHEN** standard Run 调用一个或多个工具
- **THEN** 系统记录 ToolCall 和过程事件
- **THEN** Run View 的计划步骤保持为空

## ADDED Requirements

### Requirement: Trusted Run 通过版本绑定确认开始计划执行
系统 SHALL 在 trusted Run 选择确认行为时持久化完整 Plan 和 continuation request，并 SHALL 仅通过共享续跑协议激活用户确认的精确 Plan 版本。系统 MUST NOT 恢复旧的独立 plan-only 激活 API。

#### Scenario: 客户端请求旧激活端点
- **WHEN** 客户端请求已删除的 `/runs/{run_id}/activate-plan` 端点
- **THEN** API 不匹配该路由
- **THEN** Run 状态不发生变化

#### Scenario: 确认当前计划版本
- **WHEN** waiting_user Run 收到匹配的一次性 Plan 确认
- **THEN** 系统激活确认的 Plan 并恢复 Agent Loop
- **THEN** Run 不创建第二份计划

#### Scenario: 拒绝过期计划确认
- **WHEN** Plan 确认引用的版本不是 Run 当前等待的版本
- **THEN** 系统拒绝确认并保持 Run 不执行

### Requirement: 活动旧模式 Run 在升级时终止
系统 SHALL 在单向迁移中取消包含删除模式值的非终态 Run，并 SHALL 不允许其在新运行时恢复。

#### Scenario: 迁移等待激活的 plan-only Run
- **WHEN** 数据迁移发现 plan-only、planning、executing 或 waiting_user 状态且 Profile 包含删除值的 Run
- **THEN** 迁移将该 Run 标记为 cancelled
- **THEN** 终止原因标识模式升级
