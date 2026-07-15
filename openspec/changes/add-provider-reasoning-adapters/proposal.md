## Why

Astra 当前的 `fast / balanced / deep` 只控制 Agent 循环预算，没有控制单次模型请求的推理强度，导致简单问答仍可能产生大量 reasoning tokens 和较长首字延迟。不同模型供应商对推理强度、开关和 token 预算使用不同参数，因此需要一个显式、可验证且能安全降级的适配层。

## What Changes

- 为模型 Provider 和具体模型声明推理相关能力，包括强度等级、思考开关、思考预算以及 JSON/流式兼容性。
- 将 Astra 的统一推理强度按 Provider/模型转换为 OpenAI、Anthropic、Gemini、Qwen 和 DeepSeek 兼容请求参数。
- 对不支持或无法确认的参数采用安全省略策略，避免因盲目透传产生 HTTP 400。
- 让模型调用记录保存实际应用的推理配置，便于诊断延迟和验证策略是否真实生效。
- 补充请求构建、能力识别、降级和运行时接入测试。

## Capabilities

### New Capabilities
- `provider-reasoning-adaptation`: 定义统一推理策略到不同 Provider/模型请求参数的能力识别、转换、降级和可观测行为。

### Modified Capabilities
- `reasoning-policy`: 推理强度除 Agent 循环预算外，还应控制支持该能力的单次模型请求推理配置。
- `runtime-reasoning-policy-enforcement`: 运行时必须将持久化的有效推理策略传递到模型调用适配层，并记录实际应用结果。

## Impact

- 主要影响 `backend/app/runner/model_client.py`、推理策略编译/运行时接线和模型调用 usage 记录。
- 新增 Provider/模型能力适配模块及后端单元测试。
- 不改变现有前端 API、数据库中用户策略结构或工具协议；不支持推理控制的模型继续按原请求运行。
