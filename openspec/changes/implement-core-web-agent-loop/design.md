## Context

Astra 目前的后端已经具备 Task/Run/Step/ToolCall/Artifact/RunEvent 的持久化模型，`ToolRegistry` 已能注册 `web_search` 和 `web_fetch`，Web 数据查询流程也能构造 Evidence Pack 并完成验证。当前限制是 `RunEngine` 仍然以固定函数 `_execute_web_query` 编排搜索、抓取、综合和验证，缺少通用 Agent loop 的逐轮决策、观察、反思、记忆召回和持续对话表达。

项目文档已经把 Agent 内核拆成规划器、执行器、反思器、验证器、记忆管理器和工具路由器。本 change 将实现第一版最小闭环：只开放基础 Web 工具，但将执行方式升级为可审计、可恢复、可测试的 Agent loop，并让前端呈现为聊天式 Agent 入口。

## Goals / Non-Goals

**Goals:**

- 实现 Web-only Agent loop，支持 plan、act、observe、reflect、verify、finalize。
- 将每一轮 Agent 决策持久化为可审计记录，不保存完整隐藏思维链。
- 只通过 ToolRegistry 调用 `web_search` / `web_fetch`，并对工具名、权限和副作用做 gating。
- 在工具失败、低质量来源、证据不足或验证失败时触发结构化反思。
- 新增 Memory 管理基础，支持 run/workspace/user memory 的读取、写入和 provenance。
- 将最终回复、工具事件、反思、来源和验证报告映射到聊天式前端。
- 保留现有 Timeline、ToolCall、Artifact 和 Evidence Pack 的审计能力。

**Non-Goals:**

- 不接入文件、shell、git、浏览器控制、消息发送、日历等高风险工具。
- 不实现无限自主后台任务、定时自我派活或跨会话自动执行。
- 不在第一版引入向量库或 embedding 检索；Memory 先使用结构化过滤和 recency。
- 不实现多 Agent 协作、权限审批 UI 或复杂组织知识图谱。
- 不移除现有 Run/Step/ToolCall/Artifact 模型。

## Decisions

### Decision 1: 新增 Agent loop 执行层，而不是继续扩展专用 Web 查询函数

现有 `_execute_web_query` 可以继续作为兼容路径或被逐步拆解，但核心实现应引入 `AgentEngine` / `AgentLoop`：

```text
RunEngine
  -> ContextAssembler
  -> AgentPlanner
  -> AgentLoop
       -> ModelDecision
       -> ToolRouter
       -> Observation
       -> Reflection
  -> VerificationEngine
  -> FinalResponse
```

理由：如果继续扩大 `_execute_web_query`，下一类工具或任务会继续产生分支逻辑，Agent 行为无法复用。通用 loop 可以让 Web-only MVP 先跑通，同时为后续工具扩展留出接口。

替代方案：直接把 Web 搜索、抓取、反思写成硬编码步骤。优点是快，缺点是无法表达模型逐轮决策和 memory 驱动的任务适配。

### Decision 2: 使用结构化 AgentDecision，不保存完整隐藏思维链

模型每轮输出结构化 JSON：

```json
{
  "decision_type": "call_tool",
  "reasoning_summary": "需要先搜索候选来源。",
  "tool_name": "web_search",
  "tool_input": {"query": "..."},
  "expected_observation": "返回候选来源",
  "stop_condition": "获得足够来源后抓取正文"
}
```

允许的 `decision_type`：

- `call_tool`
- `reflect`
- `replan`
- `finalize`
- `ask_user`
- `blocked`

理由：结构化决策便于测试、审计和 UI 展示，同时避免把完整内部推理链持久化。`reasoning_summary` 是产品可见的简短解释，不作为模型隐藏思维的替代。

替代方案：让模型直接返回自然语言行动计划。缺点是解析不稳定，工具调用和权限控制难以可靠执行。

### Decision 3: 将 Agent turn 作为一等运行记录

每轮循环需要保存：

- run_id
- turn_index
- decision_type
- reasoning_summary
- selected_tool
- tool_call_id
- observation
- reflection
- memory_reads
- memory_writes
- status

实现上可以选择新增 `agent_turns` 表，也可以先把 turn 存为 `Artifact(type="agent_turn")` 和 `RunEvent`。如果需要查询、排序和前端流式展示，新增表更干净；如果要减少 migration 风险，artifact/event 也能启动。推荐新增表，因为它将成为后续 Agent runtime 的核心审计对象。

### Decision 4: Memory 使用结构化存储，第一版不引入 embedding

新增 Memory 记录遵循文档字段：

- `scope`: `run` / `workspace` / `user`
- `kind`: `fact` / `preference` / `workflow` / `source_summary` / `failure_pattern`
- `content`
- `structured_data`
- `provenance`
- `confidence`
- `created_at`
- `updated_at`
- `expires_at`

召回策略第一版使用 `scope`、`kind`、`workspace_id`、`created_by`、`confidence`、`recency` 过滤。写入时必须带 `run_id`，并在可能时带 `tool_call_id` 或 `artifact_id`。

理由：embedding 会引入额外模型、索引和一致性复杂度。第一版先保证 memory 可解释、可审计、可删除。

替代方案：只把 memory 放进 run artifact。优点简单，缺点无法跨 run 召回 user/workspace memory。

### Decision 5: ToolRouter 做硬性 allowlist 和权限检查

第一版只允许：

- `web_search`
- `web_fetch`

即使模型输出其它工具名，也必须被拒绝并记录为 reflection/blocked。ToolRouter 负责检查：

- 工具是否注册
- 工具是否在 run mode allowlist 内
- permission 和 side_effect_level 是否允许
- tool_input 是否符合 schema
- loop 是否超过最大工具调用数或最大轮数

理由：模型输出不能直接等同于授权行为。工具路由器是 Agent 安全边界。

### Decision 6: 聊天 UI 是主视图，审计信息可展开

前端从 dashboard 主导变为聊天主导：

```text
┌────────────────────────────────────────────┐
│ Astra                                      │
├────────────────────────────────────────────┤
│ user bubble                                │
│ agent bubble: reasoning summary            │
│ tool event: web_search                     │
│ tool event: web_fetch                      │
│ reflection event                           │
│ final answer + sources                     │
├────────────────────────────────────────────┤
│ input composer                             │
└────────────────────────────────────────────┘
```

Timeline、ToolCall、Artifacts、Evidence Pack 和 Memory writes 放到可展开 panel 或右侧抽屉。这样主体验更接近 Gemini，但不会牺牲 Astra 的可审计性。

## Risks / Trade-offs

- [Risk] Agent loop 过早泛化导致实现复杂。→ Mitigation: 第一版只开放 Web 工具，最大轮数默认较小，mock model 提供确定性路径。
- [Risk] 反思循环可能无限重试。→ Mitigation: 设置 `max_turns`、`max_tool_calls`、每工具失败上限和最终阻塞条件。
- [Risk] Memory 写入污染长期记忆。→ Mitigation: 只写带 provenance 的记忆，低 confidence 默认不进入 user/workspace scope，UI 暴露 memory writes。
- [Risk] 模型可能请求未授权工具。→ Mitigation: ToolRouter allowlist 拒绝并记录 blocked/reflection，不执行未注册或未授权工具。
- [Risk] 聊天 UI 隐藏审计细节。→ Mitigation: 主视图保留工具事件摘要，详细 timeline 和 artifacts 可展开。
- [Risk] 真实模型输出 schema 不稳定。→ Mitigation: 使用 Pydantic schema validate，失败进入 reflection 或 blocked；mock client 覆盖确定性测试。

## Migration Plan

1. 增加 Agent loop 所需 schema、模型接口和持久化结构。
2. 保留现有 run 创建 API，但 run mode 从固定 Web 查询逐步映射到 Web Agent loop。
3. 将现有 Web 查询中的搜索、抓取、证据包和验证能力迁移为 loop 内的工具/观察/验证阶段。
4. 前端先兼容旧 RunView 字段，再读取新增 chat messages / turns / memory events。
5. 完成测试后，默认新 run 使用 Agent loop；保留旧逻辑作为短期回退直到验证完成。

Rollback 策略：如果 loop 行为不稳定，可通过配置将 run mode 切回现有固定 Web 查询流程；数据库新增表不影响旧流程读取。

## Open Questions

- 第一版 Memory 是否需要用户可编辑 UI，还是只展示 memory writes 并将编辑放到后续 change？
- Agent turn 是否作为独立表实现，还是先用 Artifact/RunEvent 启动？推荐独立表。
- 聊天输入是否支持多轮 follow-up 复用同一个 task，还是每次消息创建新 run？推荐第一版每次消息创建新 run，UI 呈现为连续会话。
