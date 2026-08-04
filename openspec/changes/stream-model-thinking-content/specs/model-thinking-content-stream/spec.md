## ADDED Requirements

### Requirement: 供应商公开思考正文通过独立事件流传输
系统 SHALL 从已声明兼容的供应商响应字段中提取模型公开返回的思考正文，并 SHALL 使用独立于 Astra `reasoning_summary` 的事件类型按原始顺序流式发布开始、增量和完成状态。系统 MUST NOT 将签名、加密思考块、Token 计数、普通回答正文或未知字段解释为思考正文。

#### Scenario: OpenAI 兼容端点返回 reasoning_content
- **WHEN** 已开启模型思考的调用在流式 `choices[0].delta.reasoning_content` 中返回文本
- **THEN** 系统按响应顺序发布 `model_thinking.started`、`model_thinking.delta` 和 `model_thinking.completed`
- **THEN** 普通 `delta.content` 继续仅用于 Astra 的结构化模型输出

#### Scenario: Anthropic 返回 thinking_delta
- **WHEN** 已开启模型思考的 Anthropic 调用返回 `content_block_delta` 类型的 `thinking_delta`
- **THEN** 系统仅提取 `delta.thinking` 并忽略 `signature_delta` 与 `redacted_thinking`

#### Scenario: 非流式响应返回公开思考正文
- **WHEN** 兼容供应商以非流式响应返回可见思考正文
- **THEN** 系统将正文作为一个有序增量持久化并产生完成事件

### Requirement: 模型思考正文遵循有效 Run 配置和供应商可用性
系统 SHALL 仅在 Run 的不可变有效模型思考配置为开启时请求、采集和展示供应商公开思考正文。若供应商只返回思考 Token 数、加密内容或不公开正文，系统 MUST NOT 推断正文，并 SHALL 记录稳定的不可用原因。

#### Scenario: 模型思考关闭
- **WHEN** Run 的有效模型思考配置为关闭
- **THEN** 系统不请求思考显示、不发布模型思考正文事件，并继续发布 Astra 公开运行摘要

#### Scenario: 供应商未返回正文
- **WHEN** 模型思考开启但调用完成时没有收到任何可见思考正文
- **THEN** 系统发布 `model_thinking.unavailable`，包含供应商、模型操作和稳定原因，不包含提示或响应秘密

#### Scenario: 供应商只返回推理摘要
- **WHEN** 供应商明确将返回内容定义为推理摘要而非原始思维链
- **THEN** 系统展示供应商返回的完整摘要并标记内容层级为 `summary`
- **THEN** UI 不将该内容描述为原始思维链

### Requirement: 模型思考事件可持久化恢复且有明确完整性状态
系统 SHALL 将已公开的模型思考增量作为 Run 事件持久化，使刷新、断线重连和历史 Run 能按事件顺序恢复相同文本。系统 MUST 对达到存储上限的内容发布截断状态，而不是静默丢失或错误声称完整。

#### Scenario: 页面刷新恢复思考正文
- **WHEN** 用户在模型思考流式生成后刷新页面
- **THEN** Run 快照从持久化事件重建同一操作的正文、内容层级和完成状态

#### Scenario: 实时流与权威快照包含相同增量
- **WHEN** 前端已接收模型思考实时增量，随后又收到包含相同持久化事件的 Run 快照
- **THEN** 前端从权威快照重建过程投影，不在现有正文后重复追加相同增量

#### Scenario: 思考正文超过上限
- **WHEN** 单次调用或单 Run 的公开思考正文超过配置的字符上限
- **THEN** 系统停止持久化后续正文并发布带有限制值的 `model_thinking.completed` 截断状态
- **THEN** UI 明确标记内容未完整保存
