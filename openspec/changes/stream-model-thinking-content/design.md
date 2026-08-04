## Context

Astra 的模型思考开关目前只改变 Provider 请求参数。OpenAI-compatible 传输只消费 `choices[0].delta.content`，因此 Qwen 和 DeepSeek 返回的 `reasoning_content` 被丢弃；Anthropic 适配器又显式设置 `thinking.display = "omitted"`，不会产生 `thinking_delta`。与此同时，前端“思考”时间线只表达 Astra 的 `reasoning_summary`、阶段、工具、反思和验证。

供应商公开程度不同：Qwen/DeepSeek 可以返回 `reasoning_content`；Anthropic 的 `display: "summarized"` 返回可见但不是原始思维链的摘要；OpenAI Chat Completions 通常只提供推理用量，而 Responses API 才可能提供模型允许的 reasoning text/summary。实现必须准确描述收到的内容，不把供应商摘要宣传成原始隐藏思维链。

## Goals / Non-Goals

**Goals:**

- 在有效模型思考开启时，完整保留并流式展示供应商明确公开返回的思考文本。
- 将模型思考正文与 Astra 自己的公开决策摘要严格分流。
- 支持刷新、断线重连、历史 Run 和内容不可用/截断状态。
- 忽略签名、加密块、redacted thinking、提示和其他敏感字段。

**Non-Goals:**

- 解密、推断或要求供应商返回未公开的原始思维链。
- 将模型思考正文重新注入 Astra Agent 上下文或用于工具授权。
- 在本变更中把 OpenAI Chat Completions 迁移到 Responses API。
- 将模型思考正文放入对话分享快照、记忆、usage metadata 或子 Agent 委派上下文。

## Decisions

### 使用独立 `model_thinking.*` Run 事件

新增 `started`、`delta`、`completed` 和 `unavailable` 事件。事件携带稳定 `stream_id`、Provider、模型操作、内容层级（`reasoning` 或 `summary`）、增量以及截断/不可用状态。它们不使用 `reasoning.*` 前缀，避免旧客户端和审计逻辑把 Provider 内容误认为 Astra 的决策理由。

备选方案是把正文放进 `reasoning.summary.delta`；该方案会破坏现有 4,000 字符摘要合同，也无法可靠区分安全摘要与供应商内容，因此不采用。

### 在模型客户端绑定 Run 级观察器

`ModelClient` 新增可选的模型思考流观察器。RunEngine 在加载不可变思考快照后绑定一个带 Run 上下文的 writer；每次传输请求再用 operation 和 invocation stream ID 包装回调。这样 contract、plan、decision、reflection、synthesis 和 memory 等操作共享一个协议，不需要扩大每个业务方法签名。

观察器只在有效思考开启时绑定。Mock/旧客户端使用基类 no-op 保持兼容。子 Agent 的独立客户端后续可绑定到相同协议，但首版不将其内容合并进父 Run，避免并发顺序和隔离语义不清。

### 只解析明确列入兼容表的响应字段

- OpenAI-compatible SSE：`choices[0].delta.reasoning_content`；非流式为 `choices[0].message.reasoning_content`。
- Anthropic SSE：仅 `content_block_delta.delta.type == "thinking_delta"` 的 `delta.thinking`；非流式仅 `content[].type == "thinking"` 的 `thinking`。
- Anthropic 启用思考时把 `display` 从 `omitted` 改为 `summarized`，并标记内容层级为 `summary`。
- 不解析 `signature_delta`、`redacted_thinking`、`encrypted_content` 或未知扩展字段。

OpenAI 当前 Chat Completions 没有可靠的可见思考正文合同，因此只在端点实际返回已声明的兼容字段时展示；未来 Responses API 支持作为独立适配器添加。

### 事件持久化采用缓冲写入和显式上限

Run 级 writer 按时间或字符阈值合并增量后提交，避免每个 token 一条数据库事务。每次 invocation 最多保留 256 KiB、单 Run 最多保留 1 MiB；超过后停止保存正文并在 completed 事件标记 `truncated: true`。前端只通过事件重建，不新增数据库列或迁移。

### 前端使用独立可展开条目

`processStream` 新增 `model_thinking` item kind，以 `stream_id` 聚合增量，保留空白和换行。时间线默认显示 Provider/operation 与生成状态，正文区域使用预格式化、可换行文本；历史快照从相同事件恢复。供应商未返回正文时显示“该模型未公开可展示的思考内容”，不会用 Astra 摘要填充。

模型思考条目使用独立的流式卡片视觉层级，标题区区分内容来源、操作与生成状态。用户展开条目时，正文滚动容器在每个渲染帧跟随最新增量到底部；折叠后停止跟随，避免影响对话主滚动。过程时间线行使用稳定 props 和记忆化渲染，回答打字机在每个动画帧至少推进一次并根据积压量扩大批次，以减少主线程工作同时保持流畅观感。

Run 投影额外提供从创建（或显式开始）到完成的非负处理耗时。过程面板仅在思考结束后将其格式化为紧凑的“已处理 X 秒/分钟/小时”，与“思考完成”并列；运行中不显示尚未固定的计时，历史对话沿用持久化的终态耗时。

## Risks / Trade-offs

- [思考文本可能含敏感任务信息] → 只在用户显式开启时保存，不进入分享、记忆、子 Agent 上下文或 usage；UI 清楚提示其持久化属性。
- [Anthropic 返回的是摘要而非原始思维链] → 事件和 UI 标记 `summary`，文案使用“供应商思考摘要”。
- [大量文本增加 SQLite 写入和 SSE 压力] → 缓冲提交、broker 合并 delta、单调用与单 Run 上限、显式截断。
- [重试导致重复思考条目] → 每次请求使用独立 stream ID 和 attempt，保留真实调用历史而不拼接为一次调用。
- [共享模型客户端造成跨 Run 回调污染] → 回调绑定于 RunEngine 私有模型客户端；共享的仅是无状态 HTTP client。

## Migration Plan

1. 先发布后端新事件；旧前端会忽略未知事件。
2. 再发布前端 reducer 和展示组件；历史 Run 没有新事件时保持现状。
3. 不需要数据库迁移；回滚时停止产生新事件，已有未知事件仍可安全保留。

## Open Questions

- OpenAI Responses API 的 reasoning text/summary 支持应在后续传输迁移中单独设计，不能假定 Chat Completions 返回相同字段。
- 子 Agent 的模型思考应在父时间线聚合还是只在子系统详情中展示，需要结合隔离与并发审计另行定义。
