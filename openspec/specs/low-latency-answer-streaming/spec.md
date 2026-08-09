# low-latency-answer-streaming Specification

## Purpose
TBD - created by archiving change polish-macos-chat-and-streaming-latency. Update Purpose after archive.
## Requirements
### Requirement: 尽早建立回答事件流
系统 SHALL 在运行创建成功后立即建立 SSE，不得以完整 RunView 快照加载完成作为连接前置条件；服务端 SHALL 立即发出 `stream.ready`。

#### Scenario: 创建运行后连接 SSE
- **WHEN** 创建运行 API 返回 run id
- **THEN** 客户端立即订阅事件且可在完整运行快照返回前收到 `stream.ready`

### Requirement: 低开销 delta 传输
系统 SHALL 合并短时间窗内的模型文本增量，避免为每个模型 chunk 产生一次数据库提交、React 渲染或完整运行快照请求。

#### Scenario: 模型连续输出细粒度 chunks
- **WHEN** 模型在一个渲染帧内输出多个 answer chunks
- **THEN** 服务端可合并事件且客户端最多执行一次可见文本 state 更新

### Requirement: 首 Token 额外延迟预算
在本地无代理环境中，系统从服务端收到首个可展示 answer delta 到将对应 SSE 事件写出之间的 Astra 额外处理时间 SHALL 小于 100ms。

#### Scenario: 首个 summary 字段字符可解析
- **WHEN** 模型流中首次出现可展示的 answer summary 字符
- **THEN** 系统在 100ms 内提交并输出包含该字符的 `answer.delta`

### Requirement: 回答完成即时收敛
`answer.completed` MUST 包含最终 `content`；客户端收到该事件后 MUST 立即清空高频缓冲、移除流式光标并显示最终内容，不得等待 terminal RunView 才完成视觉收敛。

#### Scenario: 最后一个模型 chunk 已处理
- **WHEN** 服务端发出 `answer.completed`
- **THEN** 客户端在下一渲染帧内展示完整回答并退出 streaming 状态

### Requirement: 完成后仅进行一次关键快照刷新
客户端 SHALL 忽略 `answer.delta` 和 heartbeat 对 RunView 的刷新触发，并在回答完成或关键阶段事件时合并刷新请求。

#### Scenario: 回答包含大量 delta 事件
- **WHEN** 客户端连续收到多个 `answer.delta`
- **THEN** 客户端不为每个 delta 请求 RunView，并在完成后获取最终审计快照

### Requirement: 流式连接可恢复
系统 SHALL 保留事件 id 和 after-id 回放能力，并在 SSE 失败时使用低频快照轮询恢复最终状态。

#### Scenario: 回答过程中 SSE 断开
- **WHEN** EventSource 报错并关闭
- **THEN** 客户端继续轮询 RunView，最终展示终态回答且不永久停留在等待态

### Requirement: 回答流在取消时可靠收敛
系统 SHALL 在 Run 取消时停止后续 answer delta，保留已经展示的部分内容，并让 SSE 连接在取消终态后关闭。

#### Scenario: 流式回答中取消
- **WHEN** 客户端已经收到部分 `answer.delta` 后取消 Run
- **THEN** 客户端保留现有文本并退出 streaming 与 settling 状态
- **THEN** 服务端不再发出 `answer.completed`，而是通过 `run.cancelled` 和终态 RunView 表示取消

#### Scenario: SSE 在取消请求期间重连
- **WHEN** 取消请求后 SSE 断开或客户端刷新页面
- **THEN** 客户端通过事件回放或 RunView 恢复 `cancelled` 终态和已持久化的部分回答

### Requirement: 过程增量使用低开销传输与渲染
系统 SHALL 对 `reasoning.summary.delta` 使用有界聚合，并 SHALL 使客户端在一个浏览器动画帧内最多提交一次可见过程文本更新。

#### Scenario: 模型连续输出推理摘要 chunks
- **WHEN** 模型在一个渲染帧内输出多个可审计摘要 chunks
- **THEN** 服务端可合并事件且客户端最多执行一次过程文本 state 更新

### Requirement: 过程事件不得触发高频完整快照刷新
客户端 SHALL 直接归约允许的过程事件，并 SHALL 对必要的 RunView 刷新进行合并；摘要 delta、heartbeat 和 stream ready MUST NOT 各自触发完整快照请求。

#### Scenario: 运行产生大量过程增量
- **WHEN** 客户端连续收到多个 `reasoning.summary.delta`
- **THEN** 客户端实时更新过程文本但不为每个 delta 请求 RunView
- **THEN** 稳定阶段事件或完成事件触发至多一次合并后的快照刷新

### Requirement: 首个过程反馈具有明确延迟预算
在 Run 创建成功并建立 SSE 后，系统 SHALL 立即提供 optimistic 过程状态；服务端从启动一个受控运行阶段到提交对应阶段事件的 Astra 额外处理时间 SHALL 小于 100ms。

#### Scenario: 首轮模型调用耗时较长
- **WHEN** 模型尚未返回任何决策内容
- **THEN** 用户已经可以看到本地 optimistic 状态和随后到达的服务端阶段状态

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

