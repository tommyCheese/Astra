## ADDED Requirements

### Requirement: 多 Agent 事件流具有有界聚合和背压
系统 SHALL 对并发 children 的高频进度事件进行有界批处理，同时立即传输终态、审批、等待用户、关键错误和 Artifact 可用事件。

#### Scenario: 多个 children 高频更新
- **WHEN** 多个 Agent 同时产生 token、工具进度或 heartbeat 更新
- **THEN** 服务端合并可合并的中间更新并保持关键状态顺序，避免事件风暴阻塞主回答流

#### Scenario: child 完成
- **WHEN** child 进入终态或产生可用 Artifact
- **THEN** 对应事件不等待普通进度批次，并带完整 lineage 发送给客户端

### Requirement: 多 Agent 流式连接可按快照恢复
系统 SHALL 允许客户端使用 Run cursor 重连，并在事件日志被压缩、缺失或检测到 Agent 局部序列不连续时返回权威 lineage snapshot。

#### Scenario: 重连期间 children 状态变化
- **WHEN** 客户端断开期间多个 children 开始、等待或完成
- **THEN** 重连响应使客户端恢复所有 Agent 当前状态和关键终态，且不会把旧 running 事件覆盖新的 completed 状态

