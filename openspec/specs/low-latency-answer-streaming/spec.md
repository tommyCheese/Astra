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

