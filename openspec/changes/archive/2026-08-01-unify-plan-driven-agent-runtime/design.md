## Context

Astra 当前在 `RunEngine` 中生成 `PlanOutput`，再分别投影为数据库 `StepRecord` 和 `AgentState.plan` 中的 `PlanGraph`。`StepRecord` 使用 UUID 并承载 ToolCall 关联，`PlanGraphStep` 使用 `step-N` 标识并承载依赖和预期结果，两者没有稳定映射；创建 Step 时依赖被写为空，Agent Loop 也不调用 `ready_steps()`，而是让模型根据包含 Plan 的上下文自由选择工具或结束。工具调用成功会先完成 Step，PlanGraph 节点状态却不更新，CompletionGate 也不检查计划完整性。

现有 `runner/runtime.py` 已提供运行时节点转换、补丁权限、错误出口和无进展检测的基础结构，但尚未成为生产 Agent Loop 的顶层控制器。本设计需要在不引入外部服务、保留单进程执行方式和旧 Run 可查看性的前提下，统一计划事实源并逐步接管执行调度。

## Goals / Non-Goals

**Goals:**

- 建立一个持久化、版本化且可审计的 Plan DAG 唯一事实源。
- 让依赖和节点状态真实约束 Agent Loop，而不是只作为模型上下文。
- 让模型继续负责当前节点内部的工具选择、参数生成、解释、反思和结果生成。
- 统一 Plan、Step、ToolCall、Observation、Evaluation、Evidence 和 timeline 的节点标识。
- 使节点完成由预期结果和证据评估驱动，而不是由工具进程是否成功驱动。
- 使 replan 产生经过版本、权限、预算和 DAG 校验的持久化新计划版本。
- 使恢复、完成判定和审计能够重建真实执行顺序。
- 保持 adaptive、plan-first 和 plan-only 的用户语义，并兼容读取历史 direct Run。

**Non-Goals:**

- 本变更不实现多 Agent 编排。
- 第一阶段不并发执行多个 ready node；调度器返回确定性的单个活动节点。
- 不引入通用工作流语言、条件表达式、循环边或任意用户脚本。
- 不改变 ToolRegistry、Sandbox 或权限系统的基础安全边界。
- 不要求迁移旧 Run 成为可恢复的新计划；旧 Run 只需保持可读和可审计。
- 不暴露模型隐藏思维链。

## Decisions

### 1. 关系型 Plan DAG 是唯一执行事实源

新增 `PlanRecord`、`PlanNodeRecord` 和 `PlanEdgeRecord`。Plan 保存 Run、版本、策略、状态和前序版本；Node 保存稳定 `node_key`、内容、运行状态、能力、成功准则、预期结果、风险和证据；Edge 保存同一 Plan 内的前驱与后继。

`RunRecord.plan_graph` 不再作为可独立更新的事实源，迁移期可保留为只读兼容快照；新 Run 的计划读取统一通过 PlanRepository 构造 `PlanGraph` DTO。`StepRecord` 的对外语义并入 PlanNode，ToolCall 直接关联规范节点。

选择关系型模型而不是继续以 `AgentState.plan` JSON 为主，是因为节点状态、ToolCall 外键、恢复 checkpoint、依赖查询和版本审计都需要稳定引用。继续维护 JSON 主状态和 Step 投影会永久保留双写一致性风险。

### 2. AgentState 引用计划，不复制计划

`AgentState` 保存 `active_plan_id`、`active_plan_version` 和 `active_node_id`，不再保存一份可独立修改的完整 PlanGraph。模型 Context 中的 PlanGraph 每轮从 PlanRepository 读取并序列化，因此计划结构和节点状态始终来自同一来源。

状态版本与计划版本分别递增：状态补丁使用 `expected_state_version`，计划补丁使用 `expected_plan_version`。一次同时修改两者的事务必须校验两个前置版本。

### 3. Planner 直接输出统一 PlanDraft

用 `PlanDraft`/`PlanNodeDraft` 替换字段不一致的 `PlanOutput` 转换链。节点草稿直接包含 `node_key`、`depends_on`、`required_capabilities`、`success_criteria_refs`、`expected_outcome` 和风险；运行时字段由 PlanService 初始化。

PlanValidator 在落库前校验：节点键唯一、依赖存在、无自依赖、无环、根节点存在、所有节点可达、成功准则引用有效、能力声明合法，以及节点数量和深度不超过生效策略预算。

### 4. PlanScheduler 是节点选择的唯一入口

PlanScheduler 根据活动计划快照计算 ready nodes：节点必须为 `pending`，且全部 hard dependency 为 `completed`。第一阶段按照稳定 index 选择一个 ready node，并在事务内将其设为 `running` 及写入 `AgentState.active_node_id`。

如果依赖失败或阻塞，调度器产生 `dependency_broken`，交由反思/重规划/阻塞策略处理。模型不能选择另一个非 ready 节点；兼容保留的 `target_step_id` 必须等于活动节点，否则决策被拒绝。

### 5. Agent Loop 只处理活动节点内部动作

模型上下文包含任务契约、计划摘要、活动节点、该节点相关观察、可用工具和预算。允许的节点内部决策包括 `call_tool`、`complete_node`、`reflect`、`replan`、`ask_user` 和 `blocked`。任务级 `finalize` 只在没有未完成必需节点且 CompletionGate 允许后执行。

工具不再通过标题或 capability 字符串猜测 Step。ToolCall、AgentTurn、Observation、Evaluation 和 Artifact/Evidence 均显式携带 `plan_node_id`。

### 6. Evaluation 驱动节点完成

工具执行成功只生成 Observation；ObservationEvaluator 根据活动节点 `expected_outcome` 和 `success_criteria_refs` 生成 Evaluation。只有 `matched` 且不存在节点级阻塞验证时，`complete_node` 才能把节点设为 `completed` 并持久化证据引用。`partial` 保持节点运行，`mismatch`、`conflict` 或不可恢复失败进入反思、重规划或节点失败路径。

删除 Run 结束时批量把所有 pending/running Step 标记完成的行为。

### 7. Replan 使用受限 PlanPatch 和不可变版本

模型不得直接覆盖整个活动图。计划级反思产生带 `expected_plan_version` 的 PlanPatch，操作限定为增加节点、修改未开始节点、增加/删除未满足依赖、跳过未开始节点或阻塞节点。

PlanService 在内存副本上应用 Patch，禁止删除或改写已完成节点及其证据，禁止静默替换运行中节点，然后重新执行完整 PlanValidator。成功后创建新 Plan 版本，记录 `supersedes_plan_id` 和节点 lineage，并原子切换 Run/AgentState 的活动计划引用；失败则记录 `plan.patch_rejected`。

### 8. CompletionGate 同时检查计划、契约和验证

成功要求：活动计划全部必需节点均为 `completed` 或策略允许的 `skipped`，无 `running` 节点、无未解决依赖失败，TaskContract 强制成功准则满足，必需验证通过，且不存在等待用户或必需审批。模型的结束意图只触发评估，不能直接完成 Run。

如果计划未完成但仍存在 ready node，CompletionGate 返回 continue；如果可通过重规划恢复，返回 replan；只有无安全可行路径时才返回 blocked/failed。

### 9. 生产循环采用现有运行时状态机边界

复用并扩展 `runner/runtime.py` 的 `TRANSITIONS`、`PATCH_AUTHORITIES`、`ERROR_EXITS`、`LoopOrchestrator` 和 `NoProgressDetector`。运行时阶段管理 `select_plan_node → select_action → policy_gate → execute → normalize_observation → evaluate → update_state → reflection_gate → completion_gate`；计划节点状态机独立管理 `pending → running → completed|failed|blocked|skipped`。

每次外部行动前持久化已验证决策、活动节点、phase 和幂等键；行动返回后先记录 ToolCall 结果，再在事务中应用 Observation、Evaluation、节点和 AgentState 更新。恢复逻辑复用已记录结果，避免重复执行幂等行动；非幂等结果不确定时进入 waiting_user 或 blocked。

### 10. 所有规划策略共享同一生命周期

- `adaptive`：创建少量粗粒度节点，允许在观察后通过 PlanPatch 扩展或修改未完成部分，并使用推理强度提供的有界重规划预算。
- `plan-first`：首次外部行动前由模型生成并持久化完整 DAG。
- `plan-only`：生成并持久化状态为 `planned` 的正式 Plan，但不激活节点执行；后续批准可以激活同一版本。

策略只改变规划时机、粒度和可用预算，不改变 DAG 校验、节点状态、策略门和 CompletionGate 的强制边界。

`direct` 不再是新请求可选策略。Schema 和执行器保留 legacy 枚举值，仅用于解析历史 Run 的不可变策略快照；偏好存储中的旧值迁移为 `adaptive`，前端、偏好 API 和新建 Run API 均不再接受或产生 `direct`。

### 11. 兼容视图和事件由规范节点投影

Run API 暂时继续返回 `steps`，但数据由 PlanNode 投影并包含 `plan_id`、`plan_version` 和 `node_key`。新 timeline 事件统一使用 `plan.node.*` 和规范节点 ID；旧 `step.*` 事件仅用于读取历史 Run，不再由新执行路径产生。

## Risks / Trade-offs

- [Risk] 关系型 Plan、Node、Edge 增加查询和迁移复杂度。→ Mitigation：使用 eager load 一次读取活动图，按 Run 缓存只读快照，并把节点状态更新限制在短事务内。
- [Risk] 一次性替换 Agent Loop 容易造成行为回归。→ Mitigation：按事实源、调度、节点验证、重规划、恢复五个阶段切换，并为每阶段增加行为测试和兼容读取。
- [Risk] 模型生成的 DAG 经常无效。→ Mitigation：PlanValidator 拒绝非法图，并允许受限的规范化修复；仍无效时退化为符合策略的单节点安全计划，而不是执行未经校验的图。
- [Risk] 计划约束过强会削弱通用 Agent 灵活性。→ Mitigation：Plan 只决定节点边界和依赖，节点内部仍允许模型自由选择合规工具、局部反思和提出 PlanPatch。
- [Risk] 新旧 Run 的步骤语义不同。→ Mitigation：API 提供统一只读 View，并明确旧 Run 不可用新调度器恢复；迁移不改写历史审计数据。
- [Risk] 重规划复制计划版本会增加存储量。→ Mitigation：预算严格限制 replan 次数，节点 lineage 复用证据引用，运行结束后可按保留策略压缩只读快照。
- [Risk] ready node 并行语义提前复杂化。→ Mitigation：第一阶段固定串行选择，Edge 和 Scheduler API 保留未来返回多个 ready nodes 的能力。

## Migration Plan

1. 新增 Plan/Node/Edge 表、状态枚举约束和索引；不删除旧字段。
2. 实现 PlanDraft、PlanValidator、PlanRepository、PlanService 和只读 PlanGraph/View 投影。
3. 新 Run 双写兼容 `steps` 视图但只允许 PlanService 修改规范节点；旧 Step 写路径停止用于新 Run。
4. 接入 PlanScheduler 和活动节点绑定，删除 `_step_for_tool()` 字符串匹配。
5. 接入节点级 Observation/Evaluation/Evidence 和 CompletionGate 计划检查。
6. 接入 PlanPatch、版本化 replan、运行时状态机和 checkpoint 恢复。
7. 切换 API/SSE/前端到规范节点 View，保留旧 Run 只读适配器。
8. 稳定后移除新路径对 `Run.plan_graph` 和旧 Step 写入的依赖；旧列的物理删除另行提案。

回滚时保留新增表和数据，只将新 Run 创建入口切回旧执行路径；由于迁移阶段不删除旧列，旧版本仍可读取 Run。已经由新运行时执行的 Run 不回退到旧 Loop 继续执行，而是暂停或由新版本恢复，避免两种状态机交叉写入。

## Open Questions

- 是否在本变更中把 API 字段 `step_id` 同步重命名为 `plan_node_id`，还是先保留兼容别名并在后续版本删除；设计默认采用兼容别名。
- `skipped` 是否可以满足 hard dependency；设计默认只有显式标记为可跳过的节点才能在 CompletionGate 中视为满足，普通 hard dependency 仍要求 `completed`。
