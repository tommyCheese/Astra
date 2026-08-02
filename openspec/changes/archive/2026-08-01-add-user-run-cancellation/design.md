## Context

Run 由 `POST /api/runs` 持久化后，通过 `asyncio.create_task(start_run_in_process(...))` 在 FastAPI 单进程内执行。前端以 `RunView` 和 SSE 观察进度，但关闭 EventSource 不会停止 `RunEngine`、模型 HTTP 流或工具。当前 `_background_tasks` 仅保存匿名 Task 集合，Run 状态机也没有 `cancelled` 终态。

取消会跨越 API、进程内任务注册、模型流、Agent Loop、工具/沙箱、Repository、SSE 和 Chat UI；同时必须处理“自然完成与取消同时发生”以及“创建请求尚未返回 run id”两类竞态。

## Goals / Non-Goals

**Goals:**

- 用户提交后立即看到终止按钮，并能终止当前 Run。
- 取消真正传播到模型请求和当前工具，而不只是停止前端监听。
- `cancelled` 成为幂等、可恢复、可审计的持久化终态。
- 清理执行中的 Step、ToolCall、AgentTurn、Sandbox Job 和模型调用记录。
- 保留已经流给用户的部分回答，并允许在同一 Task 中继续追问。
- 覆盖取消与创建、自然完成、重复请求之间的竞态。

**Non-Goals:**

- 不撤销取消前已经完成的外部副作用。
- 不永久关闭或删除 Task/对话。
- 不在本次 change 中引入外部任务队列、多进程 worker 协调或分布式取消协议。
- 不把用户取消计为运行失败或验证通过。

## Decisions

### 1. 取消目标是当前 Run，而不是整个 Task

新增 `POST /api/runs/{run_id}/cancel`。取消后 Run 进入 `cancelled`，原 Task 和历史消息保留；下一次发送创建同一 Task 下的新 Run。

替代方案是终止整个 Task，但这与发送按钮临时切换为终止按钮的交互不符，也会阻断自然追问。

### 2. 使用按 run id 索引的进程内任务注册表

将 `_background_tasks: set` 改为 `run_id -> asyncio.Task` 注册表。取消端点定位任务并调用 `task.cancel()`；注册表负责完成回调与清理。重复取消已取消 Run 返回当前快照，取消已自然完成 Run 不覆盖原终态。

当前部署是单进程 FastAPI，因此该方式能即时中断正在等待的模型或工具协程。未来外部 worker 化时，API 契约和持久化终态保持不变，执行层改为持久化取消信号与 worker 协调。

### 3. 在执行包装层持久化取消终态

`asyncio.CancelledError` 不作为普通异常处理。执行包装层在取消传播并完成工具自身清理后，使用新的数据库会话调用 Repository 的取消收敛方法，避免复用被取消时可能处于失败事务状态的 Session。

取消收敛以数据库当前状态为准：若已经是自然终态则保持不变；否则原子地写入 `cancelled`、`completed_at`、`terminal_reason={category: user_cancelled}` 和 `run.cancelled` 事件，并把活动 Step、ToolCall、AgentTurn 置为 `cancelled`。

Repository 禁止 `cancelled` Run 被迟到的普通状态更新重新改为 executing/completed。

### 4. 部分回答来自已持久化和待刷新缓冲

RunEngine 捕获取消时先把尚未提交的 answer buffer 写成 `answer.delta`，但不产生 `answer.completed`。取消收敛从已持久化的 answer delta 聚合可见部分回答：有内容时作为取消结果的 summary 保存并标记 `partial=true`；无内容时使用“已终止本次运行”。

前端保留当前 `streamingAnswer`，收到 `run.cancelled` 或取消后的快照时退出 streaming/settling，展示部分文本及“已停止”状态。

### 5. 模型与执行记录使用中断语义

模型客户端显式捕获 `CancelledError`，将当前 invocation 完成为 `interrupted` 后重新抛出。Sandbox 已有取消清理；普通 ToolCall、Step 和 AgentTurn 由 Run 取消收敛统一标记 `cancelled`，避免永久 `running`。

### 6. 前端使用一个活动态按钮和取消意图

活动条件是创建请求进行中、Run 非终态或取消请求进行中。活动时发送按钮变为带方形停止图标的终止按钮，使用 `type=button`，不触发表单提交。

若用户在创建 API 返回前点击终止，前端记录 `cancelRequested`；拿到 run id 后立即调用取消 API。取消请求期间按钮禁用以防重复提交；成功后刷新 Run 快照并恢复发送按钮。

## Risks / Trade-offs

- [取消与自然完成竞态可能覆盖正确终态] → Repository 以当前持久化终态作条件判断，取消和正常状态更新都不得覆盖已有终态。
- [任务取消发生在数据库 await 中，原 Session 可能不可复用] → 在执行包装层用新 Session 完成取消收敛。
- [工具已经产生外部副作用] → 仅停止后续工作并在终态原因中说明不提供回滚保证。
- [模型供应商可能在连接关闭后短暂继续计费] → 关闭本地 HTTP 流并将本地 invocation 标记 interrupted，但不承诺供应商侧即时停止计费。
- [多 worker 部署无法通过本地注册表定位任务] → 明确当前单进程边界；未来保留 API，替换为持久化控制面。
- [取消时部分 JSON 字段不完整] → 只保存已经通过 `answer.delta` 对用户可见的 summary 文本，不解析未完成的完整模型 JSON。

## Migration Plan

1. 先加入 `cancelled` 终态、Repository 幂等收敛和测试。
2. 接入任务注册表、取消 API、模型/工具清理和 SSE 终止条件。
3. 接入前端按钮切换、创建中取消意图、部分回答展示和本地化。
4. 在单进程环境验证模型流、工具执行、重复取消与自然完成竞态。
5. 回滚时可撤除前端入口和 API；已有 `cancelled` 记录仍按未知非活动状态安全展示，不需要数据库迁移。

## Open Questions

无。本 change 将“终止对话”解释为终止当前 Run，并允许后续继续追问。
