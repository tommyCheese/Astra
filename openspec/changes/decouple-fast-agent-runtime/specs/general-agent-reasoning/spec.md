## MODIFIED Requirements

### Requirement: 运行时控制 Agent Loop 的节点顺序
系统 SHALL 仅通过 Trusted Agent Runtime 执行 TaskContract、DAG 调度、AgentState、节点评估、Reflection 和 CompletionGate。Fast Agent Runtime SHALL 使用独立的模型驱动动作循环，并 MUST NOT 调用或模拟可信节点生命周期；两者只共享工具与平台边界。

#### Scenario: 可信模型尝试跳过完成处理
- **WHEN** trusted 模型决策尝试从行动选择直接进入 completed
- **THEN** Trusted Runtime 拒绝该转换
- **THEN** 最终状态仍由节点状态和 CompletionGate 判定

#### Scenario: 快速行动轮次完成
- **WHEN** Fast Runtime 的已授权行动返回结果
- **THEN** 运行时将规范化观察交回 Fast Agent 模型
- **THEN** 系统不执行 DAG 节点评估、Reflection 或可信完成验证

### Requirement: 观察结果统一归一化并依据预期进行评估
系统 SHALL 通过共享工具边界归一化所有模式的工具结果与失败。Trusted Runtime SHALL 针对活动节点预期生成 Evaluation；Fast Runtime SHALL 将规范化结果直接提供给下一次模型决策，不创建可信 Evaluation。

#### Scenario: 可信工具成功但未满足节点意图
- **WHEN** trusted 工具调用成功但观察未满足活动节点预期
- **THEN** Evaluation 为 mismatch、partial 或 inconclusive
- **THEN** 活动节点不被标记为完成

#### Scenario: 快速工具结果返回循环
- **WHEN** Fast Runtime 的工具调用产生规范化结果
- **THEN** 结果返回独立 Fast Agent loop
- **THEN** 系统不运行节点完成评估或可信进度更新

