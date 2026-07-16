## Why

“请求批准”执行模式目前只改变策略展示，工具调用仍会从策略门直接进入执行，用户无法在副作用发生前审查或拒绝行动。Astra 需要一个可恢复、可审计的批准闸门，并需要一个真实的命令工具来验证权限控制覆盖高风险执行路径。

## What Changes

- 在 `request_approval` 模式下，于工具执行前创建冻结的批准请求并暂停 Run。
- 在聊天输入框上方展示批准卡片，支持“仅本次”“允许类似命令”和“拒绝”。
- 持久化批准请求及 Run 级相似调用授权，确保刷新、重启、重放和拒绝均可审计。
- 增加专用批准决策 API；批准后执行被用户审查的同一工具调用，拒绝后把结果作为 observation 交回 Agent。
- 增加隔离的 `bash_execute` 工具，用于在无宿主挂载、默认断网的 Sandbox Job 中执行受控命令并返回退出码及截断日志。
- 保持 `auto_approval` 仅跳过交互确认，不能绕过工具注册、权限、风险、后端可用性或沙箱限制。

## Capabilities

### New Capabilities

- `interactive-tool-approval`: 工具批准请求、一次性批准、Run 级相似授权、拒绝、恢复和审计语义。
- `sandboxed-command-execution`: `bash_execute` 的输入输出、安全隔离、资源限制及错误语义。

### Modified Capabilities

- `policy-driven-tool-runtime`: Tool Router 解析后的行动必须服从执行模式和批准授权，且批准不能绕过平台权限。
- `agent-chat-ui`: 聊天输入区域需要呈现可恢复的批准卡片和三种明确决策。

## Impact

- 后端运行状态机、AgentLoop、Run/ToolCall 持久化、迁移、事件流、API schema 和工具 Registry。
- Docker sandbox 命令执行封装及运行时镜像能力。
- 前端 Run 类型、API 客户端、聊天 composer、i18n、样式和交互测试。
- 后端策略、恢复、防重放、命令沙箱和端到端测试。
