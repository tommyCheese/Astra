## 1. 规范模型与数据库迁移

- [x] 1.1 在 `schemas/agent.py` 中定义统一的 PlanDraft、PlanNodeDraft、PlanView、PlanNodeView、PlanPatch 操作和节点状态枚举，并移除新路径对不一致 `PlanOutput` 字段映射的依赖
- [x] 1.2 在 SQLAlchemy 中新增 PlanRecord、PlanNodeRecord 和 PlanEdgeRecord，包含版本、前序计划、节点运行元数据、证据引用及必要唯一约束和索引
- [x] 1.3 增加 Alembic 迁移创建 Plan/Node/Edge 表，并为 ToolCall、AgentTurn、Observation/Evaluation 持久化路径增加规范 `plan_node_id` 关联或兼容外键
- [x] 1.4 调整 AgentState Schema，只保存 active_plan_id、active_plan_version 和 active_node_id，并保留旧 AgentState JSON 的只读解析兼容
- [x] 1.5 添加数据库模型和迁移测试，验证同一 Run 的计划版本唯一、节点键唯一、Edge 端点归属同一 Plan 及删除保护

## 2. Plan Repository、校验与视图投影

- [x] 2.1 实现 PlanRepository 的创建、活动版本读取、节点状态更新、Edge 读取和计划版本切换接口
- [x] 2.2 实现 PlanValidator，校验节点键、引用存在性、自依赖、环、根节点、可达性、成功准则、能力声明、计划深度和节点预算
- [x] 2.3 实现 PlanService，将 direct 本地计划、adaptive 粗粒度计划、plan-first 模型 PlanDraft 和 plan-only 正式计划统一落入规范模型
- [x] 2.4 实现从规范 Plan 生成 PlanGraph/Run steps/API View 和模型 Context 的单向投影，禁止这些投影反向覆盖规范状态
- [x] 2.5 实现旧 Run 兼容读取适配器，使没有 PlanRecord 的历史 Run 继续显示旧 steps、tool calls 和结果但不能由新调度器恢复
- [x] 2.6 添加 PlanValidator、PlanService、视图投影和旧 Run 兼容测试，包括天气查询类单链计划、分支计划和非法循环计划

## 3. PlanScheduler 与节点状态机

- [x] 3.1 实现 PlanScheduler.ready_nodes，只有全部 hard dependency completed 的 pending 节点可执行
- [x] 3.2 实现确定性的单 Agent 节点选择，并在一个事务中完成 pending→running、AgentState.active_node_id 和审计事件更新
- [x] 3.3 实现受控节点状态转换和非法转换拒绝，覆盖 completed、failed、blocked、skipped 及 dependency_broken 传播
- [x] 3.4 把 `runner/runtime.py` 的转换校验、补丁权限和错误出口接入生产 RunEngine/AgentLoop 节点循环
- [x] 3.5 添加依赖阻塞、多个 ready node 稳定选择、失败依赖、非法状态转换和并发状态版本冲突测试

## 4. Agent Loop 活动节点绑定

- [x] 4.1 重构 ContextAssembler，从规范活动 Plan 读取计划摘要并向模型提供唯一 active_node、节点观察、成功准则和 eligible tools
- [x] 4.2 更新 controller prompt 和 AgentDecision 校验，使模型决策只能针对运行时活动节点；兼容 target_step_id 时要求其严格等于 active_node.id
- [x] 4.3 删除 `_step_for_tool()` 基于标题、意图或 capability 的字符串匹配，并要求所有 ToolCall 和 AgentTurn 显式关联 active plan node
- [x] 4.4 将 `finalize` 从普通节点内部自由决策改为节点完成后由 CompletionGate 允许的任务级动作，并保留 direct 单节点快速路径
- [x] 4.5 删除 Run 结束时批量完成所有 pending/running Step 的逻辑，确保未执行节点保持真实状态
- [x] 4.6 添加模型选择非 ready 节点、缺失活动节点、工具节点关联、直接回答和多节点工具任务的 Agent Loop 行为测试

## 5. Observation、Evaluation 与节点完成

- [x] 5.1 为 Observation、Evaluation、Evidence/Artifact 引用增加 plan_node_id，并确保 timeline 可以从节点重建行动与结果顺序
- [x] 5.2 重构工具成功路径，使 ToolCall succeeded 只产生 Observation，不直接完成 PlanNode
- [x] 5.3 扩展 ObservationEvaluator，依据节点 expected_outcome、required_fields 和 success_criteria_refs 生成 matched、partial、mismatch、conflict 或 inconclusive
- [x] 5.4 实现 complete_node 服务，只有 matched 且无阻塞验证时才原子写入节点 completed、证据引用、准则变化和 AgentState 版本
- [x] 5.5 将 partial 保持在活动节点循环，将 mismatch/conflict 路由到反思或重规划，将不可恢复失败写入结构化节点 failure
- [x] 5.6 添加工具成功但语义失败、缺字段、部分结果、证据冲突、验证通过后释放后继节点的测试

## 6. 版本化重规划与无进展处理

- [x] 6.1 实现 PlanPatch 应用器及 AddNode、UpdatePendingNode、AddDependency、RemoveDependency、SkipPendingNode 和 BlockNode 操作
- [x] 6.2 实现 expected_plan_version 校验、已完成节点和证据保护、运行中节点保护、完整 DAG 复验及原子新版本激活
- [x] 6.3 为新旧计划节点记录 lineage 和 supersedes_plan_id，并生成 `plan.patch_applied`、`plan.patch_rejected` 审计事件
- [x] 6.4 将 AgentDecision.replan 直接路由到计划级反思/planner，成功后重新调度，失败时产生澄清或阻塞结果，而不是只增加计数
- [x] 6.5 将 NoProgressDetector 接入证据增益、准则变化、完成节点和计划版本信号，并受 reflection/replan 预算约束
- [x] 6.6 添加过期 Patch、环形 Patch、改写已完成节点、有效局部分支替换、replan 预算耗尽和无进展检测测试

## 7. CompletionGate、恢复与策略语义

- [x] 7.1 扩展 CompletionGate，同时检查活动计划必需节点、未解决依赖失败、TaskContract 强制准则、验证、审批和等待状态
- [x] 7.2 实现计划未完成时的 continue/replan 分支，并确保 pending/running/failed/blocked 必需节点不能得到成功终态
- [x] 7.3 为外部行动实现 prepared、executing、result_recorded、committed checkpoint，并将活动节点和稳定幂等键持久化
- [x] 7.4 实现恢复协调：重放已记录结果、重试安全幂等行动、对结果未知的非幂等行动进入 waiting_user/blocked
- [x] 7.5 验证 direct、adaptive、plan-first 和 plan-only 全部使用统一计划生命周期，同时保持规划调用时机、粒度和预算差异
- [x] 7.6 添加完成门计划检查、任务暂停恢复、进程中断恢复、plan-only 后续激活和四种策略端到端测试

## 8. API、事件流、前端与收尾验证

- [x] 8.1 更新 Run API 和响应 Schema，从规范 PlanNode 投影 steps，并提供 plan_id、plan_version、node_key、依赖、状态和证据字段
- [x] 8.2 更新 SSE 事件为规范计划和节点生命周期事件，保留旧 `step.*` 历史事件读取兼容并验证断线重连
- [x] 8.3 更新前端计划/timeline 展示，使用规范节点标识呈现 pending、running、completed、failed、blocked、skipped 和计划版本变化
- [x] 8.4 增加端到端天气查询场景：解析地点与日期、调用天气能力、验证结果、生成建议，并证明后继节点在依赖完成前不可执行
- [x] 8.5 运行后端、前端和 OpenSpec 校验，修复回归并确认旧 Run 只读、新 Run 调度、重规划、恢复和 CompletionGate 证据完整
- [x] 8.6 更新代码链接架构文档，明确 Plan 是执行事实源、模型负责节点内部动作，以及 `runner/runtime.py` 已成为生产控制边界
