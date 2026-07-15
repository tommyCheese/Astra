## Why

Astra 当前对所有问答默认执行较重的对话策略、任务契约和完整完成校验，普通问答的响应成本与等待感偏高，也没有把严格校验塑造成用户可主动选择和感知的产品能力。需要在保留同一套通用 Agent、工具和会话能力的前提下，提供默认快速回答与显式可信模式。

## What Changes

- 新增默认关闭、位于聊天输入区显著位置的“可信模式”开关；关闭时为快速回答，开启时进入可信模式。
- 将模式作为用户偏好持久化，并为每个 Run 保存不可变的模式及最终生效运行 profile 快照；继续已有 Run 时沿用原模式。
- 默认快速回答进入极速 Agent 路径，不创建 TaskContract、规范计划、反思、任务验证或 CompletionGate；首轮直接生成流式回答或选择工具，同时保留权限、安全、取消与通用工具能力。
- 可信模式应用现有持久化对话策略，并执行任务契约、领域 validator、VerificationEngine 与 CompletionGate 组成的完整校验链路。
- 把权限、工具参数与执行安全、Artifact 引用清洗和运行错误处理保留为两种模式共享的不可绕过边界；产品质量校验仅属于可信模式。
- 在可信回答的过程与结果中展示校验状态，使“已校验、带警告、未通过”成为可感知、可审计的产品价值。
- 通过共享的运行 profile 解析器选择策略与校验强度，避免维护两套 RunEngine 或 AgentLoop。

## Capabilities

### New Capabilities

- `answer-mode-selection`: 定义快速回答与可信模式的选择、持久化、Run 快照、继续执行和用户可见状态。

### Modified Capabilities

- `agent-chat-ui`: 在聊天输入区提供显著可信开关，并按模式展示策略入口与可信结果状态。
- `reasoning-policy`: 根据回答模式解析固定快速策略或用户保存的可信策略，同时保持执行审批策略独立。
- `general-agent-reasoning`: 让两种模式共享同一通用 Agent runtime，并由 profile 控制契约、规划和反思强度。
- `completion-gate`: 仅可信模式执行完整契约校验和严格完成门，快速回答仍执行不可关闭的基础保障。

## Impact

- 前端聊天输入区、模型/策略菜单、国际化文案、Run 展示与相关测试。
- `POST /api/runs` 请求、RunView、偏好 API 与 TypeScript API 类型。
- 对话策略偏好表和 Run 记录需要数据库迁移，以持久化首选模式与 Run 模式快照。
- PolicyCompiler、RunEngine、AgentLoop、VerificationEngine 与 CompletionGate 的调用边界。
- 不新增外部依赖，不拆分第二套 Agent runtime，不改变工具协议或权限模型。
