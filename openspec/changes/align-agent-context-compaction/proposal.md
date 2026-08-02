## Why

Astra 当前只在新 Run 前把旧 Run 摘要按字符拼接截断，root/child Agent loop 内仍累积未压缩 observations；子 Agent 虽有 `SubagentContextCheckpoint`，却没有自动触发、摘要生成或压缩后上下文重建。长工具循环因此可能溢出或丢失关键状态，现有行为也未达到 Codex式长循环 checkpoint、LangChain 独立 Agent summarization 与 Anthropic artifact-first handoff 所体现的最新实践。

## What Changes

- 引入共享、角色感知的 Agent 上下文压缩生命周期，统一真实/估算 Token 计量、阈值检查、pre-model 与 post-tool 触发、checkpoint 持久化、恢复、审计和失败处理。
- 为 root Agent 提供与 Codex 行为对齐、但完全由 Astra 编排的语义 checkpoint：Astra 通过现有通用模型生成接口请求结构化摘要，并以“受保护前缀 + 语义 checkpoint + 最近原始上下文”替换活动模型历史。
- 明确禁止依赖 `/responses/compact`、`context_management`、compaction trigger、opaque/encrypted compaction item 或任何 Provider 专有压缩参数、端点和返回格式。
- 将主对话的字符尾截断升级为累积语义摘要；保留完整审计历史、最近原始 Run 和 `/compact`，但不再把固定字符拼接视为最终压缩算法。
- 为每个子 Agent 建立独立压缩窗口和 child-specific checkpoint，始终保护 DelegationContract、权限与 Catalog 摘要、Workspace scope、预算和终止条件，只压缩局部执行主体。
- 子 Agent checkpoint 使用结构化进度、已完成步骤、Evidence/Artifact 引用、局部事实、近期失败、未决问题、下一动作和剩余预算；禁止带入父/兄弟私有上下文或自动提升未验证事实。
- 大型工具输出先截断并外置为 Artifact/Evidence，checkpoint 只保留有来源的摘要与稳定引用；主、子 Agent 均保留少量近期原始 observations 以降低摘要损失。
- 增加压缩质量、上下文容量、重复压缩、恢复兼容性、权限隔离和长程任务连续性的确定性测试与评测。
- 迁移现有 `context_state` 和 child checkpoint，旧摘要可作为低信任历史输入读取，但后续写入采用版本化的新 checkpoint schema。

## Capabilities

### New Capabilities

- `agent-context-compaction`: 定义 root/child Agent 共享压缩生命周期、角色策略、Astra 管理的结构化 checkpoint、历史重建、恢复、隔离、可观测性和质量门槛。

### Modified Capabilities

- `conversation-context-management`: 将主对话从固定字符 Run 折叠升级为 Token 预算驱动的累积语义 checkpoint，同时保持模型可见历史与完整审计历史分离。
- `general-agent-reasoning`: 要求 standard/trusted root Agent loop 在模型调用前和工具结果后检测上下文压力并安全压缩，而不是在单个 Run 内无界累积 observations。

## Impact

- 后端：`conversation_context.py`、root Agent loop、subagent executor/context/recovery、通用模型客户端、usage 记录、Artifact/Evidence 管线和数据库 checkpoint schema。
- API/UI：上下文状态增加压缩实现、窗口编号、压缩前后 Token、质量/失败状态；现有 `/compact` 与容量面板保持兼容。
- 数据：需要版本化迁移 `TaskRecord.context_state`、root AgentState 和 `AgentExecutionRecord.checkpoint`，不得删除原始 Run、Turn、ToolCall、Artifact 或 Evidence。
- 运行成本：Astra 管理的语义压缩会增加普通模型调用；必须单独计量、预算、限流和缓存，但不得切换到 Provider 专有压缩路径。
- 变更协调：需与已完成但尚未归档的 `add-governed-subagent-runtime` 中 ContextManifest/checkpoint 契约对齐，并与正在进行的并行执行和 subagent supervision 变更避免状态字段冲突。
