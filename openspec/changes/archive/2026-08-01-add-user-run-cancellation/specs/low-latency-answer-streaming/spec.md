## ADDED Requirements

### Requirement: 回答流在取消时可靠收敛
系统 SHALL 在 Run 取消时停止后续 answer delta，保留已经展示的部分内容，并让 SSE 连接在取消终态后关闭。

#### Scenario: 流式回答中取消
- **WHEN** 客户端已经收到部分 `answer.delta` 后取消 Run
- **THEN** 客户端保留现有文本并退出 streaming 与 settling 状态
- **THEN** 服务端不再发出 `answer.completed`，而是通过 `run.cancelled` 和终态 RunView 表示取消

#### Scenario: SSE 在取消请求期间重连
- **WHEN** 取消请求后 SSE 断开或客户端刷新页面
- **THEN** 客户端通过事件回放或 RunView 恢复 `cancelled` 终态和已持久化的部分回答
