## MODIFIED Requirements

### Requirement: 两种模式共享通用 Agent 能力
系统 SHALL 让快速回答和可信模式共享模型传输、执行审批、ToolRouter、文件与 Artifact、会话、取消和分享基础设施，同时 SHALL 为两种模式维护明确分离的 Agent runtime；共享基础设施 MUST NOT 使 Fast Runtime 依赖可信计划、反思或验证生命周期。

#### Scenario: 快速回答调用工具
- **WHEN** 快速回答的模型选择已授权工具
- **THEN** Fast Runtime 通过共享 ToolRouter 和权限门执行工具
- **THEN** 工具观察返回独立 Fast Agent loop

#### Scenario: 两种模式取消运行
- **WHEN** 用户取消任一模式下的活动 Run
- **THEN** 系统使用共享取消协议停止运行
- **THEN** 每个 runtime 按自己的状态投影收敛终态

### Requirement: 系统只提供快速响应与可信执行两种产品模式
系统 SHALL 只接受 `standard` 快速响应和 `trusted` 可信执行两种回答模式，并 SHALL 根据回答模式选择一个独立、版本化的 Agent runtime。

#### Scenario: 快速响应创建运行
- **WHEN** 用户以 `standard` 模式创建 Run
- **THEN** 系统选择 Fast Agent Runtime
- **THEN** 系统不创建 TaskContract、Plan、PlanNode、PlanEdge、Reflection 或可信验证对象

#### Scenario: 可信执行创建运行
- **WHEN** 用户以 `trusted` 模式创建 Run
- **THEN** 系统选择 Trusted Agent Runtime 并在首次外部行动之前创建完整规范 Plan DAG
- **THEN** 系统按 DAG 节点执行并运行完整验证与完成门

### Requirement: 每个 Run 持久化不可变的模式 Profile
系统 SHALL 在创建 Run 时持久化不可变的 answer mode、runtime kind、runtime version 和对应模式 Profile，并 SHALL 在续跑时由冻结的 runtime 解释其状态。

#### Scenario: 运行期间切换首选模式
- **WHEN** 用户在已有 Run 创建后切换回答模式
- **THEN** 已有 Run 的模式、runtime kind 和版本不发生变化
- **THEN** 后续新建 Run 使用新的首选模式

#### Scenario: 继续等待中的新版本 Run
- **WHEN** 用户继续一个处于 `waiting_user` 的 Run
- **THEN** 系统使用该 Run 冻结的 runtime 与 Profile 恢复
- **THEN** 系统不按当前界面模式重新分派运行时

