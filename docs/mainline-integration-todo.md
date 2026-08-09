# Astra 主链路未完全融合能力清单与 TODO

更新日期：2026-08-09

## 1. 文档目的

本文盘点 Astra 中已经存在代码、数据模型、API、UI、规格或扩展边界，但尚未完整进入默认生产主链路的能力，并为每项能力给出下一步决策与验收条件。

本文不是“把所有未来设想都实现”的路线图。每项能力首先需要在以下三种处置中选择一种：

1. **完成接入**：能力有明确用户价值，补齐执行、恢复、权限、观测和验证闭环。
2. **保留为显式实验**：隔离在实验入口，不让数据模型和分支继续侵入默认主链路。
3. **删除或降级**：价值尚未被证明，删除提前建设的控制面、兼容层或抽象。

## 2. 判定标准

只有同时满足以下条件，才视为“完全融入主链路”：

- 新建 Run 能通过稳定、公开的产品入口使用该能力；
- 快速、可信、恢复、审批、取消和 Subagent 路径具有明确且一致的语义；
- 不是只有 Schema、Repository、API、UI 占位或未来 adapter；
- 具备端到端测试、故障恢复测试和安全边界验证；
- 默认策略、灰度策略和退出策略有明确产品决定；
- 文档、OpenSpec、配置默认值与真实代码一致；
- 生产运维不依赖单进程内存状态或人工修复数据库。

状态标记：

- **部分接入**：真实主链路已经使用一部分，但仍有重要路径或安全闭环缺失。
- **控制面就绪**：管理、持久化或审计已存在，生产行为仍未启用。
- **默认隔离**：实现存在，但被默认开关、rollout cohort 或只读策略隔离。
- **仅规划**：只有路线图、OpenSpec 或 adapter 边界，不能视为当前产品能力。
- **有意 opt-in**：能力完整但基于风险默认关闭，不应误判为未完成。

## 3. 总览

| 优先级 | 能力 | 当前状态 | 主缺口 | 建议处置 |
| --- | --- | --- | --- | --- |
| P0 | Fast / Trusted / Legacy Runtime 收敛 | 部分接入 | 多套执行协议共存，主规格与实现存在冲突 | 先统一事实，再决定共享内核或明确双 Runtime 契约 |
| P0 | Durable Run 调度与跨进程恢复 | 部分接入 | 有 claim/lease/checkpoint，但 Run 仍由 API 进程内 task 启动 | 引入持久化 worker queue，或收缩“durable”承诺 |
| P0 | Root / Child Context Compaction V2 | 部分接入、默认隔离 | root/child 默认关闭，隔离、恢复和故障测试未完成 | 完成现有 OpenSpec 后再灰度启用 |
| P0 | Credential Broker | 控制面就绪 | Grant/引用存在，内建工具普遍仍使用部署级配置 | 选择一个真实外部工具完成端到端接入 |
| P0 | DataFlowState / DLP | 部分接入 | 标签和外发规则仅覆盖部分 Tool/effect | 建立来源—标签—目的地—保留策略闭环 |
| P1 | 跨 Session Memory Recall | 默认隔离 | 写入和召回基础存在，active 注入默认关闭 | 先用评估证明收益，再决定启用或收缩 |
| P1 | AutoDream Consolidation | 控制面就绪、默认隔离 | 默认不调度，默认模型调用预算为零 | 明确定位为确定性维护或真正的模型整理器 |
| P1 | Agent Evolution | 控制面就绪 | 候选可管理，但永远不可执行和不可生产晋升 | 保持离线建议，或单独提案实现受控应用；否则删除 rollout 状态 |
| P1 | 外部 Tool Provider Plugin | 默认隔离 | discovery 默认关闭，默认只接受 builtin | 选一个外部 Provider 做完整部署与恢复验证 |
| P1 | 可选择 Sandbox Backend | 仅一个实现、仅规划 | 抽象和配置存在，但只支持 Docker | 实现第二后端证明抽象，或删除选择性配置 |
| P1 | Subagent 全量 rollout 与远程执行 | 部分接入、默认只读 | 本地受治理执行可用，默认 trusted-read-only，远程 executor 只有端口 | 先验证多 Agent 收益，再扩大 cohort；不要先实现远程 |
| P1 | Composer 文件、图片与来源连接 | UI 占位 | 三个入口均禁用，没有请求和上下文协议 | 创建独立材料输入契约，或移除“即将支持”入口 |
| P1 | Web 能力规格与插件化现实对齐 | 规格漂移 | 主规格仍含多套内建 Web 能力，内建 Web Provider 已移除 | 明确由外部插件满足还是删除/重写旧规格 |
| P2 | Trusted/Parallel Graph 浏览器验收 | 功能已接入、验收未闭环 | 各剩一个手工浏览器验证任务 | 完成验证并归档变更 |
| P2 | Replay / Fork / Runtime Graph | 仅规划 | 只有恢复基础和长期路线，没有公开 Replay/Fork | 等持久化 worker 完成且出现真实需求再提案 |
| P2 | Hierarchical Adaptive Graph | 仅规划 | 无层级子 Plan、条件边和推测执行 | 不进入当前主链路，保留路线图即可 |
| P2 | Coordination Graph 高阶协作 | 部分基础、主要仅规划 | 缺少共享黑板、仲裁、Quorum、人类 Reviewer 图 | 先完成单层 Subagent 价值评估 |
| P2 | Graph Memory / Semantic Index | 仅关系型基础 | 无 embedding、向量/图索引、图遍历和反事实 Replay | 仅在 lexical baseline 不足时立项 |
| P2 | Governed Agent Hooks | 仅规划 | OpenSpec 74 项任务均未开始 | 暂不进入实现；先证明插件事件扩展的真实需求 |
| P2 | 多用户、组织和远程企业治理 | 明确未覆盖 | 当前是 loopback、单管理员模型，没有完整账号/组织授权 | 在产品转向远程多用户前保持 out of scope |

## 4. P0：先解决主链路真实性与安全闭环

### 4.1 Fast / Trusted Runtime 收敛

**现状**

- 新 standard Run 使用 `fast-v1`，trusted Run 使用 `trusted-v1`。
- 旧 standard runtime、兼容读取与回滚开关已删除。
- standard 与 trusted 已进入同一个 canonical `run_loop`；差异由冻结的 typed composition/capability 表达。
- `application/fast_agent_runtime` 与 `application/runner` 已删除；DAG 归 `planning`，Run 生命周期归 `run_management`。
- `fast-v1` 只作为既有持久化/API 身份映射到内部 `standard-v1`，不再选择独立控制器。

**TODO**

- [x] 以真实调用图列出新建、续跑、审批恢复、调度恢复和历史 Run 分别进入哪个 Runtime。
- [x] 采用“一个 Runtime + standard/trusted 两套 composition policy”，规范与生产调用链一致。
- [x] Tool/permission/sandbox/artifact/cancellation 通过同一个 mandatory action port 与 canonical observation contract。
- [x] 清空历史对话后删除旧 standard 分支、配置和投影，结束三轨运行。
- [x] 为 mode × approval × recovery × cancellation × memory × skill 建立配对行为矩阵。

**完成条件**

- 每类 Run 只有一个无歧义 owner；主规格、配置和代码一致。
- 运行时身份是必填字段，不再对缺失或旧 runtime kind 做兼容推断。

### 4.2 Durable Run 调度与跨进程恢复

**现状**

- Node、Subagent、Scheduler 和 AutoDream 已有 claim、lease、heartbeat、fencing、idempotency 和恢复模型。
- 普通 Run 启动仍依赖 API 进程内 `asyncio` task。
- 多副本、滚动重启和长任务尚没有统一持久化 worker queue。

**TODO**

- [ ] 画出 Run、NodeExecution、AgentExecution、Schedule、AutoDream 五套 ownership/lease 模型，识别可合并的原语。
- [ ] 决定目标部署：只支持单进程本地，还是支持多副本 durable execution。
- [ ] 若选择单进程，删除或降级无法兑现的分布式承诺和冗余状态。
- [ ] 若选择多副本，实现持久化 dispatch queue、原子 claim、heartbeat、过期回收、shutdown drain 和 fencing commit。
- [ ] 在模型调用、外部副作用、结果提交、审批等待和最终回答阶段加入 crash-point 测试。
- [ ] 证明滚动重启不丢 Run、不重复不可逆工具效果。

**完成条件**

- “可恢复”不再依赖原 API 进程仍存活。
- 运维文档明确支持的进程数、数据库和失败模型。

### 4.3 Root / Child Context Compaction V2

**现状**

- Conversation compaction 默认启用。
- Root execution 和 child execution compaction 默认关闭。
- `align-agent-context-compaction` 仍缺少 child 引用校验、容量终态、恢复兼容、审计脱敏、并发/崩溃测试、长期循环评估和 staged rollout。

**TODO**

- [ ] 完成 `align-agent-context-compaction` 的 7.5、7.7、9.x 和 10.x 未完成任务。
- [ ] 验证 child Evidence/Artifact ref 的身份、标签、purpose、contract 和 manifest hash。
- [ ] 为 protected prefix 超容量定义统一的 waiting/blocked/budget-limited 结果。
- [ ] 完成至少三轮重复压缩与跨 Provider 恢复测试。
- [ ] 评估 V2 相比现有 folding 的任务成功率、关键信息保留、Token、成本和延迟。
- [ ] 先 shadow，再 root canary，再 child canary；未达到门槛不得修改默认值。

**完成条件**

- standard root、trusted root、quick child、trusted child 都经过长循环与故障注入验证。
- root/child 开关启用后不存在隐私越界或不可恢复状态。

### 4.4 Credential Broker

**现状**

- Credential Grant ORM、Repository、权限视图和插件 `credential_ref` 契约存在。
- 未发现向内建工具签发短 TTL、服务/资源/动作限定 handle 的完整 Broker 执行链。
- 多数内建工具仍使用部署级配置。

**TODO**

- [ ] 明确 Credential Broker 的唯一 owner 和 API，避免“Grant 数据表”被误认为已实现 Broker。
- [ ] 选择一个真实外部读写工具作为纵向切片。
- [ ] 实现 reference 解析、短期 handle 签发、scope 衰减、过期、撤销和审计。
- [ ] 验证模型、日志、ToolCall、Subagent context 和插件 transport 均不获得长期 secret。
- [ ] 验证 child 只能获得比 parent 更窄的 credential scope。
- [ ] 若近期没有真实消费者，删除未使用的运行时抽象，只保留最小数据契约。

**完成条件**

- 至少一个生产工具完全不依赖把长期 secret 注入 `ToolExecutionContext`。

### 4.5 DataFlowState / DLP

**现状**

- 读取类 effect 可以写入 trust source 和 data label。
- Permission Engine 可以根据敏感/不可信标签和目的地加严外发。
- Subagent context 已携带 purpose 和标签约束。
- 并非所有工具都精确声明输入来源、用途、目的地和保留策略。

**TODO**

- [ ] 建立每个 Tool 的 source、label、purpose、destination、retention 覆盖表。
- [ ] 消除默认 `*` scope 对真实企业治理语义的掩盖。
- [ ] 对 workspace、artifact、memory、network、plugin 和 subagent handoff 做端到端传播测试。
- [ ] 定义数据合并、派生输出、脱敏输出和删除后的标签变化规则。
- [ ] 增加允许目的地、禁止目的地、未知目的地和重定向场景测试。
- [ ] 若当前产品保持本地单用户，评估是否将 DLP 降级为较小的敏感数据外发门，而不是维护完整企业模型。

**完成条件**

- 任一外发行为都能解释“哪些数据、来自哪里、为什么允许发到该目的地”。

## 5. P1：需要产品决策的已建设能力

### 5.1 跨 Session Memory Recall

**现状**

- Memory 写入、人工激活、namespace、版本、TTL、召回评分、shadow 审计、反馈和删除传播已存在。
- `AGENT_MEMORY_CROSS_SESSION_ENABLED=false`，默认不向新 Task 注入持久 Memory。

**TODO**

- [ ] 用固定数据集比较 no-memory、task-memory 和 cross-session 三组结果。
- [ ] 设定 namespace leakage=0、stale use、negative transfer、成功率、Token 和延迟门槛。
- [ ] 明确 user/session 身份在当前单用户产品中的真实来源，避免伪多租户 namespace。
- [ ] 决定 active recall 是核心产品能力还是设置中的实验能力。
- [ ] 若收益不足，保留显式 `remember` 和 task scope，删除跨 Session 自动注入复杂度。

### 5.2 AutoDream Consolidation

**现状**

- Job、lease、idempotency、候选、发布、generation、supersession、人工 rollback 和管理 UI 已存在。
- 调度默认关闭，默认 `max_model_calls=0`，实际定位更接近确定性维护作业。

**TODO**

- [ ] 在“确定性去重维护”和“模型驱动记忆整理”之间做明确产品选择。
- [ ] 若保持确定性，删除或隔离不必要的模型 operation、Profile 和生成管线。
- [ ] 若启用模型整理，补齐质量评估、失败隔离、成本预算、人工发布和回滚演练。
- [ ] 证明 consolidation 相比不整理能提升召回质量，而不是只减少记录数。
- [ ] 未证明收益前保持默认关闭。

### 5.3 Agent Evolution

**现状**

- Candidate、来源、不可变 evaluation、approve/reject、shadow/canary/promoted 状态和 rollback metadata 已存在。
- API 固定返回 `executable=false`、`production_promotion_enabled=false`。
- Approved candidate 不会影响 Prompt、Skill、Tool、权限、调度或模型路由。

**TODO**

- [ ] 决定它是“离线改进建议库”还是“生产策略发布系统”。
- [ ] 若只是建议库，将状态收缩为 draft/evaluated/accepted/rejected，删除虚假的 rollout/promotion 复杂度。
- [ ] 若要生产应用，为唯一一种低风险目标创建独立 OpenSpec，不得直接开放通用自修改。
- [ ] 定义 baseline、held-out、安全回归、成本、canary、自动回滚和人工 break-glass。
- [ ] 禁止 Evolution 修改权限、安全下限和 Credential policy。

### 5.4 外部 Tool Provider Plugin

**现状**

- Provider identity、digest、catalog snapshot、isolated protocol、Host backend、健康和 draining 模型已存在。
- managed/external discovery 默认关闭，rollout 默认 `builtin_only`。

**TODO**

- [ ] 选一个仓库外 Provider 做安装、配置、健康检查、调用、审批、恢复和卸载全流程验证。
- [ ] 验证已暂停 Run 的 frozen catalog 在 Provider 升级、下线和 digest drift 时的行为。
- [ ] 提供部署级 metrics adapter，而不是只保留进程内计数。
- [ ] 明确 external discovery 是否是受支持产品能力；若不是，删除运行期开关和未使用 discovery source。
- [ ] 更新用户文档，区分“插件协议存在”和“第三方插件已可用”。

### 5.5 可选择 Sandbox Backend

**现状**

- `SandboxProvider` 抽象和 `sandbox_provider` 配置存在。
- `build_sandbox_provider()` 对非 `docker` 值直接报 unsupported。
- `add-selectable-sandbox-backends` 只有 change 元数据，没有 proposal、design、spec 或 tasks。

**TODO**

- [ ] 决定第二后端的真实目标：本机 process、远程容器服务或其他隔离运行时。
- [ ] 在没有第二消费者前，不继续扩展通用 Provider 抽象。
- [ ] 若无近期需求，将配置收缩为 Docker 专用配置并删除“可选择”暗示。
- [ ] 若立项，先完成 OpenSpec，再验证 workspace、artifact、network、cancel、metrics 和 cleanup 等价性。

### 5.6 Subagent rollout 与远程执行

**现状**

- governed local Subagent、层级预算、权限衰减、join/fan-in、取消和恢复已经进入 trusted 路径。
- 默认 cohort 为 `trusted_read_only`，默认 child 只读。
- `AgentExecutor` 明确预留 future remote executor，但当前是本地实现。

**TODO**

- [ ] 对照单 Agent 基线测量成功率、Token、延迟、成本和失败率。
- [ ] 完成 write-capable child 的 effect、approval、credential 和 cancellation 演练后，再考虑扩大 rollout。
- [ ] 检查 `/subagent`、自动 delegation 和 required-subagent 三种入口是否共享同一治理语义。
- [ ] 没有跨机器执行需求前，不实现 remote executor；若无替换需求，评估收缩抽象。
- [ ] 解决 context compaction 7.5/7.7 后再扩大 child 长任务范围。

### 5.7 Composer 文件、图片和来源连接

**现状**

- Composer 已展示“上传文件”“添加图片”“连接来源”，但三个按钮都被禁用并标记“即将支持”。
- 当前 Run 创建、上下文容量估算和权限协议没有用户输入附件的完整契约。

**TODO**

- [ ] 决定最小首版只支持文件，还是同时支持图片和连接器。
- [ ] 定义上传暂存、Run 绑定、媒体类型、大小、病毒/内容检查、保留和删除策略。
- [ ] 定义附件进入模型、Workspace、Artifact 或 Evidence 的唯一语义，避免四套副本。
- [ ] 将附件 Token/图像预算纳入上下文容量计算。
- [ ] 对敏感文件、图片元数据、来源凭据和 Subagent 传播执行权限检查。
- [ ] 若近期不实现，移除禁用入口，避免 UI 长期承诺不存在的能力。

### 5.8 Web 能力规格与插件化现实对齐

**现状**

- 内建 Web Provider 和旧转换路径已经移除。
- 主规格目录仍包含 adaptive crawler、Google search、keyless fallback、web atomic retrieval、web data query 等能力规格。
- 当前通用 Agent Loop 不应依赖 Web 工具；实际 Web 能力应由已安装 Provider 提供。

**TODO**

- [ ] 逐项标记 Web 主规格由哪个当前 Provider、插件或测试满足。
- [ ] 把供应商特定规格迁到对应插件，主仓库只保留通用 evidence/grounding 契约。
- [ ] 删除没有生产 owner 的旧规格，避免“规格存在即功能存在”的误判。
- [ ] 保留无 Web Provider 时的明确 capability gap 和不编造行为测试。

## 6. P2：保持规划隔离，不提前进入核心

### 6.1 Trusted/Parallel Graph 最终验收

- [ ] 完成 Trusted Graph 的 SSE、断线恢复、版本差异、移动端、暗色、键盘和 reduced-motion 浏览器验证。
- [ ] 完成 Parallel DAG 的多运行节点、fan-in、资源等待、审批、失败、取消和重连浏览器验证。
- [ ] 验证通过后同步主规格并归档对应 OpenSpec，避免长期保持“几乎完成”。

### 6.2 Replay / Fork / Durable Runtime Graph

- [ ] 在持久化 worker queue 完成前不创建公开 Replay/Fork API。
- [ ] 区分“结果重放恢复”与“用户可见执行 Replay”，不得复用模糊名称。
- [ ] 只有出现调试、审计或分支实验的真实需求时再创建独立 OpenSpec。

### 6.3 Hierarchical Adaptive Graph

- [ ] 保持路线图状态，不把层级子 Plan、条件边、推测执行或动态拓扑加入当前 Schema。
- [ ] 先证明现有扁平 DAG 在规模、延迟或成功率上构成真实瓶颈。

### 6.4 Coordination Graph 高阶协作

- [ ] 不把当前 root/child 执行宣传为完整 Multi-Agent Coordination Graph。
- [ ] 在考虑共享黑板、仲裁、Quorum 和 Reviewer 图之前，先完成单层 Subagent 质量/成本评估。
- [ ] 未来 Handoff 必须复用现有 identity、delegation、credential 和 DataFlow 边界。

### 6.5 Graph Memory / Semantic Index

- [ ] 保持 relational memory 为事实源；未来索引只能是可删除、可重建的派生投影。
- [ ] 用评估证明 lexical baseline 无法满足需求后，再选择 embedding、vector 或 graph index。
- [ ] 不把 Graph Memory 与 Agent Evolution 自动发布绑定为一个项目。

### 6.6 Governed Agent Hooks

**现状**

- `add-governed-agent-hooks` 已有 proposal/design/spec/tasks，但 74 项实现任务均未开始。
- 设计包含 catalog、admission、outbox、lease、dead letter、外部 command/HTTP handler、UI 和兼容导入，范围接近一个新平台。

**TODO**

- [ ] 在实现前列出三个无法由 Tool Plugin、Skill 或内部事件订阅解决的真实 Hook 用例。
- [ ] 若用例不足，暂停或归档 change，避免另一套扩展运行时进入核心。
- [ ] 若继续，首版只做只读 observation Hook；admission mutation、外部 command 和 HTTP handler 分开提案。
- [ ] 不复用 Agent/tool Grant 给 Hook principal，不允许 Hook 自我批准或修改控制面。

### 6.7 多用户、组织和远程企业治理

- [ ] 维持当前 loopback、单管理员的明确产品边界。
- [ ] 在没有账号、组织、租户和身份认证前，不把 Permission/DataFlow/Credential 描述为完整企业治理。
- [ ] 若产品转向远程多用户，先设计认证、租户隔离、资源所有权和管理 API 授权，再开放远程访问。

## 7. 不应误判为未完成的 opt-in 能力

以下能力默认关闭或受配置控制，但默认关闭本身不是未完成证据：

- **Conversation retention**：危险的硬删除操作有意默认关闭；只要备份、候选扫描、事务和恢复运维已经验证，应保持 opt-in。
- **Remote API access**：在没有完整身份认证前默认只允许 loopback 是正确安全边界。
- **Chart/Sandbox execution**：依赖 Docker 和部署环境的能力可以按可用性关闭，不要求所有安装默认启用。
- **Custom Skill authoring**：可以作为管理员能力按部署关闭，只要禁用后不存在残留执行入口。
- **Parallel execution 回退到单槽**：这是安全降级路径，不是功能缺失。
- **Subagent kill switch**：紧急停用是完整治理的一部分，不代表功能未实现。

## 8. 建议执行顺序

```text
阶段 A：统一事实
  Runtime owner / 主规格 / Web 规格 / OpenSpec 状态
                         ↓
阶段 B：修复核心可靠性
  durable dispatch / compaction / credential / data flow
                         ↓
阶段 C：逐项证明价值
  memory / autodream / evolution / external plugin / subagent rollout
                         ↓
阶段 D：再决定是否扩展
  remote sandbox / replay-fork / graph memory / hooks / enterprise multi-user
```

优先原则：

1. 先解决“已经宣称支持但恢复或安全闭环不完整”的能力。
2. 再处理“已有大量控制面但默认不影响生产”的能力。
3. 最后处理只有路线图或 adapter 的能力。
4. 每完成一项，生产代码、模块、类和公共 symbol 应优先净减少；新增规模必须由被删除的旧路径或可测用户价值解释。

## 9. 维护规则

- 新增默认关闭能力时，必须同时记录启用门槛、删除条件和 owner。
- Feature flag 不能成为永久架构边界；稳定后必须删除旧路径或正式定义长期双轨。
- 只有 Schema、ORM、Repository 和 API 的能力不得在 README 中表述为“已支持”。
- OpenSpec 完成状态必须与任务复选框、主规格同步和真实浏览器验收一致。
- 每季度重新执行本清单：已完成项归档，未产生用户价值的实验应删除而不是继续叠加治理层。

## 10. 主要证据来源

- [`backend/app/common/core/config.py`](../backend/app/common/core/config.py)
- [`docs/astra-system-detailed-design.md`](astra-system-detailed-design.md)
- [`docs/agent-graph-evolution-roadmap.md`](agent-graph-evolution-roadmap.md)
- [`docs/deep-memory-autodream-evolution.md`](deep-memory-autodream-evolution.md)
- [`docs/tool-provider-plugins.md`](tool-provider-plugins.md)
- [`docs/agent-runtime.md`](agent-runtime.md)
- [`openspec/changes/align-agent-context-compaction/tasks.md`](../openspec/changes/align-agent-context-compaction/tasks.md)
- [`openspec/changes/add-parallel-dag-execution/tasks.md`](../openspec/changes/add-parallel-dag-execution/tasks.md)
- [`openspec/changes/add-trusted-execution-graph-workbench/tasks.md`](../openspec/changes/add-trusted-execution-graph-workbench/tasks.md)
- [`openspec/changes/add-governed-agent-hooks/tasks.md`](../openspec/changes/add-governed-agent-hooks/tasks.md)
- [`openspec/specs/answer-mode-selection/spec.md`](../openspec/specs/answer-mode-selection/spec.md)
- [`openspec/specs/general-agent-reasoning/spec.md`](../openspec/specs/general-agent-reasoning/spec.md)
