## MODIFIED Requirements

### Requirement: 运行时控制 Agent Loop 的节点顺序
系统 SHALL 通过一个固定 Agent Loop 执行 standard 和 trusted iteration，并 SHALL 通过冻结的类型化 capability composition 决定可用行为。standard composition MUST NOT 加载 TaskContract、DAG 调度、AgentState、节点评估、Reflection 或 CompletionGate；trusted composition SHALL 通过 Planning、Progress、Reflection、Verification 和 Completion capabilities 提供这些行为，但 MUST NOT 实现第二套 Agent Loop。

#### Scenario: 可信模型尝试跳过完成处理
- **WHEN** trusted 模型决策尝试从行动选择直接进入 completed
- **THEN** 同一 Agent Loop 将该意图交给 trusted Completion capability 并拒绝非法转换
- **THEN** 最终状态仍由节点状态和 CompletionGate 判定

#### Scenario: 快速行动轮次完成
- **WHEN** standard composition 的已授权行动返回结果
- **THEN** 同一 Agent Loop 将规范化观察交回下一次 standard model decision
- **THEN** 未安装的 DAG 节点评估、Reflection 或可信完成验证不会运行
