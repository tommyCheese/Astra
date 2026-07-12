## 1. 流式协议与后端性能

- [x] 1.1 为 SSE 首帧增加 `stream.ready`，移除逐 delta 人工 sleep，并补充禁止代理缓冲/缓存的响应头
- [x] 1.2 在回答流写入层实现短时间窗 delta 聚合，确保 started、首 delta 与 completed 及时提交
- [x] 1.3 调整完成事件 payload 与运行收尾顺序，使回答可先于审计快照即时收敛
- [x] 1.4 增加 API 和引擎测试，覆盖 ready、delta 聚合、完整 completed content 与终态事件顺序

## 2. 前端低延迟流式体验

- [x] 2.1 创建运行后使用 optimistic run 立即挂载 SSE，并行获取 RunView 快照
- [x] 2.2 使用 requestAnimationFrame 合并 answer delta，完成时同步 flush 并立即退出 streaming 状态
- [x] 2.3 合并非 delta 快照刷新，避免事件风暴和回答结束后的重复请求
- [x] 2.4 增加首 Token 等待态、回答整理态及断流轮询恢复行为
- [x] 2.5 增加前端测试，覆盖即时等待反馈、流式 delta 合并和 completed 收敛

## 3. macOS 风格聊天窗口

- [x] 3.1 建立全屏环境背景、macOS 模糊层次与 Astra 材质 token，并移除红绿灯和广告浮板
- [x] 3.2 重构侧栏、顶部栏、消息气泡、过程面板和悬浮 composer 的玻璃层次
- [x] 3.3 优化消息进入、流式光标、焦点、滚动跟随及按钮微交互
- [x] 3.4 完善深色主题、移动端、backdrop-filter 降级和 prefers-reduced-motion

## 4. 验证与文档

- [x] 4.1 运行前端测试、类型检查和生产构建
- [x] 4.2 运行后端 API、引擎及完整测试集
- [x] 4.3 在浏览器中验证桌面/移动、浅色/深色、首 Token 与回答完成体验
- [x] 4.4 更新 OpenSpec 任务状态并记录验证结果
