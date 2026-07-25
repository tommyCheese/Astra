## ADDED Requirements

### Requirement: 系统只提供快速响应与可信执行两种产品模式
系统 SHALL 只接受 `standard` 快速响应和 `trusted` 可信执行两种回答模式，并 SHALL 根据回答模式确定唯一的规划与验证生命周期。

#### Scenario: 快速响应创建运行
- **WHEN** 用户以 `standard` 模式创建 Run
- **THEN** 系统直接进入共享 Agent Loop 的快速分支
- **THEN** 系统不创建 TaskContract、Plan、PlanNode、PlanEdge 或可信验证对象

#### Scenario: 可信执行创建运行
- **WHEN** 用户以 `trusted` 模式创建 Run
- **THEN** 系统在首次外部行动之前创建并持久化完整的规范 Plan DAG
- **THEN** 系统按 DAG 节点执行并运行完整验证与完成门

### Requirement: 每个 Run 持久化不可变的模式 Profile
系统 SHALL 在创建 Run 时持久化严格版本化且不可变的回答模式、可信计划执行选择与执行 Profile，并 SHALL 在续跑时使用该 Profile。

#### Scenario: 运行期间切换首选模式
- **WHEN** 用户在已有 Run 创建后切换回答模式
- **THEN** 已有 Run 的模式和 Profile 不发生变化
- **THEN** 后续新建 Run 使用新的首选模式

#### Scenario: 继续等待中的新版本 Run
- **WHEN** 用户继续一个由当前 Profile 版本创建的 `waiting_user` Run
- **THEN** 系统使用该 Run 原有的模式和 Profile 恢复

### Requirement: 可信用户决定计划生成后是否立即执行
系统 SHALL 允许 trusted Run 选择 `auto` 或 `confirm` 计划执行行为。该选择 MUST NOT 被建模为规划策略、plan-only 模式或工具效果批准。

#### Scenario: 可信计划自动执行
- **WHEN** trusted Run 的计划执行行为为 `auto`
- **THEN** 系统在完整 DAG 校验并持久化后激活该 Plan
- **THEN** 系统可以调度首个 ready 节点

#### Scenario: 可信计划等待确认
- **WHEN** trusted Run 的计划执行行为为 `confirm`
- **THEN** 系统持久化完整 DAG 并进入 `waiting_user`
- **THEN** 在用户确认对应 Plan 版本之前不执行任何 Plan 节点

#### Scenario: 用户确认展示的计划
- **WHEN** 用户提交匹配 Run、Plan ID、Plan 版本和 continuation token 的执行确认
- **THEN** 系统一次性消费该确认并激活对应 Plan
- **THEN** 后续工具效果仍独立经过配置的审批行为

#### Scenario: 用户暂不执行
- **WHEN** 用户在计划确认卡选择暂不执行
- **THEN** Run 保持可恢复的 `waiting_user`
- **THEN** 系统不把仅生成计划表示为成功完成

### Requirement: 删除的模式输入不提供兼容行为
系统 MUST NOT 接受 `plan_only`、`adaptive`、`direct` 或 `planning_strategy` 作为新请求、偏好或运行 Profile 的有效输入，并 MUST NOT 将其静默归一化为新值。

#### Scenario: 旧客户端发送规划策略
- **WHEN** 客户端提交包含 `planning_strategy` 的新 Run 或偏好请求
- **THEN** API 返回明确的请求校验错误
- **THEN** 系统不创建或更新任何记录

#### Scenario: 数据库尚未完成升级
- **WHEN** 启动检查发现活动记录仍包含删除的模式字段或枚举值
- **THEN** 应用拒绝启动 Run worker
- **THEN** 错误明确要求执行模式升级迁移

### Requirement: 两种模式共享不可关闭的安全边界
系统 SHALL 让两种模式共享工具 Schema 校验、权限门、Effect 分析、沙箱、数据流限制、Artifact 引用清洗、取消和错误处理，并 MUST NOT 允许回答模式绕过这些边界。

#### Scenario: 快速响应请求禁止操作
- **WHEN** `standard` Run 请求执行被平台策略禁止的操作
- **THEN** 共享权限门拒绝该操作
- **THEN** 快速响应不会因为缺少 DAG 而降低安全限制
