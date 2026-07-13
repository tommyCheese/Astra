## Context

当前提交路径是 `POST /runs -> GET /runs/{id} -> 打开 SSE`，天然把一次完整快照请求放在流连接之前。后端每个模型 delta 都写一条 Event 并 commit；SSE 遍历每条 delta 时额外 sleep 8ms；前端每收到非 delta 事件就拉取完整 RunView，每个 delta 又立即 setState、Markdown 解析和滚动。这些开销叠加后形成首 Token 慢、流中抖动以及回答文本已结束但 UI 仍停留在运行态的问题。

视觉上现有页面是全屏两栏 dashboard，透明度和层次有限。此次保留 Astra 青绿/蓝金色识别体系与现有信息架构，借鉴 macOS 的材质和景深，但不模拟红绿灯、独立系统窗口或额外悬浮浮板。

## Goals / Non-Goals

**Goals:**

- 用户提交后立即看见自己的消息与可感知的 Agent 启动态，SSE 尽早连接。
- 在本地网络与 mock provider 下，SSE ready 目标小于 200ms，后端收到模型首个可展示 answer delta 后小于 100ms 发到客户端。
- 回答完成事件到 UI 稳定最终态目标小于 250ms，不等待审计快照拉取才能移除光标。
- 将高频 delta 降为每帧最多一次 React 更新，并减少数据库提交与 SSE 事件数量。
- 建立 macOS 风格、Astra 原生色彩、浅深主题一致且支持 reduced motion 的聊天窗口。

**Non-Goals:**

- 不开发 Electron 原生标题栏、窗口拖拽或系统级 vibrancy API。
- 不改变 Agent 推理轮数、模型选择策略或工具执行逻辑。
- 不将结构化 JSON final answer 协议改成纯文本协议。
- 不承诺第三方模型本身的首 Token 时间，只优化 Astra 引入的额外延迟。

## Decisions

### 1. 先建流，再取快照

`createRun` 返回后前端立即设置轻量 optimistic run 并打开 SSE，完整 RunView 并行获取。SSE 首帧输出 `stream.ready`，既可穿透代理缓冲也能给客户端明确连接状态。相比新增 WebSocket，该方案保持单向事件语义和现有兼容性。

### 2. 后端按短时间窗聚合 answer delta

模型客户端仍逐 chunk 解析 JSON 的 `summary` 字段，但引擎使用约 20ms/字符阈值的 buffer 合并持久化事件，取消 SSE 逐事件的 8ms sleep。这样不改变模型接口，却显著减少 SQLite commit 与事件回放成本。关键边界（started、completed）仍立即 commit。

### 3. 完成信号先收敛 UI，审计数据后落稳

`answer.completed` payload 始终包含最终 `content`。前端收到后立即将本地流文本固化、关闭光标并触发一次最终快照刷新；后端随后完成 artifact、verification 与 terminal status。若终态稍后到达，UI 显示轻量“正在整理记录”而不是继续表现为生成答案。

### 4. 前端按动画帧合并 delta

SSE 回调只追加到 ref buffer；`requestAnimationFrame` 每帧最多提交一次 state。最终事件会先 flush buffer，避免尾字符丢失。相比固定 debounce，该方法与浏览器渲染节奏一致，也不会人为增加慢速输出的明显延迟。

### 5. 材质系统由 CSS token 驱动

新增窗口、侧栏、顶部栏、内容面和 composer 的 surface token；使用有限的 `backdrop-filter`、半透明渐变、1px 内高光和多层柔和阴影。动态背景仅使用 transform/opacity，移动端降低模糊强度，`prefers-reduced-motion` 关闭非必要动画。

## Risks / Trade-offs

- [过多 backdrop-filter 增加 GPU 开销] → 只在窗口、侧栏和 composer 三个主要层级启用，并在小屏/不支持时回退到实色。
- [delta 聚合可能略微增加单个字符延迟] → 聚合窗限制在约一帧，并在标点、长度阈值和完成时立即 flush。
- [completed 早于 terminal status 导致短暂状态差] → 区分“回答已完成”和“运行已归档”，避免继续显示生成光标。
- [SSE 重连重复事件] → 客户端维护 last event id/started 状态，服务端继续支持 after_id 回放语义。

## Migration Plan

1. 先扩展 SSE payload，旧客户端会忽略新增事件和字段。
2. 上线前端帧合并和 optimistic run；保留 3 秒轮询作为断流兜底。
3. 上线后端事件聚合并运行 API/引擎回归测试。
4. 若异常，可独立回滚聚合逻辑，扩展后的 SSE 协议仍兼容旧行为。

## Open Questions

- Electron 化后是否接入真正的 `NSVisualEffectView`/系统 vibrancy，留待桌面壳变更处理。
