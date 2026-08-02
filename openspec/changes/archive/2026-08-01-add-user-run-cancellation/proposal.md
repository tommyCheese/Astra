## Why

Astra 当前只能让前端断开 Run 的 SSE 监听，无法真正停止仍在后台执行的模型请求、工具调用和 Agent Loop。用户需要在发起任务后随时终止当前 Run，同时保留已产生的对话内容并继续后续追问。

## What Changes

- 为活动 Run 增加幂等的用户取消 API，并将对应的进程内执行任务真正取消。
- 将 `cancelled` 纳入 Run 正式终态，持久化用户终止原因、事件和执行中资源的中断状态。
- 在模型流、ToolCall、Step、AgentTurn、Sandbox Job 和模型用量记录中正确处理取消，避免遗留永久 `running` 记录。
- Chat composer 在创建或执行 Run 时将发送按钮切换为终止按钮；终止完成后恢复发送，并允许在同一 Task 内继续追问。
- 保留取消前已经展示的部分回答；尚未产生回答时显示简洁的已终止状态。
- 让 SSE 在 `cancelled` 后可靠收敛并关闭，同时覆盖创建、完成与取消之间的竞态。

## Capabilities

### New Capabilities
- `run-cancellation`: 定义用户取消 Run 的 API、幂等语义、执行资源清理、部分结果保留和审计要求。

### Modified Capabilities
- `agent-chat-ui`: 活动 Run 的发送按钮改为可访问的终止按钮，并在取消后恢复发送能力。
- `completion-gate`: 将用户取消定义为独立于完成、阻塞和失败的持久化终态。
- `low-latency-answer-streaming`: 回答流在取消时保存已展示内容并通过 SSE 收敛到取消终态。

## Impact

- 前端：`frontend/src/App.tsx`、`frontend/src/api.ts`、`frontend/src/types.ts`、按钮与终态样式、组件测试。
- 后端：`backend/app/api/runs.py` 的任务注册与取消端点、`RunEngine`/模型客户端取消处理、`RunRepository` 终态清理、SSE 终止条件及 API/运行时测试。
- 协议：新增 `POST /api/runs/{run_id}/cancel`、`cancelled` Run 状态、`run.cancelled` 事件和结构化 `terminal_reason`。
- 部署边界：当前实现面向现有单进程 FastAPI 运行时；未来切换外部 worker 时需用持久化取消信号替换进程内任务注册表。
