## Context

Astra 已有 `RunEngine -> AgentLoop -> ModelClient -> ToolRouter` 的 Web-only 执行链，能够保存 Agent turn、Observation、Reflection、Memory 和 Evidence Pack。当前初始计划与循环决策之间没有稳定的共享状态；`AgentDecision` 只描述动作，不关联成功标准或计划版本；Reflection 会被持久化但不会直接修订计划或状态；循环在 blocked、ask_user 或预算耗尽后仍统一进入 finalize；`VerificationEngine` 也依赖 Web 来源数量。前端已经提供推理强度、规划策略、反思开关/触发方式和执行模式，但这些值仍是本地 UI 状态。

本设计将推理正确性建立在显式任务契约、受策略约束的逐轮控制、结构化观察评估、能改变状态的反思和独立完成闸门之上。首个迁移对象仍为 Web Agent，但核心协议不得依赖 Web 工具或 Evidence Pack。

## Goals / Non-Goals

**Goals:**

- 将模糊用户目标编译为可跟踪、可验证的任务契约。
- 让计划、决策、观察、评估、反思和验证共享同一份版本化 Agent 状态。
- 将用户界面选项转换为后端有效策略，并应用安全与正确性下限。
- 让反思只在明确触发时运行，并保证反思产生可审计的状态变化。
- 严格区分 completed、completed_with_warnings、waiting_user、blocked 和 failed。
- 保持推理摘要可审计，不存储或依赖隐藏思维链。
- 让任务类型通过适配器提供工具和验证能力，而不是复制 Agent loop。

**Non-Goals:**

- 不在本 change 中新增 shell、文件写入、浏览器控制或消息发送工具。
- 不实现多 Agent 编排、长期后台自治或跨任务自动派活。
- 不允许用户策略关闭权限边界、基础错误处理或最终完成验证。
- 不以增加模型调用次数作为“深入推理”的唯一实现。
- 不在本 change 中实现通用形式化证明或保证模型结论绝对正确。

## Decisions

### Decision 1: TaskContract 是所有推理与验证的共同根

规划前生成并持久化 `TaskContract`：

```text
TaskContract
  goal
  deliverables[]
  constraints[]
  prohibited_actions[]
  assumptions[]
  success_criteria[]
  risk_level
  verification_requirements[]
  ambiguity_status
```

每条成功标准有稳定 ID、强制性、验证方法和当前状态。后续计划步骤、决策和完成报告必须引用这些 ID。目标存在会改变结果的歧义时，契约进入 `needs_clarification`，运行转为 waiting_user，而不是猜测。

替代方案是继续只传递 goal 字符串。它实现简单，但无法可靠判断是否完成，也无法解释为何需要某一步或为何允许最终结束。

### Decision 2: ReasoningPolicy 分为 requested 与 effective 两层

Run 创建请求携带用户偏好，`PolicyCompiler` 结合任务风险、复杂度、工具权限和系统下限生成不可变策略快照：

```json
{
  "requested": {
    "reasoning_effort": "balanced",
    "planning_strategy": "adaptive",
    "reflection_enabled": true,
    "reflection_trigger": "adaptive",
    "execution_mode": "request_approval",
    "verification_level": "standard"
  },
  "effective": {
    "reasoning_effort": "balanced",
    "planning_strategy": "adaptive",
    "reflection_enabled": true,
    "reflection_trigger": "adaptive",
    "execution_mode": "request_approval",
    "verification_level": "standard",
    "max_turns": 12,
    "max_reflections": 3,
    "max_replans": 2
  },
  "adjustments": []
}
```

推理强度同时控制计划深度、候选策略数、模型预算、反思预算和验证覆盖；规划策略控制 direct/adaptive/plan_first；反思策略控制模型驱动反思，但不关闭基础恢复、权限检查或完成闸门。高风险任务可以提升最低规划、验证或审批要求，所有提升必须记录原因。

替代方案是把 UI 值直接传入 prompt。这无法执行硬性安全下限，也无法测试策略是否真正生效。

### Decision 3: 使用版本化 AgentState 与 PlanGraph

`AgentState` 作为每轮输入的规范状态，包含 task contract、policy snapshot、plan version、criterion states、accepted facts、open questions、observations、failure fingerprints、budgets 和 terminal intent。

计划表示为带依赖关系的 `PlanGraph`，但第一版只允许 DAG，不实现任意工作流语言。direct 模式可以生成单个即时步骤；adaptive 模式维护粗粒度计划并局部修订；plan_first 在执行前生成完整计划。策略允许升级规划强度，但不得静默降低安全和验证要求。

替代方案是只依赖完整聊天上下文。其状态边界模糊，容易把陈旧事实、已失败策略和当前计划混在一起，恢复和测试也更困难。

### Decision 4: Decision、Observation 与 Evaluation 形成显式预期闭环

扩展逐轮决策，使其引用 `target_step_id`、`success_criteria_refs`、结构化 expected observation、风险、置信度和 fallback。工具结果先标准化为 Observation，再由确定性规则与可选模型评估器生成 Evaluation：matched、partial、mismatch、conflict 或 inconclusive。

```text
Decision(expected) -> Action -> Observation(actual) -> Evaluation(delta)
```

Controller 只消费规范状态和 Evaluation，不直接从原始工具输出猜测是否成功。模型不可自行声明工具已成功或标准已满足。

替代方案是让下一轮模型阅读全部原始输出并自行判断。灵活但不可重复，且很难检测“工具成功但任务方向错误”。

### Decision 5: ReflectionGate 事件驱动，ReflectionPatch 必须可应用

基础运行时恢复始终存在，包括 schema 修复、受限重试、权限拒绝、预算停止和重复动作检测。模型驱动反思由 `ReflectionGate` 根据策略和信号触发：tool_failed、expectation_mismatch、evidence_conflict、low_confidence、no_progress、dependency_broken 或 completion_gate_failed。

反思输出分 local、plan、goal 三层，并产生 `ReflectionPatch`：修订工具输入、假设/事实、步骤状态、计划版本、验证要求或终态。Patch 在 schema、权限和预算校验后原子应用；无法应用的反思记录为 rejected，不能影响状态。每个失败策略生成 fingerprint，防止同一动作换一种描述后无限重试。

替代方案是只保存自然语言反思。它适合展示，但不能保证下一轮真正改变行为。

### Decision 6: CompletionGate 独立于 Controller 和 Finalizer

Controller 只能提出 `terminal_intent`，不能直接把 Run 标记为完成。`CompletionGate` 检查：强制成功标准、未解决关键失败、所需验证、证据/工件引用、审批状态、预算终止原因和任务适配器报告。

```text
terminal_intent
  -> completed
  -> completed_with_warnings
  -> waiting_user
  -> blocked
  -> failed
  -> continue (reflect/replan)
```

Finalizer 只能根据 Gate 决定的终态生成对应类型的响应。blocked 与 waiting_user 响应必须描述缺口或所需输入，不得伪装为完成答案。

替代方案是保留循环结束后统一 finalize。它会混淆“停止执行”和“成功完成”。

### Decision 7: TaskAdapter 隔离领域工具与验证

通用 Runtime 通过 `TaskAdapter` 获取工具 manifest、Observation normalizer、默认成功标准、验证器和 final response schema。Web Adapter 继续提供 web_search、web_fetch、Evidence Pack 和来源验证；未来代码或文件任务可以注册自己的适配器，而无需复制 reasoning/reflection loop。

第一版通过显式 adapter 配置选择任务类型，不依赖模型自由加载任意工具。

替代方案是在通用循环中保留 Web 条件分支。短期改动较小，但会在加入第二类任务时继续扩大耦合。

### Decision 8: 审计记录保存摘要、引用和状态差异

每个 turn 保存 decision、observation 引用、evaluation、reflection trigger/patch、plan version、policy snapshot reference 和状态差异。只持久化面向用户与审计的简短 reasoning summary 和 diagnosis，不请求、不显示、不依赖隐藏思维链。

策略自动提升、反思拒绝、终态判定和未满足标准都生成 RunEvent，前端可展示“请求策略”和“实际策略”的差异。

### Decision 9: LoopOrchestrator 使用固定节点协议而非模型自由跳转

通用循环由运行时控制节点顺序，模型只在规定节点内输出结构化结果，不能自行跳过策略、评估或完成闸门：

```text
INIT -> COMPILE_POLICY -> BUILD_CONTRACT -> PLAN -> SELECT_ACTION
  -> POLICY_GATE -> EXECUTE -> NORMALIZE_OBSERVATION -> EVALUATE
  -> UPDATE_STATE -> REFLECTION_GATE -> COMPLETION_GATE
  -> CONTINUE / REPLAN / WAITING_USER / BLOCKED / FAILED / FINALIZE_RESPONSE
```

节点以统一 `NodeResult` 返回 `state_patch`、`events`、`next_node` 和可选错误。`next_node` 必须通过运行时转换表校验；例如 SELECT_ACTION 不能直接跳到 COMPLETED，EXECUTE 不能绕过 EVALUATE。模型输出中的终态只能作为候选意图，不能成为未经验证的状态转换。

替代方案是让模型决定完整流程和下一节点。它更灵活，但无法保证审批、观察评估与验证一定执行。

### Decision 10: 每轮采用可恢复的 checkpoint 事务边界

一次外部动作分为 prepare 与 commit 两段，避免进程重启后重复产生副作用：

```text
prepare turn
  persist decision + idempotency_key + policy result
  commit

execute external action

commit result
  persist ToolCall outcome + Observation + Evaluation + AgentState version
  commit
```

外部执行前必须持久化稳定 idempotency key。恢复时，Orchestrator 根据 turn phase 判断：尚未执行则安全继续；结果已存在则重放状态更新；执行结果未知且工具非幂等时进入 waiting_user 或 blocked，不自动重试。只读幂等工具可以按策略恢复。

`waiting_user` 保存 continuation token、暂停节点、计划/状态版本和未决问题或审批。用户响应被规范化为 Observation 后，从暂停节点的后继节点恢复，而不是从头重新规划。取消运行写入 cancelled terminal intent，并阻止任何新动作启动。

替代方案是只在一轮结束时写数据库。实现更简单，但在工具调用成功、数据库提交失败时可能重复执行外部动作。

### Decision 11: 节点失败按来源分类并有确定性出口

错误分为 model_output、policy_denied、tool_transient、tool_permanent、state_conflict、validator_failure、budget_exhausted 和 runtime_internal。每类错误由运行时映射到固定候选出口，模型反思只能在允许集合中选择修正：

| 错误类别 | 默认出口 |
|---|---|
| model_output | schema repair → bounded retry → blocked |
| policy_denied | waiting_user 或 blocked |
| tool_transient | bounded retry / local reflection |
| tool_permanent | alternative strategy / replan / blocked |
| state_conflict | reload state / reject stale patch |
| validator_failure | reflect / replan / warning or blocked |
| budget_exhausted | completion gate |
| runtime_internal | failed |

这保证错误处理不会依赖 prompt 中的偶然措辞，也能让不同 TaskAdapter 保持一致终态语义。

## Risks / Trade-offs

- [Risk] 通用状态模型过早复杂化。→ Mitigation: 第一版仅支持单 Agent、DAG 计划、显式 adapter 和有限 patch 操作，并以 Web Adapter 做兼容验收。
- [Risk] TaskContract 由模型生成时可能遗漏真实要求。→ Mitigation: 保留原始用户目标，使用 schema 和规则校验，高风险或关键歧义进入 waiting_user，最终闸门同时检查原始交付物要求。
- [Risk] 深入推理和每轮反思显著增加延迟与费用。→ Mitigation: 策略预算、事件门控、模型调用计数、无进展检测和可见的有效策略。
- [Risk] 反思错误地推翻正确状态。→ Mitigation: Patch 白名单、事实 provenance、原子应用、计划版本和拒绝审计。
- [Risk] 策略自动提升让用户觉得设置无效。→ Mitigation: 只向安全/正确性更严格方向提升，并在 UI 显示原因；非高风险任务尊重用户设置。
- [Risk] 新旧 Run 数据结构不兼容。→ Mitigation: 新字段提供默认值，API 维持旧字段读取，旧 Run 以 legacy policy/adapter 展示。
- [Risk] 外部动作与数据库提交无法形成真正的分布式事务。→ Mitigation: prepare checkpoint、稳定 idempotency key、工具幂等声明和未知结果时禁止盲目重试。
- [Trade-off] 独立 Evaluation 和 CompletionGate 增加模型外逻辑，但换来更稳定的状态语义和可测试性。

## Migration Plan

1. 新增 schema 与数据库字段/表，保持现有 Run API 的兼容默认值。
2. 实现 PolicyCompiler、TaskContract 与 AgentState 持久化，先以 shadow mode 记录但不改变现有 Web 执行结果。
3. 引入 PlanGraph、Evaluation、ReflectionGate/Patch，并让 Web Adapter 在受控 feature flag 下运行。
4. 引入 CompletionGate，分别验证 completed、warning、waiting_user、blocked 和 failed；移除循环结束后无条件成功 finalize 的路径。
5. 将前端策略选择接入创建 Run 请求，展示 effective policy 和调整原因。
6. 默认切换到通用 Runtime；保留 legacy Web loop feature flag 作为短期回滚路径。

回滚时关闭通用 Runtime feature flag，继续读取新审计字段但由现有 Web loop 执行；新增数据库结构不做破坏性回滚。

## Open Questions

- TaskAdapter 第一版由调用方显式选择，还是由受限分类器从目标中选择？推荐显式 Web 默认加受限分类器建议，避免模型获得未授权工具。
- waiting_user 的恢复是继续同一个 Run，还是创建同 Task 的新 Run 并引用暂停状态？推荐恢复同一个 Run，以保持计划版本和预算连续。
- 深入推理是否需要单独的模型配置，还是先仅通过预算与 prompt 策略实现？推荐先保持同一模型接口，并预留 per-stage model policy。
