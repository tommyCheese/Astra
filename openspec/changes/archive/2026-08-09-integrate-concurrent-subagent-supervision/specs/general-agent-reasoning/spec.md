## ADDED Requirements

### Requirement: Trusted 根 Agent 使用 Swarm 内建能力委派
系统 SHALL 向 eligible trusted 根控制器提供 Astra `swarm` runtime built-in，其中包含有界 DelegationRequest 集合和一个 Join 规范；系统 MUST NOT 将该能力交给第三方插件或 sandbox 执行，也 MUST NOT 允许其绕过 SubagentSupervisor。

#### Scenario: 控制器识别独立并行工作
- **WHEN** trusted 根控制器识别出两个相互独立、预期收益为正且符合策略的子任务
- **THEN** 控制器可在一次 `swarm` 调用中提交两个完整 DelegationRequest 和 Join policy
- **THEN** 运行时在执行前验证目标、成功标准、范围、输入、输出 schema、能力、预算和去重信息

#### Scenario: Standard 控制器尝试调用 Swarm
- **WHEN** standard Run 的控制器构造或请求 `swarm` 调用
- **THEN** 运行时拒绝该决策且不创建 child

### Requirement: 根 Agent 仅消费验证后的合并观察
系统 SHALL 将已消费 Join 的合并结果作为类型化 parent Observation 提供给后续根决策，并 MUST NOT 注入 child 隐藏推理、完整对话或私有 scratchpad。

#### Scenario: 多个 child 返回相互冲突的结论
- **WHEN** Join Merger 检测到两个已验证 child 对同一事实或声明给出不同值
- **THEN** parent Observation 保留各来源、Evidence 和结构化 conflict
- **THEN** 根控制器处理冲突而不是任意覆盖一个结果
