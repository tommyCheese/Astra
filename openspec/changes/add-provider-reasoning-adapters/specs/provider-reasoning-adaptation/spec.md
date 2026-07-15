## ADDED Requirements

### Requirement: Provider 能力决定模型推理请求参数
系统 SHALL 根据配置的 Provider、模型标识、模型操作和 Run 的生效推理强度生成模型请求参数，并 MUST NOT 向未知或不支持的组合发送未经确认的推理字段。

#### Scenario: OpenAI GPT 使用推理强度
- **WHEN** Run 使用支持推理控制的 OpenAI GPT 模型
- **THEN** 系统将 fast、balanced、deep 转换为该模型支持的 `reasoning_effort` 值

#### Scenario: Qwen 混合思考模型使用开关和预算
- **WHEN** Run 通过已识别的 DashScope/Qwen 兼容端点调用混合思考模型
- **THEN** 系统使用 `enable_thinking` 控制思考模式，并在启用时提供与推理强度对应的有界 `thinking_budget`

#### Scenario: 未知模型安全降级
- **WHEN** Provider 或模型没有声明支持推理控制
- **THEN** 系统省略额外推理参数并继续使用基础模型请求
- **THEN** 系统记录未应用参数的原因

### Requirement: 适配器处理推理模式与结构化输出兼容性
系统 MUST 在发送请求前应用 Provider/模型的功能兼容规则，并 SHALL 优先保证 Agent 控制协议可被可靠解析。

#### Scenario: Qwen thinking 不兼容 JSON mode
- **WHEN** Qwen 请求启用 thinking 且已知该模式不兼容 `response_format`
- **THEN** 系统省略 `response_format` 字段
- **THEN** 系统仍通过严格 JSON 提示、解析和有界修复重试校验控制器输出

### Requirement: 实际推理配置可观测
系统 SHALL 为每次模型调用记录实际选用的推理适配器、是否应用参数以及降级原因，并 MUST NOT 记录 API 凭据或隐藏思考正文。

#### Scenario: 调用应用了 Provider 参数
- **WHEN** 模型调用成功应用推理参数
- **THEN** 对应 ModelInvocation usage metadata 包含适配器标识和已应用配置

#### Scenario: 调用降级为基础请求
- **WHEN** 当前 Provider/模型或传输不支持推理参数
- **THEN** usage metadata 记录降级原因且调用继续执行
