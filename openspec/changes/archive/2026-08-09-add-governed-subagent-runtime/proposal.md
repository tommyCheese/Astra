## Why

Astra 已经具备可恢复 Run、可信 DAG、并行节点、权限衰减模型和执行图，但一个 Run 仍只有一个真正拥有独立推理循环与上下文的 Agent；复杂任务只能把工作拆成短生命周期节点，无法通过专门化子 Agent 获得上下文隔离、并行探索和长程自治。现在需要把已有并行与治理基础升级为受控的父子 Agent 运行时，同时避免用共享聊天、提示词约定或无界递归制造成本、安全和恢复风险。

## What Changes

- 引入一等 `SubagentExecution`：父 Agent 通过结构化委派契约创建子 Agent，明确目标、成功标准、输入、输出 schema、能力、工具、Skill、数据、工作区、预算、截止时间和取消策略。
- 以 supervisor/worker（主管—工作者）作为第一阶段默认语义：父 Agent 保持用户会话和最终答案所有权，子 Agent 作为可并行调用的受治理工作单元返回结构化结果或 Artifact 引用；handoff、平级群聊、投票/辩论和跨部署 Agent 互操作不作为第一阶段默认路径。
- 为每个子 Agent 建立独立 identity、Agent loop、上下文窗口、checkpoint、状态机和事件流；只向子 Agent传递任务所需的最小上下文，不复制父 Agent 的完整消息、隐式记忆或内部推理记录。
- 复用 Astra 的 Plan/NodeExecution、并发槽、资源租约、Permission Engine、Tool/Skill Catalog snapshot、Workspace、Evidence Ledger、Completion Gate 和 Run recovery，并新增父子执行谱系、委派深度、fan-out/fan-in 与结果合并语义。
- 强制权限和资源衰减：子 Agent 的权限、工具、Skill、凭据、数据、网络、工作区范围和预算必须是父级有效范围与显式 delegated scope 的交集；子 Agent 不得审批自身提权或扩大后代权限。
- 增加分层预算与背压：限制每 Run 子 Agent 数、并发数、嵌套深度、token、模型调用、工具调用、墙钟时间和成本；预留、结算、取消、超时和失败必须沿委派树传播且不重复记账。
- 定义结构化 `SubagentResult` 和 artifact-first 回传：区分 completed、completed_with_warnings、waiting_parent、blocked、failed、cancelled，携带摘要、交付物、证据、缺口、使用量和可验证完成声明；父 Agent 不能直接信任未通过 schema、证据和完成门校验的子 Agent 声明。
- 扩展 SSE、运行历史和可信执行图，展示父子树、并行子 Agent、当前阶段、等待原因、预算、权限范围、Artifacts、失败与取消传播，并允许在不暴露隐藏推理的前提下下钻审计。
- 建立面向委派质量的评测与灰度开关：覆盖是否该委派、分解覆盖率、重复工作、结果质量、成本/延迟增益、权限衰减、恢复一致性和失控 fan-out；默认仅对显式允许的 trusted Run 与只读/低风险能力开放。
- 为未来接入 OpenAI Agents SDK 风格 agents-as-tools/handoff、LangGraph 子图以及 A2A 的 Task/Message/Artifact 边界保留适配层，但第一阶段不引入第三方多 Agent 框架作为 Astra 控制面依赖。

## Capabilities

### New Capabilities

- `governed-subagent-runtime`: 定义子 Agent 的委派契约、独立身份与上下文、生命周期、层级预算、权限衰减、取消/恢复和结构化结果。
- `subagent-orchestration`: 定义 supervisor/worker 的选择、并行 fan-out/fan-in、递归深度、任务去重、等待父级、结果合并及失败传播。
- `subagent-observability`: 定义父子执行谱系、事件、指标、审计、评测，以及在可信执行图和运行历史中的分层呈现。

### Modified Capabilities

- `task-runner`: 允许一个 Run 持久化和恢复多个独立 Agent execution，并协调父子状态、checkpoint、并发槽与终态。
- `general-agent-reasoning`: 增加何时委派、如何生成最小完备任务契约、如何验证和综合子 Agent 结果的确定性协议。
- `completion-gate`: 在父 Run 完成前校验所有必需子 Agent 终态、结果 schema、证据、Artifacts 和未解决缺口。
- `policy-driven-tool-runtime`: 将实际 Agent identity、delegation chain 和衰减后的 Catalog/权限范围带入每次子 Agent 工具调用。
- `agent-chat-ui`: 增加子 Agent 树、状态、预算、权限、Artifacts 和可审计下钻，同时保持主会话由父 Agent 所有。
- `low-latency-answer-streaming`: 支持多子 Agent 并发事件的有序聚合、背压、重放、快照校正和紧凑进度摘要。

## Impact

- 后端：Run Coordinator、Agent Loop、Plan Scheduler、Node Worker、Permission/Delegation Repository、Catalog Resolver、Context Composer、Completion Gate、Usage Metering、Recovery 和 SSE projection。
- 数据与 API：新增子 Agent execution、委派任务/结果、checkpoint、层级预算与 lineage 记录；扩展 Run/graph/result/event schema，保留现有单 Agent Run 的兼容路径。
- 前端：可信执行图和过程流增加可折叠 Agent 树、子 Agent 详情、等待/失败/取消传播、预算与交付物入口。
- 安全与运维：新增 fan-out/depth/cost 限制、子 Agent feature flag、kill switch、审计指标和恢复扫描；第一阶段继续使用进程内结构化并发，持久化状态不依赖协程存活。
- 测试：委派决策、上下文隔离、权限/Skill/Tool/凭据衰减、并发资源冲突、预算原子性、审批、取消、超时、重启恢复、事件乱序、结果验证和端到端收益评测。
