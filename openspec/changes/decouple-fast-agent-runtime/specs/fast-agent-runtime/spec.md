## ADDED Requirements

### Requirement: 快速模式使用独立的模型驱动运行时
系统 SHALL 为所有新建 `standard` Run 使用独立且版本化的 Fast Agent Runtime，并 MUST NOT 装配 trusted TaskContract、AgentState、Plan DAG、节点调度器、Reflection、VerificationEngine 或 CompletionGate。

#### Scenario: 新建快速运行
- **WHEN** 用户以 `standard` 模式提交请求
- **THEN** Run 冻结 `runtime_kind = fast-v1`
- **THEN** 系统直接进入 Fast Agent loop，不调用 trusted runtime builder

#### Scenario: 新建可信运行
- **WHEN** 用户以 `trusted` 模式提交请求
- **THEN** Run 冻结 `runtime_kind = trusted-v1`
- **THEN** 系统继续使用完整可信执行生命周期

### Requirement: 快速循环相信模型选择下一动作
Fast Agent Runtime SHALL 向模型提供当前对话、可用工具描述和最近工具观察，并 SHALL 允许模型直接选择 `answer`、`call_tool`、`ask_user` 或 `stop`，无需生成计划、成功准则、预期节点结果、反思补丁或验证要求。

#### Scenario: 模型直接回答
- **WHEN** 模型判断无需工具即可响应
- **THEN** 系统立即流式输出答案并完成 Run
- **THEN** 系统不启动额外验证或完成判断调用

#### Scenario: 模型连续使用工具
- **WHEN** 模型根据最近观察再次选择当前可用工具
- **THEN** 系统执行该工具并将规范化观察返回下一轮
- **THEN** 系统不要求该动作映射到 Plan node 或成功准则

#### Scenario: 工具返回失败
- **WHEN** Fast Agent 的工具调用失败
- **THEN** 系统将标准化错误观察交回模型决定重试、换工具、提问或回答
- **THEN** 系统不触发 trusted Reflection 或 replan

### Requirement: 快速运行只持久化轻量恢复状态
Fast Agent Runtime SHALL 持久化版本化的轻量快照，至少包含消息轮次、最近观察、待处理审批或工具调用引用、协议版本和终态意图，并 MUST NOT 伪造 TaskContract、AgentState、Plan 或验证对象。

#### Scenario: 快速运行在工具返回前重启
- **WHEN** 进程在已持久化工具调用后停止
- **THEN** Fast Recovery 根据幂等状态恢复或明确报告结果未知
- **THEN** 恢复过程不进入 trusted AgentState 或 DAG

#### Scenario: 读取历史快速运行
- **WHEN** 历史 `standard` Run 没有 runtime kind
- **THEN** 系统使用 legacy 只读投影或兼容 executor
- **THEN** 系统不把历史记录改写为 `fast-v1`

### Requirement: 快速运行不生成可信校验产物
Fast Agent Runtime MUST NOT 创建 VerificationReport、CompletionDecision、Evidence Pack Artifact、领域 ValidationOutcome 或“已校验”状态，最终回答 SHALL 标记为快速结果而非可信交付。

#### Scenario: 快速回答完成
- **WHEN** 模型输出最终答案
- **THEN** 系统直接清洗可访问 Artifact 引用、持久化答案并发送完成事件
- **THEN** `verification_report` 和 `completion_decision` 均为空

### Requirement: 平台硬边界独立于运行时
Fast Agent Runtime SHALL 复用平台唯一的工具启用状态、输入 Schema、Effect 分析、权限与审批、Sandbox、敏感数据边界、Artifact 访问控制、取消和基础错误处理，并 MUST NOT 允许模型输出关闭或绕过这些边界。

#### Scenario: 模型请求被禁止的操作
- **WHEN** Fast Agent 选择的平台策略禁止操作
- **THEN** 共享权限门拒绝执行并返回标准化观察
- **THEN** 该拒绝不依赖 trusted CompletionGate

#### Scenario: 用户取消快速运行
- **WHEN** 用户取消活动 Fast Run
- **THEN** 共享取消协议停止后续模型与工具动作
- **THEN** Run 收敛为 `cancelled`

### Requirement: 快速运行时可以独立演进
系统 SHALL 对 Fast Agent 协议、运行时版本、指标和发布开关独立版本化，使快速模式实现变化不要求修改 trusted runtime 行为或数据合同。

#### Scenario: 发布新的快速协议
- **WHEN** 部署启用新的 Fast Runtime 版本
- **THEN** 只有之后新建的 `standard` Run 冻结新版本
- **THEN** 已存在 Run 继续由其冻结版本恢复和完成

