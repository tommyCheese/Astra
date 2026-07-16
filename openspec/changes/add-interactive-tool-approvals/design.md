## Context

Astra 已有 `plan_only`、`request_approval`、`auto_approval` 三种执行模式、`policy_gate → execute` 运行时节点、`waiting_user`/continuation token 恢复机制和严格隔离的 Docker Sandbox。然而 `request_approval` 尚未参与 Tool Router 后的执行决策，当前 ToolCall 创建后立即运行；通用 resume 接口也只把 `approved` 当作普通 observation，不能保证批准的行动与实际执行的行动一致。

该变化同时涉及运行时检查点、持久化、API、SSE/RunView、聊天 composer 和高风险命令工具。现有安全约束要求所有应用工具继续使用 `sandbox.remote`，不得挂载 Docker socket 或宿主目录。

## Goals / Non-Goals

**Goals:**

- 让 `request_approval` 在每次未获授权的工具调用执行前可靠暂停。
- 冻结待执行工具行动，使批准、刷新或进程重启后执行的仍是用户审查的输入。
- 支持一次性批准、Run 级相似调用授权和拒绝，并提供防重放审计。
- 通过 `bash_execute` 验证高风险工具仍受 Tool Router、批准闸门和 Docker 沙箱共同约束。
- 保持现有普通追问/澄清的 waiting-user 恢复行为。

**Non-Goals:**

- 不提供跨 Run、跨会话或全局永久命令授权。
- 不允许 `bash_execute` 访问宿主工作区、Docker socket、宿主环境变量或公网。
- 不构建完整终端、交互式 TTY、后台进程或流式 stdout。
- 不让自动批准绕过平台权限、安全策略或沙箱后端检查。

## Decisions

### 使用独立 ApprovalRequest 和 ApprovalGrant 持久模型

ApprovalRequest 保存 Run、Turn、ToolCall、工具名、冻结输入、规范化输入哈希、建议规则、状态和决定时间；ApprovalGrant 保存 Run、工具名、后端生成的 matcher 和来源请求。独立记录比把全部内容塞入 `waiting_state` 更适合审计、防重放和恢复；`waiting_state` 仅携带当前批准请求的展示引用与 continuation token。

备选方案是只保存到 Run JSON。它迁移较少，但无法可靠约束唯一性、消费一次性决定或查询审计轨迹，因此不采用。

### 在 Tool Router 解析后、Tool.run 前执行批准策略

运行时先解析工具 manifest、输入和平台权限，再计算批准决策。`request_approval` 对未匹配 Grant 的工具调用创建 `awaiting_approval` ToolCall 和 ApprovalRequest，然后持久化 frozen action 并进入 `waiting_user`；`auto_approval` 可直接进入执行，但仍经过 Router。

批准后从 ApprovalRequest 恢复同一 ToolCall，不再次向模型请求行动。拒绝把 ToolCall 标记为 `rejected`，产生 `approval_result` observation，并从 `select_action` 继续。这样避免“批准 A、执行 B”的 TOCTOU 问题。

### 使用专用批准决策 API

新增 `POST /api/runs/{run_id}/approvals/{approval_id}/decision`，请求包含 `decision=approve_once|allow_similar|reject` 和 continuation token。后端原子校验 Run、pending 状态、token 和未消费决定。普通 `/resume` 继续服务澄清文本，不承担工具批准。

### 相似授权为 Run 级、工具特定、后端生成

Grant 仅在同一 Run 内生效。一般工具默认只能生成精确输入 matcher；`bash_execute` 对无 shell 元字符的简单命令生成首个稳定命令前缀，例如 `pytest` 或 `npm test`。含管道、重定向、命令替换、控制操作符或无法可靠解析的命令不提供“允许类似命令”，UI 只显示一次性批准和拒绝。

模型不能提交或扩大 matcher。匹配总是同时校验工具名、版本和规范化输入字段，避免授权从一个工具泄漏到另一个工具。

### `bash_execute` 复用一次性 Docker Sandbox Job

工具 manifest 使用 `command_execute` capability/permission、`high` risk、`sandbox.remote` backend 和 `external_side_effect` side-effect level。工具把命令写入隔离输入，容器通过固定入口点 `/bin/bash --noprofile --norc` 执行；默认断网、只读 rootfs、非 root、drop capabilities、限制 wall time/CPU/内存/PID，不上传宿主工作区或秘密。

结果返回 exit code、经过脱敏和长度限制的 stdout/stderr。非零退出是正常结构化结果而非 Sandbox 基础设施失败，因此命令包装器本身以成功退出并回传子命令状态。

### 批准卡片由 RunView 恢复、事件负责低延迟刷新

RunView 暴露当前 pending approval 的安全展示 DTO；`approval.requested` 事件触发立即刷新。composer 上方渲染卡片，显示工具、命令摘要、权限和影响范围。决定提交期间禁用按钮；刷新页面后根据 RunView 恢复，而不依赖仅存在于 React state 的临时数据。

## Risks / Trade-offs

- [批准请求和 AgentLoop 恢复可能发生重复提交] → 决策使用 pending 条件更新和 continuation token，ToolCall 只允许从 `awaiting_approval` 进入一个后继状态。
- [相似 Bash 匹配规则过宽] → 仅对可安全 token 化且无 shell 元字符的命令生成短前缀；复杂命令只允许精确批准。
- [Run 被视为 terminal 导致 SSE 在 waiting_user 时关闭] → pending approval 以 RunView 为恢复源，决定后重新建立当前运行流；前端明确区分“暂停”与真正终态。
- [命令输出泄漏敏感信息或耗尽存储] → 复用日志脱敏并限制 stdout/stderr 大小，不保存宿主环境或工作区内容。
- [新增高风险工具扩大攻击面] → 默认关闭工具开关；仅在 Docker sandbox 可用时注册，且不挂载宿主资源、不联网。

## Migration Plan

1. 新增批准请求/授权表和 `bash_execute` 工具开关迁移，默认关闭命令工具。
2. 部署后端持久化、策略闸门、决策 API 和沙箱工具；旧 Run 没有批准记录时保持兼容。
3. 部署前端批准卡片；前端未知 approval 字段时仍可展示现有 waiting message。
4. 通过策略、恢复、防重放、沙箱和 UI 测试后显式启用 `bash_execute` 测试开关。
5. 回滚时先关闭工具开关，再回滚应用；新增表可保留以避免破坏审计记录。

## Open Questions

- 后续若要运行项目测试，应另行设计只读工作区快照及可控输出补丁，本 change 不开放宿主挂载。
