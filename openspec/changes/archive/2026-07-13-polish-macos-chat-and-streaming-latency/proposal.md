## Why

当前聊天界面虽已具备基础对话能力，但层级、材质与反馈仍偏网页面板，不能体现 Astra 作为桌面级通用 Agent 的精致感；同时首段回答要等运行创建与完整快照拉取后才建立 SSE，流式事件又逐条提交和刷新，造成明显首 Token 延迟及回答结束后的数秒卡顿。现在需要把视觉与流式链路作为一个完整体验共同优化。

## What Changes

- 将主界面升级为 macOS 风格的沉浸式聊天界面：分层半透明材质、动态背景光、细腻边框/阴影、模糊侧栏与焦点态；不模拟系统红绿灯或额外悬浮窗口。
- 移除聊天页右下角的悬浮广告浮板，减少对核心问答的干扰。
- 重构聊天消息与输入区的动效和视觉反馈，增加低干扰的首 Token 等待态、流式光标、平滑自动跟随及 reduced-motion 降级。
- 创建运行后立即建立事件流，不再等待完整 RunView；服务端在连接建立时立即输出 ready 事件，降低可感知等待。
- 对 answer delta 进行前后端批处理，减少数据库 commit、RunView 全量刷新、Markdown 重解析与布局抖动。
- 在 `answer.completed` 到达时携带可直接收敛的最终内容和状态提示，使 UI 立即退出流式态；最终审计与持久化在后台完成后再合并快照。
- 增加首事件、首 Token、结束收敛和流事件数量的回归测试，并记录性能预算。

## Capabilities

### New Capabilities
- `desktop-chat-materials`: 定义 macOS 风格聊天窗口、玻璃材质、交互动效、可访问性和响应式行为。
- `low-latency-answer-streaming`: 定义从提交到首 Token、delta 合并、完成收敛及断线恢复的低延迟流式协议。

### Modified Capabilities

无。

## Impact

- 前端：`frontend/src/App.tsx`、`frontend/src/styles.css`、`frontend/src/api.ts` 及相关测试。
- 后端：运行创建响应、SSE 事件端点、回答流事件写入/提交策略、运行引擎完成顺序及 API/引擎测试。
- API：SSE 增加 `stream.ready`，`answer.completed` 的 payload 扩展为可直接渲染的完成信号；保持现有客户端兼容。
- 依赖：不引入新的运行时依赖，优先使用 CSS backdrop-filter、requestAnimationFrame 与现有 FastAPI/SQLAlchemy 能力。
