## Context

Astra 当前以 `TaskRecord` 表示用户会话，以 `RunRecord` 表示一次可恢复执行。可信运行由 `TaskContract`、`PlanGraph`、`PlanScheduler`、`RunCoordinator` 和 `NodeExecution` 驱动；并行 Worker 已经具备独立数据库 session、资源租约、预算预留、审批等待、heartbeat 和幂等恢复。`AgentIdentityRecord`、`AgentDelegationRecord`、`PermissionSubject.delegation_chain` 及权限衰减检查也已存在，但主循环尚不会创建或调度具有独立上下文和自主工具循环的子 Agent。

现有 DAG Worker 与子 Agent 的差异是：前者执行父计划中的一个既定节点，通常共享父 Run 的推理协议；后者接收一个结果导向的委派契约，在受限范围内自行规划、多轮调用工具、产生 checkpoint，并以结构化结果返回父级。若直接把每个 DAG 节点改名为 Agent，会丢失这个边界，也会让权限、预算、恢复和 UI 无法区分“并行步骤”和“自治子系统”。

### 业界方案调研

| 方案 | 核心模式 | 值得吸收 | 不直接照搬的原因 |
|---|---|---|---|
| [OpenAI Agents SDK orchestration](https://openai.github.io/openai-agents-python/multi_agent/) | manager 将 Agent 暴露为 tool；handoff 把当前会话控制权交给另一个 Agent | 明确区分 agents-as-tools 与 handoff；结构化输入输出；主管保留合成权 | SDK 运行循环不能替代 Astra 的持久化状态、权限引擎、Workspace 和 Completion Gate |
| [LangGraph subgraphs](https://docs.langchain.com/oss/python/langgraph/use-subgraphs) 与 [handoffs](https://docs.langchain.com/oss/python/langchain/multi-agent/handoffs) | 子图拥有独立 state/checkpoint namespace；显式控制传递哪些消息 | 子 Agent 使用独立 namespace；最小上下文映射；handoff 作为状态转换 | 共享完整消息会造成上下文膨胀和协议损坏；Astra 已有自己的图和恢复模型 |
| [AutoGen teams](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/teams.html) | round-robin、selector、swarm；team = participants + turn policy + termination | 终止条件必须是一等对象；Agent/Team state 可保存恢复 | 广播式 group chat token 成本高，责任和权限边界模糊，不适合 Astra 第一阶段默认路径 |
| [Google ADK multi-agent](https://adk.dev/agents/multi-agents/) | 层级 sub-agents、transfer，以及 sequential/parallel/loop workflow agents | 声明式层级、确定性 workflow 与 LLM 自主 Agent 分离 | Astra 不应引入第二套 workflow runtime；应在现有 DAG 上表达确定性编排 |
| [CrewAI](https://docs.crewai.com/) | role/task/crew，sequential 或 hierarchical manager process | 角色描述、任务 schema、guardrail、human-in-the-loop | prompt 驱动的角色委派不足以形成权限、幂等和恢复的硬边界 |
| [Anthropic multi-agent research](https://www.anthropic.com/engineering/multi-agent-research-system) | lead researcher 并行创建专门化 subagents，再合成和引用校验 | 广度型任务的并行独立上下文；任务说明要包含目标/格式/工具/边界；artifact-first；从小规模 eval 开始 | 成本约为普通 chat 的多倍，并非所有任务适用；同步等待和无界 fan-out 是已知瓶颈 |
| [A2A protocol](https://a2a-protocol.org/v0.3.0/specification/) | Agent Card 能力发现，Task/Message/Artifact，SSE/push 状态更新 | 内外 Agent 统一的任务、状态、Artifact 和流式事件概念 | 第一阶段子 Agent 在同一 Astra trust boundary 内；过早引入远程发现和认证会扩大范围 |

调研结论是：不存在适合直接嵌入 Astra 的“万能多 Agent 框架”。成熟方案的共同底层是独立状态、显式任务边界、结构化终止、有限上下文交换和可观测性；生产差异主要落在持久化、预算、安全与恢复。Astra 应复用自己的控制面，仅在协议边界兼容这些模式。

## Goals / Non-Goals

**Goals:**

- 支持父 Agent 在同一 Run 中创建一个或多个具有独立 Agent loop、上下文、identity、checkpoint 和预算的子 Agent。
- 对适合并行、需要专门工具/Skill 或会挤压父上下文的任务提高质量或延迟表现，并能量化收益。
- 以确定性运行时强制权限衰减、层级预算、并发背压、深度限制、取消、终止和结果验证。
- 让父 Run 在进程重启、审批等待、工具故障或部分子 Agent 失败后安全恢复，不依赖内存中的协程对象。
- 让用户理解当前有哪些子 Agent、为何创建、正在做什么、消耗多少、产生了什么，而不暴露隐藏思维链。
- 让单 Agent Run 保持默认、兼容和低开销，并能通过 feature flag 或 kill switch 立即禁用新委派。

**Non-Goals:**

- 第一阶段不实现平级自由群聊、round-robin、swarm、辩论、投票或“Agent 社会”。
- 第一阶段不把用户会话所有权 handoff 给子 Agent；需要澄清、审批和最终回答仍由父 Agent 代理。
- 第一阶段不提供远程第三方 Agent 发现、Agent marketplace 或完整 A2A server/client。
- 不允许子 Agent共享原始思维链、完整父消息历史、未筛选 Memory、长期凭据或全量 Workspace。
- 不把多 Agent 当成所有任务的默认优化，也不以增加 token 消耗本身作为成功指标。
- 不用外部多 Agent 框架替换 Astra 的 Plan、Permission、Tool、Workspace、Evidence、Completion 或 Recovery 子系统。

## Decisions

### 1. 第一阶段采用 supervisor/worker，而不是共享群聊或直接 handoff

父 Agent 是 supervisor，拥有用户请求、顶层 `TaskContract`、最终回答和总预算。子 Agent 是 worker，通过一个受治理的 `delegate_task` 语义能力创建，完成后返回父级。子 Agent 不能直接向用户发送最终消息；需要输入时返回 `waiting_parent` 和结构化问题，由父级决定用已有上下文回答、改派或让 Run 进入 `waiting_user`。

这对应 OpenAI 的 agents-as-tools/manager 模式，也符合 Anthropic 生产研究系统的 orchestrator-worker 实践。它使责任、权限、完成门和 UI 清晰。handoff 以后可以作为另一种 `control_transfer` 协议增加，而不是让同一个“spawn”同时意味着调用和会话所有权转移。

替代方案：第一版同时支持 manager、handoff、swarm。拒绝，因为三者的上下文、终止、用户交互和权限语义不同，会放大状态空间并使测试不可控。

### 2. 子 Agent execution 是一等持久化实体，不等同于 Plan node 或新 Run

新增 `AgentExecutionRecord`（命名可在实现时收敛），至少包含：

```text
id, run_id, parent_execution_id, parent_node_execution_id
identity_id, delegation_id, depth, ordinal
task_contract, context_manifest, catalog_snapshot, budget_envelope
status, phase, checkpoint, result, error
created_at, claimed_at, heartbeat_at, finished_at, version
```

顶层 Run 有一个 root execution；每个子 Agent 是同一 Run 的 descendant。子 Agent 内部可拥有自己的 plan revision 与 node executions，通过 `agent_execution_id` 形成 namespace。这样用户审批、总结果和 Task Workspace 仍归属同一 Run，同时每个 Agent 的状态可以独立恢复、取消和审计。

Plan node 可以选择执行原子工具工作，或启动一个 `delegated_agent` 工作单元；它们不能共享同一个 execution id。现有 `NodeExecution`、资源租约和工具调用表增加可空 `agent_execution_id`，旧数据映射到 root execution。

替代方案 A：每个子 Agent 创建独立 Run。拒绝，因为会割裂顶层预算、用户审批、Workspace、结果和事件流，并使父子一致性依赖跨 Run 协调。替代方案 B：仅用内存 task。拒绝，因为无法恢复和审计。

### 3. 委派是一份冻结、可验证、幂等的任务契约

父 Agent提出 `DelegationRequest`，Runtime 校验并冻结为 `DelegationContract`：

```json
{
  "request_id": "stable-idempotency-key",
  "objective": "明确的结果导向目标",
  "success_criteria": ["可验证条件"],
  "scope": {"included": [], "excluded": []},
  "inputs": [{"kind": "fact|artifact|evidence", "ref": "..."}],
  "output_schema": {"type": "object"},
  "required_capabilities": [],
  "requested_tools": [],
  "requested_skills": [],
  "resource_scope": {},
  "budget_request": {},
  "deadline_at": "...",
  "join_policy": "required|optional|first_success",
  "dedupe_key": "..."
}
```

Runtime 拒绝目标空泛、输出不可验证、范围与 sibling 高度重复、超出父预算或请求未授权能力的委派。相同父 execution 与 `request_id` 只创建一次；恢复和模型重试返回已有 child handle。

Runtime 为父 Agent暴露少量稳定操作：`delegate_task`、`inspect_delegation`、`cancel_delegation` 和 `collect_delegation_results`。这些是运行时语义能力，不作为普通第三方 Tool 绕过 Permission Engine。

替代方案：允许父 Agent传一段自然语言 prompt。拒绝，因为无法做 scope、预算、去重、权限和结果校验。

### 4. 使用最小上下文 manifest，不复制完整聊天或共享 scratchpad

每个子 Agent 的 prompt/context 由 `SubagentContextComposer` 从冻结引用组装：平台 Profile 的适用层、子角色协议、委派契约、显式 facts、Evidence/Artifact refs、衰减后的 Tool/Skill Catalog、Workspace view、预算和终止规则。默认不包含：完整会话历史、父/兄弟内部消息、隐藏 reasoning、未选择的 Memory、其他子 Agent 的 tool traces。

上下文项记录来源、摘要、hash、data label、用途和 token estimate。大对象通过 Artifact/Evidence 引用访问；父子返回仅传结构化摘要和引用，避免“传话游戏”和重复 token。子 Agent 可在自己的 namespace 内压缩上下文和保存 checkpoint，但不能把临时 scratchpad 晋升为 Run facts；只有父级合并器可把已验证结果提升到共享事实层。

替代方案：共享一个 message list。拒绝，因为产生上下文污染、并发写冲突、token 放大和潜在数据越权。

### 5. 权限、Catalog、数据和工作区逐维衰减

创建 child identity 后，Runtime 对每个维度求交，而不是仅比较一个粗粒度布尔 scope：

```text
child authority = parent effective authority
                ∩ task/run policy
                ∩ DelegationContract.resource_scope
                ∩ server-side subagent policy
```

衰减对象包括 actions/resources、Tool Catalog、Skill revisions、Credential Grants、network destinations、data labels/purposes、Workspace read/write roots、模型路由上限、预算和继续委派能力。Skill 或工具被父级看见不等于自动委派；Credential 必须按 child identity 单独签发短 TTL grant。

每次工具调用带真实 `agent_execution_id`、identity 和完整 delegation chain 进入 `authorize_invocation()`。`delegation_create` 是受控 effect；child 不能审批自己的 ask、不能作为 reviewer、不能创建超出 `max_depth` 的后代。等待审批时，父级和无冲突 sibling 可继续；审批内容展示执行 Agent 和衰减范围。

### 6. 预算形成可原子结算的树，并由 Runtime 控制 fan-out

顶层 reasoning policy 编译出 `SubagentBudgetPolicy`：

- `max_children_total`、`max_children_per_parent`、`max_parallel_children`；
- `max_depth`，第一阶段默认 1，实验环境最多 2；
- `max_tokens`、`max_model_calls`、`max_tool_calls`、`max_wall_time_ms`、`max_cost`；
- 按模型/provider/capability 的并发和速率上限；
- `minimum_parent_reserve`，防止父级把全部预算委派出去。

创建前从父 envelope 原子预留预算，完成后按真实 usage 结算并释放余额；child 只能从自己的 envelope 再预留。预算不足时 Runtime 拒绝创建并向父级返回机器可读原因。fan-out 由复杂度、独立性和收益策略上限共同控制，模型的数量建议只能收窄或在允许范围内选择。

默认启用 adaptive delegation gate：简单任务、强顺序依赖、共享写热点或估计收益低于阈值时继续单 Agent/DAG；适合宽度优先、独立检索、跨领域分析或需要上下文隔离时才开放子 Agent。系统记录未委派的原因，便于评测。

### 7. 调度使用现有 Coordinator 原语，但 Agent 槽与节点槽分层

新增 `AgentCoordinator` 管理 child execution 的 claim、heartbeat、取消和 join；每个 child 内继续使用现有 `RunCoordinator`/`PlanScheduler` 执行节点。进程内第一版使用结构化 `asyncio` 并发，但持久化 claim 和 fencing token 才是权威。

全局调度顺序：

```text
Run budget/provider limits
  -> Agent execution slots
    -> child plan node slots
      -> resource leases / tool concurrency
```

必须避免 `max_parallel_children × max_parallel_nodes` 无界乘法：AgentCoordinator 为 child 发放动态 node allowance，总活动模型调用和工具调用仍受 Run/部署级 semaphore 约束。共享 Workspace 和外部资源继续使用现有规范化 resource key；未知资源或非幂等写保持独占。

父级默认采用 barrier-free supervision：可在 child 运行时执行无依赖工作；只有读取结果的 fan-in 节点等待指定 join set。`join_policy` 支持 required、optional 和 first_success，但取消 loser 只适用于无持久副作用或已定义补偿的 child。

### 8. 子 Agent 终态与回传使用结构化结果，不信任自然语言完成声明

`SubagentResult`：

```json
{
  "status": "completed|completed_with_warnings|waiting_parent|blocked|failed|cancelled",
  "summary": "给父级的有界摘要",
  "outputs": {},
  "artifacts": [],
  "evidence_refs": [],
  "claims": [],
  "open_issues": [],
  "question": null,
  "completion": {},
  "usage": {},
  "provenance": {}
}
```

child Completion Gate 先验证自身任务契约、output schema、引用存在性、证据覆盖和权限合规。父级 Result Merger 再验证 sibling 去重/冲突、required join 完整性、Artifact/Evidence lineage 和顶层成功标准；不能因 child 返回 “done” 就完成 Run。

大输出由 child 直接写入受限 Workspace/Artifact 管线并返回 ref；父级不复制内容。多个 child 对同一结论冲突时，合并器保留 conflict set，父 Agent 可追加验证任务或在最终答案中披露不确定性。

### 9. 取消、失败、审批和恢复按委派树确定性传播

生命周期建议为：

```text
proposed -> authorizing -> queued -> running
         -> waiting_parent | waiting_approval | waiting_resource
         -> completing -> terminal
```

- 用户取消 Run：标记 root cancellation epoch，阻止新 claim，向所有 descendants 传播 cooperative cancellation，再按超时强制终止 sandbox/tool job。
- 父取消 child：只取消其 descendants；已提交的不可逆外部副作用不会回滚，必须记录结果和警告。
- child 失败：required join 阻塞依赖分支并允许父级在预算内重试/改派；optional child 产生 warning；unrelated sibling 可继续。
- child `waiting_parent`：释放模型/工具槽但保留 execution；父级响应后以版本化 continuation 恢复。
- 审批：绑定 child identity、frozen input/effect hash 和 continuation token；不得把一次 child approval 泛化为整个树。
- 进程重启：恢复器扫描 heartbeat 过期 execution；已 checkpoint/已记录工具结果的幂等阶段恢复，结果未知的非幂等调用进入 waiting/blocked，不盲目重放。

运行代码、Profile、Skill 或 Catalog snapshot 版本变化时，已启动 execution 继续使用冻结版本；不能兼容恢复时 fail closed。该规则也为未来从进程内 Worker 迁移到队列/多进程执行器保留边界。

### 10. 事件与 UI 使用统一 lineage，不展示内部思维链

所有事件带 `run_id`、`agent_execution_id`、`parent_agent_execution_id`、可空 `node_execution_id`、`sequence`、`agent_sequence` 和 `causation_id`。Run 级 sequence 保证重放顺序；高频 token/tool progress 经过有界批处理，权威快照可校正乱序或缺失事件。

顶层过程流默认显示紧凑摘要：“3 个子 Agent：2 运行、1 完成；预算 41%”。可信执行图可切换为复合视图：Agent 树作为外层，每个 Agent 节点可展开内部 Plan DAG。详情面板展示：委派目标、创建原因、状态/等待原因、允许的能力摘要、预算、工具/Artifacts、结果和错误。隐藏 reasoning、secret、原始敏感 Tool input 不进入事件或 UI。

指标至少包含：delegation rate、accept/reject reason、fan-out/depth、parallel overlap、duplicate-work rate、child success、parent merge failure、tokens/cost、wall time、quality delta、cancel latency、recovery count 和 permission denials。

### 11. 先建立受控评测，再扩大默认范围

评测分三层：

1. 确定性协议测试：契约校验、幂等创建、上下文隔离、权限衰减、预算原子性、取消/恢复、事件重放。
2. 行为 eval：是否该委派、任务分解覆盖率/重叠率、工具匹配、结果 schema、冲突处理、停止时机。
3. 端到端收益：相对同模型/预算的单 Agent baseline，比较任务成功、质量、p50/p95 延迟、token/cost 和安全事件。

第一批基准选择并行收益明确的宽度型研究、跨多数据源比较、独立文件审阅和多方案评估；共享写密集编码、强顺序流程和简单问答作为负样本。发布门要求质量提升或延迟下降达到预设阈值，同时成本和失败率不越界。LLM judge 只能补充，不替代可执行验收、证据校验和人工抽检。

### 12. 保留内部 Agent adapter 与未来 A2A 边界

`AgentExecutor` 接口仅接收 `DelegationContract`、`SubagentContextManifest` 和受限 runtime handles，输出事件与 `SubagentResult`。第一版实现 `LocalAstraAgentExecutor`；未来可增加 SDK adapter 或 remote A2A executor，但任何 adapter 都必须经过相同的 authorization、budget、result validation 和 audit wrapper。

内部对象与 A2A 概念有意对齐但不等同：DelegationContract 对齐 Task input，AgentExecution 对齐 Task lifecycle，SubagentResult/ArtifactRef 对齐 Artifact/Message，SSE 对齐 status/artifact updates。Agent Card、远程认证、push notification 和 opaque remote execution 延后处理。

替代方案：直接把 A2A 作为内部总线。拒绝，因为内部需要更强的事务、权限、checkpoint 和资源租约语义，强行用互操作协议会丢失控制面信息。

## Risks / Trade-offs

- [多 Agent 通过增加 token 获得表面质量，成本失控] → 默认单 Agent；使用 adaptive gate、层级预算、父级保留、硬 fan-out/depth/cost 上限和相对 baseline eval。
- [父级产生含糊或重复委派] → 强制结构化成功标准、scope/dedupe key、sibling overlap 检查和委派质量指标；Runtime 可拒绝或合并任务。
- [上下文过少导致 child 失败，过多导致污染和越权] → manifest 化最小上下文、显式引用、data label/purpose 检查、token 估计和可观测的 context diagnostics。
- [权限通过多级委派放大] → 每一层逐维求交、短 TTL credential、完整 identity chain、统一 `authorize_invocation()`、禁止 self-approval 和默认深度 1。
- [并发槽相乘压垮数据库、provider 或外部服务] → Agent/Node 分层配额、Run/部署级 semaphore、预算预留、resource lease 和背压指标。
- [共享 Workspace 产生竞态或 child 覆盖彼此成果] → 默认 child 私有 staging namespace；显式共享只读输入；发布到公共路径需租约、checkpoint/diff 和父级合并。
- [父级在 child 尚未终态时提前回答] → required join set、root Completion Gate 和 descendant terminal barrier。
- [取消后仍发生外部副作用] → cancellation epoch、执行前 fencing 检查、可中断 sandbox；不可逆调用在已发送后只报告未知/已发生状态，不虚假回滚。
- [重启导致重复 child 或重复工具调用] → stable request id、唯一约束、checkpoint、fencing token、工具幂等键和 result-unknown 处理。
- [大量子事件制造 UI 噪声或泄露敏感数据] → 摘要优先、折叠下钻、事件清洗/批处理、权威快照和禁止 reasoning/secret 输出。
- [模型学会为简单任务滥用委派] → 负样本 eval、创建成本提示、Runtime complexity gate、按 profile/mode 开关和 kill switch。
- [外部框架/A2A adapter 破坏 Astra 语义] → adapter 只实现受限 `AgentExecutor`，外层统一施加预算、权限、事件和结果验证；第一阶段不启用远程 adapter。

## Migration Plan

1. 增加 root `AgentExecution` 兼容层和 lineage 字段，所有现有 Run 自动表现为单 root execution；不改变现有执行行为。
2. 落地 DelegationContract、SubagentResult、层级预算、identity/catalog/context attenuation 及纯协议测试，feature flag 保持关闭。
3. 实现单 child、只读、depth=1 的 `LocalAstraAgentExecutor`；子 Agent 串行运行，验证持久化、审批、取消和重启恢复。
4. 接入 AgentCoordinator 和有界并行 child，复用资源租约与 Run/部署级配额；先对内部 eval/管理员测试开放。
5. 接入 Result Merger、root Completion Gate、SSE lineage 和可折叠 Agent 树 UI。
6. 对宽度型 trusted Run 小流量启用 adaptive gate，比较 shadow decision、单 Agent baseline 与多 Agent实际结果；达到质量/延迟/成本门槛后逐步放量。
7. 后续独立变更再评估 handoff、depth>1、远程 worker、A2A 和第三方 SDK adapter。

回滚时关闭 `AGENT_SUBAGENT_EXECUTION_ENABLED`，阻止新委派；已存在 child 由兼容 coordinator 排空或按策略取消。数据库新增字段和记录保持只读兼容，不删除 lineage；旧 root-only Run 不受影响。

## Open Questions

- 第一阶段是否允许 child 写入共享 Task Workspace，还是只允许私有 staging 后由父级显式 promote？建议默认后者。
- trusted Run 中是由 Planner 预声明可委派节点，还是允许 Agent loop 动态创建 child？建议两者都走同一契约，但首发只开放 Planner 标记过的节点。
- child 的默认模型是否必须与父级相同，还是可由 ModelResolver 在冻结上限内选择更便宜模型？建议允许受策略约束的模型降档，并把选择写入 snapshot。
- `waiting_parent` 的问题最多允许往返几次？建议首发 1 次，超过后由父级改派或进入 blocked，避免隐式群聊。
- 用户是否可在 UI 单独取消一个 optional child？建议支持，但不允许绕过 required join 和父级 Completion Gate。
