## Context

`PolicyCompiler` 已将用户选择编译为不可变的 `ReasoningPolicySnapshot`，但模型请求未将生效的 `reasoning_effort` 传入模型。与此同时，Astra 已有 OpenAI-compatible Chat Completions 与 Anthropic Messages 两种传输层；OpenAI、Anthropic、DashScope/Qwen 的推理参数不同，而 Gemini 原生协议还具有不同 endpoint、消息和流事件结构。

## Goals / Non-Goals

**Goals:**

- 在模型调用前用纯函数解析 Provider、模型、操作类型和 Astra 推理强度，生成可测试的请求参数。
- 首先支持当前传输层能够安全表达的 OpenAI GPT、Anthropic Claude 与 DashScope/Qwen 推理控制。
- 明确识别 DeepSeek、Gemini 等模型的能力边界；当前传输无法可靠表达时省略参数并记录原因。
- 让同一 Run 的不可变生效策略贯穿 plan、contract、decision、reflection、finalize 和 memory 调用。
- 记录每次调用实际应用的推理配置，但不持久化隐藏思考内容。

**Non-Goals:**

- 本次不新增 Gemini GenerateContent 原生传输实现。
- 不自动迁移默认模型，也不改变用户保存的策略结构。
- 不暴露或保存模型的原始 chain-of-thought。

## Decisions

### 1. 使用规范化策略和 Provider 能力解析器

新增独立的 `model_reasoning.py`，输入 `provider`、`model`、`ReasoningEffort`、模型操作和是否使用 JSON mode，输出不可变的 `ModelReasoningConfig`。输出包含请求参数、是否应用、适配器标识、降级原因和是否允许 JSON mode。

选择纯函数而不是在 `_chat_json()` 内堆叠条件，是为了让能力矩阵可以独立测试，并让未来原生 Transport 复用同一个规范化策略。

### 2. 只发送已知兼容参数

- OpenAI GPT-5 系列映射为 `reasoning_effort`：fast=`minimal`、balanced=`low`、deep=`high`。
- 已知支持 effort 的 Claude 型号通过 Messages API 的 `output_config.effort` 映射为 low、medium、high。
- DashScope/Qwen 混合思考模型使用 `enable_thinking`；balanced/deep 额外使用有界 `thinking_budget`。fast 关闭思考。
- 对 DeepSeek 原生 `deepseek-reasoner` 和 Gemini，在当前传输无法确认等价强度参数时不发送推理参数，并返回可观测的降级原因。
- 未知 Provider/模型默认不发送额外字段。

这比将 `reasoning_effort` 盲目透传到所有兼容 endpoint 更安全，避免不支持字段导致请求失败。

### 3. Qwen thinking 与 JSON mode 冲突时优先结构化控制流

Agent 控制器依赖 JSON 对象解析。若已知 Qwen endpoint 在 thinking 模式下不兼容 `response_format`，适配器将关闭 JSON mode 字段，但保留“只返回 JSON”的提示和现有解析/一次修复重试。fast 模式关闭 thinking，可继续使用 JSON mode。

### 4. 每个 Run 绑定一次生效推理强度

`RunEngine` 在绑定 Agent profile 时同时把 Run 的 effective reasoning effort 绑定到 ModelClient。ModelClient 的所有后续操作复用该值，避免 plan/contract 与 Agent Loop 使用不同策略。未绑定或测试桩调用时回退到 balanced。

### 5. 在 ModelInvocation 的 raw_usage 中记录适配结果

调用完成时把适配器、请求参数键、应用状态和降级原因合并进 usage metadata；不记录 API key、消息正文或思考内容。现有数据库 JSON 字段足以承载，无需迁移。

## Risks / Trade-offs

- [Provider 的兼容接口行为可能变化] → 能力解析采用显式 allowlist，未知模型安全省略，并以测试固定当前行为。
- [Qwen 深度模式关闭 JSON mode 后更易产生非法 JSON] → 保留严格提示、解析器和一次格式修复重试，并覆盖测试。
- [同名模型可能由不同网关提供] → Provider 优先于模型名判断；只有明确 Provider/模型组合才应用参数。
- [推理强度降低可能影响控制器质量] → balanced 使用低推理而非完全关闭；deep 保留高强度，运行时安全和验证规则不变。
- [Gemini 暂未获得参数控制] → 明确记录 unsupported transport，未来新增原生 Transport 时复用规范化策略。

## Migration Plan

1. 上线能力解析器和单元测试。
2. 接入 ModelClient，并保持未知 Provider 请求与现状完全一致。
3. 用 fake endpoint 验证 OpenAI/Qwen 请求体与降级 metadata。
4. 重启后用 fast、balanced、deep 各创建一次 Run，检查 model invocation usage 和完成状态。

回滚只需移除请求参数合并和策略绑定；数据库无需回退。

## Open Questions

- 后续是否为 Anthropic Messages API 与 Gemini GenerateContent 建立独立 Transport，将作为单独 change 评估。
