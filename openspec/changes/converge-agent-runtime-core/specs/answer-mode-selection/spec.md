## MODIFIED Requirements

### Requirement: 两种模式共享通用 Agent 能力
系统 SHALL 让快速回答和可信模式共享同一个 Agent Runtime、同一个 Agent Loop，以及模型传输、执行审批、ToolRouter、文件与 Artifact、会话、取消和分享基础设施。两种模式 SHALL 通过冻结的类型化能力组合表达差异；快速模式 MUST NOT 加载可信计划、反思或验证能力，可信模式 MUST NOT 建立第二套控制循环或工具执行路径。

#### Scenario: 快速回答调用工具
- **WHEN** 快速回答的模型选择已授权工具
- **THEN** 单一 Agent Loop 通过共享的 ToolRouter、权限门和行动边界执行工具
- **THEN** 规范化观察返回同一个 Loop 的下一次 standard iteration

#### Scenario: 两种模式取消运行
- **WHEN** 用户取消任一模式下的活动 Run
- **THEN** 系统使用共享取消协议停止运行
- **THEN** 单一 Runtime 按冻结的模式能力组合收敛终态

### Requirement: 系统只提供快速响应与可信执行两种产品模式
系统 SHALL 只接受 `standard` 快速响应和 `trusted` 可信执行两种回答模式，并 SHALL 根据回答模式选择一个版本化、冻结的 Runtime capability composition，而不是选择独立 Agent controller。

#### Scenario: 快速响应创建运行
- **WHEN** 用户以 `standard` 模式创建 Run
- **THEN** 系统选择单一 Agent Runtime 的 standard capability composition
- **THEN** 该组合不创建 TaskContract、Plan、PlanNode、PlanEdge、Reflection 或可信验证对象

#### Scenario: 可信执行创建运行
- **WHEN** 用户以 `trusted` 模式创建 Run
- **THEN** 系统选择同一 Agent Runtime 的 trusted capability composition，并在首次外部行动之前通过 Planning capability 创建完整规范 Plan DAG
- **THEN** 系统按 DAG 节点执行并通过 Verification 和 CompletionGate capabilities 运行完整验证与完成门
