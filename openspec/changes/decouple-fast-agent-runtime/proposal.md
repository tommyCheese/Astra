## Why

当前快速模式只是可信运行时的裁剪分支：仍共享其 profile、循环服务和若干可信执行假设。这使得快速功能必须随可信架构演进，难以采用业界通用的模型驱动 Agent loop，也把不需要的验证、状态编排和限制带入低延迟任务。

需要将快速模式重建为独立的快速 Agent 运行时，以模型能力和短反馈循环为中心；可信模式继续作为面向可审计交付的严格路径。

## What Changes

- **BREAKING** 将 `standard`（产品中显示为“快速模式”）从共享可信 Agent Loop 的分支改为独立的 `FastAgentRuntime`；它不再依赖 TaskContract、AgentState、Plan DAG、可信节点调度或可信完成门。
- 快速运行时采用模型驱动的 observe → decide → act loop：模型根据当前上下文自行决定是否调用可用工具、继续、重试、向用户提问或直接回答。
- 快速运行时移除可信模式的任务契约生成、计划/重规划、反思编排、领域 VerificationEngine、CompletionGate、证据包工件和完成声明校验；最终答案直接由模型输出并流式呈现。
- 快速运行时拥有独立的轻量配置、提示词、状态快照、事件语义、失败恢复和测试基线，可在不改动可信运行时的情况下快速迭代。
- **BREAKING** 快速模式不再使用可信模式的推理预算、验证等级、Subagent/DAG 配置或审计 UI；快速模式的并发委派、记忆写入和复杂工作流不作为首个独立运行时的能力，后续可按独立 Fast Runtime 扩展。
- 可信模式保持现有契约、计划、审批、反思、验证、CompletionGate、证据与治理行为，不因本变更降低约束。
- 保留所有模式共用的平台硬边界：工具目录启用状态、工具输入 Schema、权限/审批、Sandbox 隔离、取消、基础错误分类以及敏感数据边界。

## Capabilities

### New Capabilities

- `fast-agent-runtime`: 定义独立、模型驱动、低延迟的快速 Agent 生命周期、上下文、工具循环、流式回答、失败处理和明确排除的可信执行能力。

### Modified Capabilities

- `answer-mode-selection`: 两种模式改为选择不同运行时，而非同一运行时的策略档位。
- `general-agent-reasoning`: standard Run 不再进入可信 Agent Loop；trusted Run 保持现有任务契约、DAG 与验证生命周期。
- `completion-gate`: 快速运行时的终结语义从可信运行时中移出，并明确不产生验证报告或完成门决策。
- `agent-chat-ui`: 快速模式展示简洁的流式对话和工具活动；可信模式继续展示过程、计划图与验证状态。
- `reasoning-policy`: 可信推理策略只适用于 trusted Run；快速运行时使用独立且最小的运行配置。

## Impact

- 后端：Run 创建与恢复路由、运行时装配、模型提示词/输出协议、SSE 事件、持久化视图及测试夹具。
- 前端：模式说明、Composer 选项、流式过程展示、运行历史与审计面板的条件渲染。
- API/数据：保留 `answer_mode` 兼容值和历史 Run 可读性，但新增明确的 runtime kind 和快速运行快照；历史 standard Run 按旧记录只读展示。
- 运维：快速运行时可独立发布、观测与回滚，不与可信执行发布节奏绑定。
