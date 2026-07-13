## Context

Astra 已有一条低延迟回答链路：创建 Run 后立即连接 SSE，后端持久化并批量发送 `answer.delta`，前端使用 `requestAnimationFrame` 合并渲染。Agent Loop 同时已经持久化 `AgentTurn.reasoning_summary`，并产生 `agent_turn.created`、`tool_call.started/completed`、`reflection.created` 和验证事件。

当前缺口不在传输能力，而在投影方式：首轮模型决策完成前没有过程事件；模型流只提取最终答案 `summary`；前端对非回答事件重新加载完整 RunView，并把 `ProcessPanel` 渲染为默认折叠的终态审计面板。实现需要跨越模型流解析、Agent Loop 事件、SSE 恢复、React 状态与过程面板交互，同时必须维持“不暴露隐藏思维链”的安全边界。

## Goals / Non-Goals

**Goals:**

- 用户提交后立即看到一个真实、持续更新的 Astra 过程区域。
- 流式展示运行阶段和模型主动生成的简洁、可审计 `reasoning_summary`。
- 工具、反思、验证和回答组织事件按实际执行顺序进入同一时间线。
- 高频过程增量最多每动画帧触发一次 React 可见更新，并保留 SSE 重连恢复能力。
- 运行期间默认展开过程面板，尊重用户主动收起；回答完成后保留可展开审计记录。
- 终态 RunView 继续作为持久化事实来源，实时状态仅作为增量投影。

**Non-Goals:**

- 不显示、请求或推断模型供应商隐藏 Chain-of-Thought、reasoning token 内容或内部注意力状态。
- 不用 WebSocket 替换 SSE，不新增消息队列或数据库表。
- 不在过程事件中发送完整工具输入、凭据、宿主路径或未经清洗的模型响应。
- 不在本 change 中重做聊天信息架构、富文本协议或历史会话存储。

## Decisions

### 1. 扩展现有 SSE，而不是创建第二条实时通道

新增 `reasoning.phase.started`、`reasoning.summary.delta` 和 `reasoning.summary.completed`。现有 AgentTurn、ToolCall、Reflection、Step 与 Answer 事件保持不变并参与同一过程投影。所有事件继续写入 RunEvent，因此 `after_id` 回放和低频快照兜底仍然有效。

选择现有 SSE 是因为过程数据是服务端到客户端的单向有序事件，和回答 delta 具有相同生命周期。独立 WebSocket 会引入双通道排序、恢复和部署复杂度，没有必要。

### 2. 阶段事件由运行时拥有，推理摘要由模型显式输出

RunEngine 在规划开始时发出阶段事件；AgentLoop 每轮模型决策前发出选择行动阶段，工具、反思、验证和回答组织继续由实际节点事件驱动。阶段文案来自受控枚举，不让模型生成任意 UI 状态。

模型流解析器从同一 JSON 响应中同时提取顶层 `reasoning_summary` 和最终答案 `summary`。前者进入过程事件，后者继续进入回答事件。模型提示继续要求 `reasoning_summary` 简洁、适合审计，并禁止隐藏思维链。

考虑过只展示固定阶段文案，但首轮等待仍缺乏任务相关反馈；考虑过展示供应商 `reasoning_content`，但其可用性、隐私和产品安全均不可控，因此不采用。

### 3. 多字段流解析使用字段到回调的显式映射

`ModelClient._chat_json` 从单个 `stream_field/on_field_delta` 扩展为可选字段回调映射，并为每个字段独立维护已发送长度和完成状态。现有单字段调用保持兼容；`decide_with_answer` 同时注册 `reasoning_summary` 与 `summary`。

过程摘要增量经过短时间窗/长度阈值合并后持久化，完成时发出包含完整文本的 `reasoning.summary.completed`，用于重连纠错。不得按供应商原始 token 逐条提交数据库。

### 4. 前端建立可重放的 ProcessStreamState reducer

提交后立即创建包含 `planning` 阶段的 optimistic process state。SSE 回调直接把允许的事件归约为稳定条目，摘要 delta 先进入 ref buffer，再按 `requestAnimationFrame` 合并。关键事件仍触发受节流的 RunView 刷新，但 UI 不依赖刷新才能显示过程。

终态快照到达后，用 `run.turns/tool_calls/events` 重建并校正过程状态；事件 ID 用于去重。断流后继续使用现有 3 秒轮询，最终不会永久停在运行态。

### 5. ProcessPanel 采用运行期受控展开、终态保留的交互

过程消息从 Run 创建时就存在。首次运行默认展开；用户手动收起后不因普通 delta 强制打开，只显示活动指示和未读变化。若用户未干预，回答开始后自动收起，为答案让出阅读空间；终态仍可再次展开。

面板显示的是“阶段 + 可审计摘要 + 工具/反思/验证结果”，而不是模拟逐字内心独白。无过程内容时不显示“0 次工具调用”。

### 6. 安全与可访问性是协议约束

过程事件 payload 只允许阶段枚举、turn/tool 标识、公开工具名、截断后的摘要和状态。前端对过程区域使用 `aria-live=polite`，但仅播报阶段完成或新稳定条目，不逐字符播报 delta。错误信息使用现有安全错误合同，不回显原始模型响应。

## Risks / Trade-offs

- [模型把 `reasoning_summary` 放在 JSON 较后位置] → 提示固定字段顺序，并始终先发运行时阶段事件，避免空白等待。
- [字段名 `summary` 在嵌套对象中出现歧义] → 多字段解析器按首次合法字段和完成状态工作，测试 `reasoning_summary` 与 `final_answer.summary` 的顺序；后续可演进为字段路径解析。
- [过程 delta 增加 RunEvent 数量] → 使用与回答相同的短窗口合并、长度阈值和完成事件，不逐 token 持久化。
- [SSE 事件与 RunView 刷新竞争造成重复条目] → 使用稳定事件/turn/tool ID 归约，终态快照执行确定性校正。
- [自动展开干扰用户阅读] → 只在首次运行且用户尚未干预时自动控制；用户手动选择优先。
- [“思考过程”被误解为原始思维链] → UI 与规格统一使用“可审计过程/推理摘要”语义，禁止供应商隐藏推理字段进入协议。

## Migration Plan

1. 先增加后端事件与多字段流解析；旧前端忽略未知事件。
2. 增加前端 reducer 和实时 ProcessPanel；保留现有快照刷新与轮询兜底。
3. 上线自动展开、回答衔接与无障碍行为，并完成 mock、真实 SSE 和浏览器验证。
4. 若发生问题，可关闭过程 delta 生产，仅保留阶段和现有 AgentTurn 事件；回答流不受影响。

## Open Questions

- 本轮默认采用“回答开始时自动收起，但尊重用户手动展开/收起”；后续可根据真实使用反馈调整为回答完成时再收起。
- 过程摘要第一版只展示纯文本；是否支持安全的引用和工具输出定位，沿用现有 ProcessPanel 能力，不新增 Markdown UI DSL。
