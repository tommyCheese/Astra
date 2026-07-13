## 1. 后端过程事件协议

- [x] 1.1 扩展模型 JSON 流解析器，使 `reasoning_summary` 与最终答案 `summary` 可通过独立回调增量解析
- [x] 1.2 在 RunEngine 和 AgentLoop 中产生受控阶段、摘要增量与摘要完成事件，并合并细粒度 chunks
- [x] 1.3 保持 AgentTurn、ToolCall、Reflection、Verification 与 Answer 事件的顺序、恢复和安全边界

## 2. 前端实时过程状态

- [x] 2.1 定义 ProcessStreamState 与事件 reducer，支持 optimistic 初始阶段、事件去重和终态快照校正
- [x] 2.2 在 SSE 订阅中按动画帧合并摘要 delta，避免每个过程 delta 触发 RunView 请求
- [x] 2.3 将 ProcessPanel 改为运行期实时展示，支持默认展开、用户手动覆盖与回答阶段自动衔接

## 3. 体验与安全

- [x] 3.1 完善过程阶段、活动指示、无工具计数降级、错误与无障碍文案
- [x] 3.2 调整过程时间线样式和响应式布局，保持现有聊天视觉体系与 reduced-motion 行为

## 4. 验证

- [x] 4.1 增加模型多字段解析、后端事件顺序、SSE 恢复和隐藏推理不外泄测试
- [x] 4.2 增加前端 reducer、实时展开/收起、回答衔接和低频刷新测试
- [x] 4.3 运行前后端完整测试，并在浏览器验证长决策、工具调用、无工具回答和断流恢复体验
