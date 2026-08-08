## Context

`standard` 目前由 `AgentLoop` 的 `quick_mode` 分支实现。它虽然跳过可信的 TaskContract、Plan 与 CompletionGate，但仍与 trusted 共用运行时装配、Profile、决策服务、终结器和多处 `quick_mode` 条件；这使快速路径的任何改动都要回归可信生命周期。产品需要一个可独立演进、贴近通用 Agent 实践的快速路径：短上下文、模型自主决策、工具循环和低延迟流式回答。

可信模式仍需保持现有可审计交付语义。平台安全边界不是可信验证的一部分，仍必须由共享基础设施强制执行。

## Goals / Non-Goals

**Goals:**

- 让快速与可信模式由不同的运行时入口、状态、提示词、循环和终结器实现。
- 让快速运行时以模型输出作为下一动作的主要决定因素，避免计划、反思、验证和完成门的额外模型调用与状态门控。
- 使快速运行时可单独发布、观测、回滚和迭代，而不改变 trusted Run 行为。
- 保留一套共享的模型传输、ToolRouter、权限、审批、Sandbox、Artifact 存储、取消、会话和 SSE 基础设施。

**Non-Goals:**

- 不降低平台强制的授权、Schema、Sandbox、取消或敏感数据边界。
- 不迁移或重写历史 Run；历史 `standard` Run 按旧快照只读展示。
- 不在首版把 Subagent、记忆候选写入、DAG、Plan 确认或可信审计移植到 Fast Runtime。
- 不更改 `trusted` 的 API 值、结果合同或执行顺序。

## Decisions

### 1. 引入独立 `FastAgentRuntime` 包

新增 `application/fast_agent_runtime/`，由 `FastRunExecutor`、`FastContextBuilder`、`FastDecisionLoop`、`FastToolStage`、`FastFinalizer` 和 `FastRecovery` 组成。`RunApplicationService` 按冻结的 `runtime_kind` 分派执行器：`fast-v1` 或 `trusted-v1`。trusted 代码不再接收 `quick_mode` 参数。

选择单独包而非继续扩大 `if quick_mode`，以便快速路径可以修改提示词、循环协议和持久化快照而不影响可信路径；代价是初期会有少量共享模型/工具适配代码。

### 2. 快速循环采用模型自主的最小动作协议

快速模型调用仅要求返回结构化动作：`answer`、`call_tool`、`ask_user` 或 `stop`，并允许模型使用最近的用户上下文和已归一化工具观察决定下一步。循环不生成成功准则、预期节点结果、计划版本、反思补丁或验证要求。连续工具调用失败只记录标准化观察并交回模型，除基础故障/取消外不触发策略性阻塞。

保留结构化动作而非自由文本 ReAct 解析，以维持可靠流式、工具 Schema 绑定和可恢复的调用记录；它是快速协议，不是可信决策 schema 的子集。

### 3. 快速运行时只保留轻量可恢复状态

新增版本化 `fast_runtime_snapshot`（消息轮次、最近观察、待审批/工具调用引用、终态意图和协议版本）。它不包含 TaskContract、AgentState、Plan 或完成标准。Run 继续保存 `answer_mode`，并在 execution profile 中持久化 `runtime_kind` 和 `runtime_version`；恢复由对应运行时解释快照。

历史 `standard` Run 没有 `runtime_kind` 时保留旧读取路径；新 Run 一律写入 `fast-v1`。这允许渐进发布与回滚，而不需要数据重写。

### 4. 工具执行复用共享边界，但不复用可信评估

FastToolStage 调用既有工具选择/执行基础设施、ToolRouter、效果分析、审批、Schema 校验和 Sandbox。结果通过公共归一化器转为 Fast Observation，不调用 node Evaluation、VerificationEngine、CompletionGate、evidence pack 或 memory candidate writer。Artifact 引用继续在最终持久化前清洗。

这样区分“平台不能被模式绕过的安全边界”和“可信模式才需要的交付证明”。

### 5. 明确快速模式的产品与事件合同

快速 Run 只发出 `fast.*` 阶段、动作、工具和回答事件；UI 仅显示简洁进度、工具行、审批和流式答案。它不显示反思、计划图、VerificationReport、CompletionDecision 或“已校验”状态。可信事件及 UI 不改变。前端 reducer 按 `runtime_kind` 选择投影，避免通过事件缺失来猜测模式。

### 6. 运行控制从可信 PolicyCompiler 中分离

可信 `ReasoningPolicy` 仅在 `trusted-v1` 编译和展示。快速运行时使用一个小型 `FastRuntimePolicy`：模型配置、最大上下文轮数、最大连续工具动作、流式节流和恢复策略；这些是部署保护参数而不是计划、反思或验证限制。前端不再把可信推理强度、验证或计划选项映射到快速 Run。

## Risks / Trade-offs

- [快速模型反复调用工具导致延迟/成本上升] → 保留可观测的动作计数和部署级熔断，默认只记录/告警，不采用可信完成门阻断。
- [两个运行时的共享边界出现行为漂移] → ToolRouter、审批、Sandbox、Artifact 清洗和取消只保留一个实现，并对两种 runtime 做契约测试。
- [历史 standard Run 无法恢复] → 新旧 runtime kind 显式版本化；迁移前的运行只允许读取或由 legacy executor 继续。
- [UI 将无验证的快速答案误认为可信交付] → 快速结果固定显示“快速回答”，不显示验证状态或可信标记。
- [快速路径修改造成模型输出协议不兼容] → 在 executor 边界做版本化 JSON 协议校验；协议错误以普通模型输出失败处理并可重试一次。

## Migration Plan

1. 添加 runtime kind、快照 schema、Fast Runtime 包和仅内部可用的执行分派，不改变默认模式。
2. 为 `fast-v1` 增加单元、流式、审批、取消、恢复和工具契约测试；并用影子运行记录与旧 standard 输出/延迟对比。
3. 让新 standard Run 使用 `fast-v1`，保留 `legacy-standard-v1` 读取/恢复兼容期；trusted Run 保持原路径。
4. 更新前端模式文案和过程投影，发布后监控首 token、完成率、工具错误、取消和恢复指标。
5. 回滚时将新 Run 分派回 legacy standard executor；已经启动的 `fast-v1` Run 依冻结 runtime kind 收敛。

## Open Questions

- 首版快速模式是否允许多轮工具调用无限接近模型自主，还是用部署级最大连续动作熔断；提案默认采用后者但不暴露为可信策略。
- 是否保留快速模式的轻量 Subagent，或在独立 runtime 稳定后以单独变更重新引入；本变更默认移除。
- 历史 standard 的可恢复兼容期长度及最终退役时间由发布数据决定。
