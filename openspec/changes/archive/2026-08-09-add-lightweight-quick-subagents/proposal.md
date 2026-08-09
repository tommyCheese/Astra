## Why

快速模式当前会完全移除 `swarm`，即使任务包含适合并行读取、比较或独立检查的子问题，也只能由根 Agent 串行完成。我们需要让快速模式获得轻量、受治理的并发委派能力，同时复用现有 SubagentSupervisor、Swarm、Join 与权限治理，避免形成第二套子 Agent 运行时。

## What Changes

- 允许 eligible 的 standard Run 冻结一份更保守的快速 Subagent 策略，并在没有规范 Plan DAG 的情况下向根 Agent暴露现有 `swarm` built-in。
- 允许快速模式通过自适应 `subagent_mode = auto` 机会式委派，也允许 `/subagent <task>` 在当前快速模式下创建 `subagent_mode = required` 的 standard Run。
- 复用现有 Supervisor、child executor、Join reconciliation、权限衰减、预算、取消与恢复路径；模式差异只存在于运行画像、委派上下文、验证等级和完成门槛。
- 为快速模式设置独立且更紧的 child 数量、并发、深度、wall-time、token、调用和成本上限；首发仍保持 depth-one、read-only。
- 在快速对话中只显示紧凑的 Subagent 活动与结果摘要，不创建或展示可信 Plan DAG；trusted Run 保持现有 DAG 工作台与严格 Completion Gate。
- 在应用内帮助文档增加独立的“快速模式与可信模式”章节，完整说明定义、共享基础、执行与 Subagent 差异、选择建议和能力边界。
- 在应用内帮助文档增加“关于 Astra”章节，说明项目创建动机、使命、核心原则以及基于仓库 LICENSE 的版权与许可证信息。

## Capabilities

### New Capabilities

- `lightweight-quick-subagents`: 定义 standard Run 中无规范 DAG 的受治理轻量委派、保守预算、共享运行时、Join 和完成语义。

### Modified Capabilities

- `slash-system-commands`: `/subagent` 按当前回答模式创建 required-subagent Run，而不再无条件切换到 trusted。
- `general-agent-reasoning`: standard 根 Agent 可在冻结快速策略允许时直接选择 `swarm`，但仍不创建规范 Plan DAG。
- `agent-chat-ui`: 快速 Subagent 使用紧凑进度与结果呈现，并与可信 DAG 工作台保持清晰区分。
- `in-app-documentation-center`: 增加可独立导航的回答模式帮助主题，以当前运行时合同解释快速与可信模式。

## Impact

- 后端：Run profile/schema、Subagent policy 编译、AgentLoop Tool 资格判断、Supervisor 生命周期与完成判断。
- 前端：`/subagent` Run 创建参数、快速 Subagent 状态呈现和测试。
- 配置与治理：增加快速模式独立 rollout/预算边界，但继续使用现有 Swarm 用户开关和紧急停止开关。
- 数据与运行时：复用现有 AgentExecution、Delegation、Join、事件和恢复模型，不引入新的持久化执行体系。
