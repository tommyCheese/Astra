## 1. Run 取消终态与持久化

- [ ] 1.1 将 `cancelled` 纳入 Run/SSE/前端终态集合，并补充状态显示与类型契约
- [ ] 1.2 在 RunRepository 实现幂等取消收敛，写入 terminal reason、部分回答和 `run.cancelled` 事件
- [ ] 1.3 取消收敛时将活动 Step、ToolCall、AgentTurn 和模型 invocation 标记为 cancelled/interrupted

## 2. 后端执行取消链路

- [ ] 2.1 将后台任务集合改为按 run id 索引的注册表，并实现安全的任务取消与完成清理
- [ ] 2.2 新增 `POST /api/runs/{run_id}/cancel` 幂等 API，处理不存在、已完成与并发取消
- [ ] 2.3 在 RunEngine 和模型客户端传播 CancelledError、刷新 answer buffer 并记录 interrupted 用量
- [ ] 2.4 让 SSE 在 cancelled 终态发送剩余事件后关闭

## 3. Chat UI 终止交互

- [ ] 3.1 在前端 API 增加 cancelRun，并为创建中取消维护待处理取消意图
- [ ] 3.2 活动 Run 时将发送按钮切换为可访问终止按钮，终止中禁止重复点击并在结束后恢复
- [ ] 3.3 保留部分回答或显示已终止状态，并允许在同一 Task 中继续追问

## 4. 验证

- [ ] 4.1 增加 Repository、API、Engine/模型取消测试，覆盖幂等、自然完成竞态和执行记录清理
- [ ] 4.2 增加前端测试，覆盖创建中终止、执行中终止、按钮恢复和取消后继续追问
- [ ] 4.3 运行后端与前端相关测试、类型检查和生产构建，并修复发现的问题
