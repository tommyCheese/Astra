## Why

Astra 当前由大模型自由选择下一动作，`PlanGraph` 主要作为模型上下文存在；数据库 `StepRecord`、`Run.plan_graph` 与 `AgentState.plan` 又分别保存不同的步骤、依赖和状态，导致计划无法可靠约束执行、恢复、重规划和完成判定。现在需要建立唯一的版本化计划事实源，让 Plan 决定哪些节点允许执行，大模型只负责当前节点内部的实现策略。

## What Changes

- 引入持久化、版本化的 Plan、PlanNode 和 PlanEdge 运行时模型，作为计划结构、节点状态、依赖关系和证据关联的唯一事实源。
- 统一初始规划输出和可执行计划 Schema，使节点直接携带稳定标识、依赖、所需能力、成功准则引用、预期观察和风险。
- 增加 DAG 校验与 PlanScheduler，只允许执行依赖已满足的 ready node；第一阶段保持单 Agent 串行调度，为后续并行执行保留边界。
- 将 Agent Loop 改为“Plan 选择节点，大模型决定节点内部动作”，并禁止模型通过任意 `target_step_id` 跳过计划依赖。
- 将工具结果、Observation、Evaluation、Evidence 和 ToolCall 关联到规范 PlanNode；工具调用成功不再自动代表节点完成。
- 将 `direct`、`adaptive`、`plan-first` 和 `plan-only` 统一到同一套 Plan 生命周期，同时保留各自的计划粒度和规划时机语义。
- 将 `replan` 接入版本化 PlanPatch：保留已完成节点和证据，校验版本、引用、预算与无环性，并记录新旧计划关系。
- 扩展 CompletionGate，使成功状态同时要求活动计划完成、TaskContract 强制准则满足且必需验证通过。
- 将现有运行时转换校验、补丁权限和无进展检测接入生产 Agent Loop，建立可恢复的节点级 checkpoint。
- **BREAKING**：`StepRecord` 不再是独立于 PlanGraph 的展示步骤；步骤 API、事件和工具调用统一使用规范 PlanNode 标识。旧 Run 通过兼容读取或迁移投影继续可查看，但不参与新计划调度。
- **BREAKING**：`AgentState` 不再内嵌可独立修改的 PlanGraph，而只保存活动计划标识、版本和当前节点引用。

## Capabilities

### New Capabilities

- `plan-execution-runtime`: 定义版本化计划事实源、DAG 校验、ready-node 调度、节点状态机、节点级 checkpoint 和计划视图投影。

### Modified Capabilities

- `general-agent-reasoning`: 将计划图从模型上下文提升为运行时强制执行边界，并使 AgentState 引用规范活动计划。
- `task-runner`: 将可审计 Step 统一为规范 PlanNode，并让 ToolCall、证据和 timeline 关联同一节点标识。
- `completion-gate`: 将活动计划的必需节点完成状态加入成功判定。
- `structured-reflection`: 将计划级反思改为有版本前置条件、受限操作和完整 DAG 校验的 PlanPatch。
- `reasoning-policy`: 明确 direct、adaptive、plan-first 和 plan-only 均使用统一计划生命周期，仅在计划生成时机和粒度上不同。
- `runtime-reasoning-policy-enforcement`: 使规划策略不仅选择初始化路径，还真实约束节点调度、重规划预算和运行时行为。

## Impact

- 后端 Schema：`backend/app/schemas/agent.py` 的 Plan、AgentState、AgentDecision、ReflectionPatch 和运行视图。
- 持久化：SQLAlchemy 模型、Alembic 迁移、`RunRepository`，以及 Plan/Node/Edge 专用 Repository 或 Service。
- 执行路径：`RunEngine`、`AgentLoop`、`runner/runtime.py`、ObservationEvaluator、ReflectionGate 和 CompletionGate。
- 模型协议：planner、controller 和 reflector 的结构化输出及 Prompt Context。
- 工具与审计：ToolCall、AgentTurn、Evidence、事件流和恢复 checkpoint 的节点关联。
- API 与前端：Run steps/plan timeline 的标识和状态来源；旧 Run 保持只读兼容。
- 测试：DAG 校验、依赖阻塞、节点完成、重规划版本、恢复、策略差异、完成门及端到端行为测试。
- 不引入新的外部服务；数据库迁移使用现有 SQLAlchemy/Alembic 技术栈。
