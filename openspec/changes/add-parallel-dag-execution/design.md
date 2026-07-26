## Context

当前 `PlanScheduler.ready_nodes()` 已能返回多个满足依赖的 pending 节点，但 `select_next()` 只认领排序后的第一个节点，并把 Run 的 `AgentState.active_node_id` 设置为该节点。Agent Loop 也围绕一个 `active_node` 顺序生成决策、执行工具和提交状态。因此 DAG 具有并行结构语义，运行时却是单通道串行执行。

现有系统同时具备不可变 Plan 版本、PlanNode 状态机、持久化 RunEvent、工具权限与审批、任务工作区、预算、取消、重规划和 CompletionGate。并行化必须保留这些安全与审计边界，并让断线、进程重启和部分分支失败后的状态仍可确定恢复。

## Goals / Non-Goals

**Goals:**

- 在单个 trusted Run 内真实并发执行无依赖且无资源冲突的 ready 节点。
- 用明确上限、背压和资源租约约束并发，而不是对全部 ready 节点无限 `create_task`。
- 隔离节点上下文、工具调用、观察、评估和证据，同时安全合并 Run 级事实与预算。
- 保持 Plan DAG、权限、审批、沙箱、幂等、取消、重规划和 CompletionGate 的现有语义。
- 让图谱准确显示多个活动节点、并行路径、等待资源、审批暂停和 fan-in 屏障。
- 通过持久化执行记录、领域事件和快照实现确定恢复与审计。

**Non-Goals:**

- 不在第一版引入分布式任务队列、跨主机 worker 或跨 Run 的全局公平调度器。
- 不并行执行 standard/快速响应模式内部的模型行动轮次。
- 不允许模型自行决定并发度、绕过依赖或声明未经验证的资源互斥关系。
- 不把运行时 worker、重试或审批节点写回规范 Plan DAG。
- 不保证所有 ready 节点并行；有冲突、预算压力或策略限制时允许确定性串行化。

## Decisions

### 1. 使用 RunCoordinator 与节点 Worker 分层

每个 trusted Run 由一个 `RunCoordinator` 持有调度权。Coordinator 在事务中读取权威 Plan、计算 ready 集合、应用并发与资源策略、原子认领节点，并为每个成功认领的节点启动独立 `NodeWorker`。Worker 只执行一个 PlanNode 的受控 Agent Loop，不能认领后继节点或直接结束 Run。

选择单 Coordinator 而不是多个 worker 竞争扫描 Plan，可以集中处理预算、取消、重规划和公平性，减少数据库竞态。Worker 使用独立数据库 session；不得在并发协程间共享当前 SQLAlchemy session。

### 2. PlanNode 与 NodeExecution 分层持久化

PlanNode 继续保存规范状态 `pending/running/completed/failed/blocked/skipped/superseded`。新增持久化 `NodeExecution`（或等价记录）保存：

- `id`, `run_id`, `plan_id`, `plan_version`, `plan_node_id`
- `attempt`, `dispatch_batch_id`, `worker_id`
- `phase`: claimed/running/waiting_approval/committing/terminal
- `status`: active/succeeded/failed/cancelled/unknown
- `state_version`, `started_at`, `heartbeat_at`, `finished_at`
- 预算预留、资源租约、工具调用和恢复引用

Run 的 `AgentState.active_node_id` 迁移为 `active_executions` 摘要集合；旧字段只用于读取迁移，不再作为并发事实来源。PlanNode 从 running 转为终态时必须由对应的当前 attempt 进行带版本校验的提交。

### 3. 批量认领是原子的、确定的且有界

Coordinator 按依赖 rank、node index 和稳定 ID 排序候选节点，再依次应用：

1. Run 并发上限；
2. 能力或 provider 并发上限；
3. 预算预留；
4. 资源冲突；
5. 取消、审批和重规划屏障。

第一版默认 `max_parallel_nodes = 3`，可由服务端安全上限和可信策略取较小值。批量认领在同一事务内执行 pending→running、创建 NodeExecution、预留预算和资源租约，并发 Coordinator 或恢复扫描不能重复认领同一节点。

不采用一次性并行全部 ready 节点，因为这会造成工具配额、模型限流和事件风暴，也无法为高风险行动提供背压。

### 4. 节点上下文隔离，Run 级状态由 Coordinator 合并

每个 Worker 从认领时的不可变快照构建上下文：

- TaskContract 与当前 Plan 版本；
- 本节点计划字段与依赖节点已接受证据；
- 节点本地 turns、observations、tool calls 和 retry 状态；
- 只读的 Run 级已接受事实与策略快照。

Worker 只能提交节点作用域的追加记录和一个类型化 `NodeExecutionResult`。Coordinator 通过版本检查合并 Run 级成功准则、事实、预算和证据。冲突事实不得按“最后写入者获胜”静默覆盖，而要产生 conflict Evaluation。

### 5. 资源声明与副作用决定是否可并行

权限分析产生的 effect plan 同时作为并发冲突输入。每个执行申请规范化资源键和模式：

- `read(resource)` 可与其他 read 并行；
- `write(resource)` 与同一或祖先/子路径资源的 read/write 冲突；
- 未知资源、非幂等外部写和声明为 exclusive 的 provider 默认独占；
- 不同资源上的已授权写可以在策略允许时并行。

资源租约带 execution ID、版本和过期时间，终态或取消时释放。无法获得租约的节点保持 pending，并通过调度元数据公开 `waiting_reason=resource_conflict`，不伪装为 running。

### 6. 审批暂停分支，而不是无条件暂停整个 Run

节点需要审批时，其 NodeExecution 进入 `waiting_approval` 并释放普通并发槽，但保留必要的审批/资源语义。Coordinator 可以继续调度与该行动无依赖、无冲突的安全分支。

只有当不存在 active 或可调度节点且至少一个必要分支等待用户时，Run 才进入 `waiting_user`。批准或拒绝必须绑定 execution ID、attempt、冻结输入和状态版本，避免旧审批恢复错误的重试。

### 7. 失败、取消和重规划采用分支作用域

- 节点失败后，仅其必要后继传播 blocked；无依赖关系的已运行或 ready 分支可以继续。
- 用户取消 Run 时，Coordinator 先停止新认领，再向所有 active Worker 传播取消；已产生的副作用按现有补偿与审计规则处理。
- 超时按节点 attempt 处理；只有满足幂等和重试策略才自动创建新 attempt。
- 重规划进入 `draining_for_replan`：停止认领新节点，等待可安全完成的 Worker 提交，并取消可安全取消的 Worker；所有活动 attempt 达到可判定终态后才创建新 Plan 版本。

不允许在旧版本 Worker 仍可能提交时直接激活新版本。

### 8. fan-in 与完成门使用持久化屏障

fan-in 节点只有在全部必要前置 PlanNode 为 completed（或规范允许的 skipped）后才能被认领。并行分支完成顺序不影响资格判断。

CompletionGate 只在以下条件同时满足时运行：

- 没有 active、claimed、committing 或结果未知的 NodeExecution；
- 没有仍可能推进强制成功条件的 pending/ready 节点；
- 所有必要 fan-in 和验证节点达到允许终态；
- 预算、审批、证据和验证状态已完成原子合并。

最终答案由一次独立综合阶段读取已接受的分支结果生成，NodeWorker 的临时候选答案不得直接流式成为最终答案。

### 9. 事件按 Run 排序，节点增量允许批量提交

所有并行 Worker 事件写入同一 RunEvent 序列，事件包含 `node_execution_id`、attempt、batch 和 Plan 版本。关键事件包括：

- `plan.nodes.claimed`
- `plan.node.execution_started`
- `plan.node.waiting_resource`
- `plan.node.waiting_approval`
- `plan.node.execution_completed/failed/cancelled`
- `plan.join.ready`
- `plan.parallelism.changed`

事件分配顺序不代表真实同时发生的先后关系；时间重叠由 started/finished 时间和 execution ID 表达。前端按动画帧合并多个节点 delta，发现版本或 attempt 缺口时获取权威快照。

### 10. 图谱以多活动态和屏障语义呈现并行

图谱允许多个节点同时显示 running，并通过活动节点计数、并发槽位摘要和分支高亮表达并行。等待资源的 pending 节点显示明确等待原因；等待审批的活动节点使用独立徽标；fan-in 节点显示“已满足 N/M 个必要依赖”。

边的活动状态由两端节点与执行关系推导，不使用高速流动动画模拟并行。reduced-motion 下完全静态，屏幕阅读器通过 live region 合并播报“3 个节点正在并行”等摘要，避免逐事件刷屏。

### 11. 第一版使用进程内并发，但恢复合同不依赖协程存活

Coordinator 可使用结构化 `asyncio` 并发运行 Worker，但持久化 NodeExecution、租约、幂等键和 heartbeat 才是事实来源。进程重启后恢复器扫描非终态 execution：

- 已记录工具结果的 attempt 从提交阶段恢复；
- 可安全重试的幂等行动继续；
- 外部结果未知的非幂等行动进入 waiting_user 或 blocked；
- 过期租约在 fencing token 校验后回收。

这样后续替换为分布式队列时无需改变 Plan 和前端协议。

## Risks / Trade-offs

- [并发写导致工作区或外部系统冲突] → 使用 effect-plan 资源键、读写租约和未知资源默认独占。
- [共享 SQLAlchemy session 引发事务错误] → Coordinator 与每个 Worker 使用独立 session，所有共享状态通过版本化提交协调。
- [并发预算超额] → 认领阶段原子预留，结束时结算并释放，拒绝无预留执行。
- [审批使整个任务看似停住] → 审批仅暂停对应 execution；无冲突分支继续，Run 仅在无其他进展时进入 waiting_user。
- [事件乱序造成 UI 回退] → 使用 Run 级序列、Plan/attempt guards 和快照校正。
- [失败分支继续执行造成浪费] → 立即阻塞其必要后继，但允许无关分支完成；策略可配置 fail-fast。
- [模型上下文互相污染] → Worker 使用节点快照和节点作用域记录，Run 级事实由 Coordinator 显式合并。
- [进程内任务丢失] → NodeExecution、heartbeat、租约和幂等恢复，不把协程对象视为权威状态。
- [并行动画造成视觉噪声] → 使用稳定边框、徽标和汇总计数，持续动画可关闭且不是唯一状态编码。

## Migration Plan

1. 增加 NodeExecution、资源租约、预算预留和活动执行投影；保留旧 `active_node_id` 的只读迁移。
2. 引入 Coordinator 的单槽模式，使新执行模型在 `max_parallel_nodes=1` 时与当前行为等价。
3. 完成原子批量认领、独立 session Worker、节点结果合并及恢复测试。
4. 对只读、无冲突工具逐步启用 2–3 个并发槽；副作用工具继续默认独占。
5. 接入审批、取消、超时、失败传播、重规划 drain 和 CompletionGate 屏障。
6. 发布图谱快照/事件扩展和多活动节点 UI，再开启默认并行。
7. 通过功能开关将单 Run 并发度降回 1 作为回滚；已持久化 NodeExecution 仍可由串行 Coordinator 排空。

## Open Questions

- 第一版是否允许不同目标文件的工作区写并行，还是所有 workspace write 统一独占？
- provider 并发限制是仅按 Run，还是同时遵守进程级 provider 配额？
- 对已等待审批的 execution 是否保留资源写租约，需根据工具 effect 的可变性进一步细化。
