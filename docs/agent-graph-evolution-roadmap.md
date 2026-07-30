# Astra Agent Graph 完整演进路线

## 1. 文档定位

本文定义 Astra 从“可信模式使用 Plan DAG”演进到“可恢复、可组合、可协作、可学习的 Agent Graph Runtime”的长期架构路线。

这是一份方向性架构文档，不代表所有阶段已经实现，也不把后续阶段纳入当前开发范围。每个阶段都应通过独立 OpenSpec 提案完成设计、风险评估和交付拆分。

当前变更 [`add-trusted-execution-graph-workbench`](../openspec/changes/add-trusted-execution-graph-workbench/proposal.md) **只聚焦阶段一**：把可信模式后台已有的规范 Plan DAG 升级为可查看、可确认、可修订和可审计的前台图谱工作台。

## 2. 总体目标

Astra 的 Graph 演进不是把所有运行数据塞进一张图，而是逐步建立边界清晰、可以组合的图模型：

```mermaid
flowchart LR
    P1["阶段一：可信执行图谱"] --> P2["阶段二：可恢复 Runtime Graph"]
    P2 --> P3["阶段三：层级与自适应 Graph"]
    P3 --> P4["阶段四：Multi-Agent Coordination Graph"]
    P4 --> P5["阶段五：Graph Memory 与持续优化"]
```

最终目标包括：

- 用户可以理解 Agent 计划、当前路径、阻塞原因和证据来源；
- 系统可以从持久化检查点安全恢复，并对历史执行进行确定性回放和受控分叉；
- Runtime 可以展开层级子图，并根据依赖、风险、预算和反馈动态调度；
- 多个 Agent 可以在明确身份、能力和授权边界内协作；
- 系统可以从跨 Run 经验中检索和复用有效模式，并通过评估闭环持续改进。

## 3. 长期图模型

Astra 应保持以下图层相互关联但语义独立。

### 3.1 Plan Graph

回答“应该执行什么”。

- 每个版本是经过完整校验的不可变 DAG；
- 节点表示计划承诺，边表示显式依赖；
- 修订、反思和失败恢复通过新版本表达；
- 使用 Plan、PlanNode、PlanEdge、version 和 lineage 建立稳定身份。

### 3.2 Runtime State Graph

回答“控制器接下来可以进入什么状态”。

- 允许暂停、恢复、条件路由、重试、反思和审批循环；
- 不要求是 DAG；
- 必须具备状态版本、幂等边界和持久化检查点；
- 不把 Runtime 循环伪装成 Plan 依赖。

### 3.3 Execution Trace Graph

回答“实际发生了什么”。

- 关联 AgentTurn、ToolCall、Approval、Evaluation、Reflection 和 Artifact；
- 以追加式事件和稳定执行引用为事实来源；
- 同时支持节点局部 Trace 和跨节点时间线；
- 不展示隐藏思维链或未经清洗的敏感输入。

### 3.4 Evidence and Provenance Graph

回答“结果为什么可信”。

- 关联成功准则、Observation、Artifact、来源、验证结果和失败；
- 保留数据来源、工具、执行环境、版本和校验链路；
- 支持从答案回溯到证据，也支持从证据定位受影响结论。

### 3.5 Coordination Graph

回答“谁负责什么，以及如何交接”。

- 表达 Agent 身份、角色、任务所有权、Handoff 和 Delegation；
- 权限始终满足子级不超过父级、任务策略和显式委托范围；
- 协作关系不能绕过 Tool Permission、Approval 或 Credential Broker。

### 3.6 Memory Graph

回答“哪些跨 Run 经验值得复用”。

- 区分事实、情节、策略、失败模式和评估反馈；
- 每条记忆带 provenance、confidence、scope、TTL 和访问策略；
- 检索结果只是建议上下文，不能直接成为未经验证的执行事实；
- 支持遗忘、冲突检测、撤销和质量衰减。

## 4. 不可破坏的架构原则

后续阶段不得破坏以下边界：

1. **Plan 与 Runtime 分离**：Plan 保持不可变版本 DAG，运行时循环属于状态图。
2. **图不是隐藏思维链**：只展示公开计划、安全摘要、执行事实和证据。
3. **事件不是任意补丁**：结构变化通过类型化领域命令和完整校验产生。
4. **恢复不等于重复执行**：所有外部行动必须受幂等键、Effect Plan 和结果未知策略保护。
5. **Agent 不拥有授权权力**：模型可以提出行动，但不能自行扩大权限或批准提权。
6. **历史不可被当前状态覆盖**：版本、事件、证据和审计记录保持可追溯。
7. **Standard 模式保持轻量**：快速响应不为统一视觉效果创建虚假 DAG。
8. **能力按阶段进入**：尚未满足正确性、安全性和可观测性门槛时，不提前引入更高阶动态性。

## 5. 阶段一：Trusted Execution Graph Workbench

### 5.1 目标

把可信模式已经存在的规范 Plan DAG 从后台内部结构升级为主要的用户过程视图。

### 5.2 核心能力

- 确定性分层 DAG 布局；
- 并行、汇合、当前路径、ready、blocked 和终态展示；
- Plan Graph、Node Trace、Evidence 三层信息结构；
- Plan 版本、lineage 和相邻版本差异；
- SSE 图快照、领域增量事件和断流恢复；
- 等待确认时通过自然语言生成完整新 Plan 版本；
- 节点检查器、全屏工作台、键盘操作和等价结构化列表；
- standard/trusted 图谱边界。

### 5.3 明确不包含

- 任意 checkpoint replay 或 fork；
- 运行时自由展开子图；
- 关键路径预测和高级并行调度；
- Multi-Agent 网络；
- 跨 Run Graph Memory；
- 拖拽、连线或前端局部 Patch 改写规范计划。

### 5.4 完成门槛

- 前后端以显式节点 ID 和边作为图谱事实来源；
- 计划确认、执行、等待、失败、重规划和完成共享同一图谱组件；
- 历史版本和实时版本不会相互污染；
- 图事件可重放、可恢复且不泄露敏感信息；
- standard 模式不承担图形库启动成本，也不产生占位图；
- 桌面、移动、暗色、高对比度、键盘和 reduced-motion 验证通过。

当前阶段的详细范围、设计和任务以现有 OpenSpec 为准：

- [提案](../openspec/changes/add-trusted-execution-graph-workbench/proposal.md)
- [设计](../openspec/changes/add-trusted-execution-graph-workbench/design.md)
- [任务](../openspec/changes/add-trusted-execution-graph-workbench/tasks.md)

## 6. 阶段二：Durable Runtime Graph

### 6.1 目标

把当前依赖进程生命周期的 Agent Loop 升级为可以跨进程、跨部署暂停和恢复的持久化 Runtime Graph。

### 6.2 候选能力

- 持久化 worker queue、claim、lease、heartbeat 和失联回收；
- Runtime 状态节点、合法转换和转换原因的类型化协议；
- 模型调用前、外部行动前、行动提交后和等待用户时的检查点；
- 基于事件和检查点的确定性状态重建；
- 只读 Replay，用于调试和审计；
- 从安全检查点创建显式 Fork，不覆盖原 Run；
- 人工介入、超时、取消和恢复的统一 Gate；
- 外部副作用的 exactly-once intent 与 at-least-once delivery 防护；
- Runtime Graph 和 Workspace Checkpoint 的一致性关联。

### 6.3 安全边界

- Replay 默认不重新调用模型或工具；
- Fork 必须获得新的 Run 身份、预算和权限上下文；
- 结果未知的非幂等行动不得自动重放；
- 检查点不得保存明文凭据、隐藏推理或未经清洗的工具秘密。

### 6.4 进入条件

- 阶段一图协议和 Plan 版本语义稳定；
- RunEvent 顺序、幂等键和状态版本已有完整测试；
- worker claim 和外部副作用恢复模型完成威胁分析。

### 6.5 完成门槛

- 服务滚动重启不会丢失可恢复 Run；
- 同一个检查点的重复恢复不会重复产生外部副作用；
- Replay 可以稳定重建公开状态和审计视图；
- Fork 与原 Run 的身份、权限、事件和产物完全隔离。

## 7. 阶段三：Hierarchical Adaptive Graph

### 7.1 目标

让复杂任务可以使用层级子图、条件路由和受预算约束的动态调度，而不牺牲可验证性。

### 7.2 候选能力

- PlanNode 引用经过校验的子 Plan；
- 子图折叠、展开和局部 Trace；
- 显式条件边、Gate 和受控分支选择；
- 运行时根据观察生成新子图或新 Plan 版本；
- fan-out/fan-in 并发调度和资源上限；
- 关键路径、等待时间和阻塞传播分析；
- 延迟、成本、风险和验证覆盖率联合预算；
- 可取消的推测执行，仅用于无副作用或可安全回滚的候选；
- 调度决策解释和替代策略记录。

### 7.3 设计约束

- 动态展开仍然产生可校验、可版本化的规范子图；
- 条件路由基于结构化 Observation，不解析隐藏思维链；
- 并行度由 Runtime 和资源策略控制，不由模型无限扩张；
- 有副作用节点默认不参与推测执行。

### 7.4 进入条件

- 阶段二具备可靠 checkpoint、恢复和取消语义；
- 现有 Plan 规模、延迟或复杂度数据证明 Dagre/扁平 DAG 已成为限制；
- 已建立成本、延迟、风险和验证覆盖率基线。

### 7.5 完成门槛

- 层级图可以确定性展开、折叠和恢复；
- 并行执行不会突破预算、权限或资源限制；
- 动态路由和重规划都能解释其输入事实和版本变化；
- 对复杂任务的延迟或成功率改善通过离线与线上评估验证。

## 8. 阶段四：Multi-Agent Coordination Graph

### 8.1 目标

在明确身份、职责、权限和交接协议下，让多个专门 Agent 协作完成单 Agent 难以可靠处理的任务。

### 8.2 候选能力

- Agent 角色、能力、模型、预算和信任等级；
- Manager、Specialist、Reviewer 等受控拓扑；
- 子任务委派、Handoff、Join 和结果退回；
- 共享黑板或消息通道中的结构化上下文；
- 所有权、等待关系和跨 Agent 依赖图；
- 冲突检测、仲裁、Quorum 和独立验证；
- Agent 级成本、延迟、成功率和权限审计；
- 人类 Reviewer 作为 Coordination Graph 中的独立身份。

### 8.3 安全边界

```text
child scope ⊆ parent scope ∩ task policy ∩ explicit delegated scope
```

- 子 Agent 不能审批自身或父 Agent 的提权；
- Handoff 不转移长期凭据，只转移受限任务上下文和短期授权；
- 共享上下文按数据标签、来源和目的地策略过滤；
- 多 Agent 共识不能覆盖平台安全策略。

### 8.4 进入条件

- 阶段三已经具备稳定的子图和任务所有权语义；
- Identity、Delegation、Credential Broker 和 DataFlowState 完成端到端接入；
- 单 Agent 基线证明多 Agent 带来的质量收益高于协调成本。

### 8.5 完成门槛

- 每项工作和副作用都能归属到明确 Agent 身份；
- 委派链、权限收窄和结果来源可完整审计；
- Agent 失败或超时可以局部恢复，不必重启整个任务；
- 多 Agent 方案通过质量、成本和延迟对照评估。

## 9. 阶段五：Graph Memory and Continuous Optimization

### 9.1 目标

让 Astra 在不削弱隐私、来源审计和任务隔离的前提下，从历史执行图中检索有效经验并持续改进。

### 9.2 候选能力

- Run 内情节记忆、跨 Run 策略记忆和失败模式图；
- 事实、策略、偏好、案例和评估反馈的类型化节点；
- provenance、confidence、scope、TTL、版本和撤销；
- 结合任务结构、能力和风险的图检索；
- 成功子图、验证路径和工具组合的候选复用；
- 冲突事实检测和时间有效性管理；
- 离线 Graph Eval、回归数据集和反事实 Replay；
- 调度、规划和模型选择策略的受控优化；
- Shadow、Canary 和自动回滚机制。

### 9.3 安全边界

- 用户、Workspace 和组织数据默认隔离；
- 共享记忆必须具备明确授权和可解释来源；
- 被召回的经验必须重新经过当前能力、权限、预算和验证策略；
- 删除、过期或撤销的数据不得继续通过派生索引出现；
- 自动优化不能直接修改平台安全下限。

### 9.4 进入条件

- 前四个阶段能产生稳定、类型化且质量可评估的图数据；
- 已建立数据保留、删除、共享和训练用途政策；
- Eval 可以区分检索收益、模型收益和调度收益。

### 9.5 完成门槛

- 记忆召回对成功率或成本有可重复的正向影响；
- 每条影响执行的记忆都能解释来源和适用范围；
- 冲突、过期、删除和权限变化可以及时传播；
- 自动优化经过离线评估和渐进发布，不直接污染生产策略。

### 9.6 已实现的关系型基础（2026-07）

`add-deep-memory-agent-evolution` 已提前交付阶段五中不依赖 Graph Runtime 的安全底座：

- 显式 Run/Task/Workspace/user 命名空间、类型化 Memory、时态生命周期和不可变版本；
- 来源链接、删除传播、确定性跨 Session 召回、shadow 审计和有界 feedback；
- 不要求 embedding 的 lexical/kind/tag/recency/confidence/importance/utility baseline；
- 默认关闭、带数据库 lease/idempotency 的 AutoDream consolidation，以及人工 publish/rollback；
- 不可执行的 evolution candidate、冻结离线 evaluation、人工 approve/reject 和 promotion deny；
- 固定 no-memory、legacy、cross-session、consolidation 对照夹具与 leakage/stale/harm 指标。

这不是 Graph Memory 完成声明。当前数据库仍是关系模型，没有 Memory 节点/边投影、图遍历、embedding/vector index、反事实 Graph Replay 或自动 Shadow/Canary promotion。未来实现必须复用已经落地的 namespace、source、lifecycle、evaluation 和 deletion contract；向量或图索引只能是可删除的派生投影，不能成为新的授权或事实来源。

## 10. 跨阶段基础设施

以下能力不是独立阶段，但必须贯穿整条路线。

### 10.1 类型与版本

- 所有公开图快照、领域事件、命令和差异协议带 schema version；
- 后端 Schema、OpenAPI、前端类型和测试夹具保持同步；
- 不使用标题、显示顺序或布局坐标替代稳定身份。

### 10.2 可观测性与评估

至少持续采集：

- 计划生成、修订和验证失败率；
- 节点等待、执行和阻塞时长；
- 关键路径与非关键路径耗时；
- 模型调用、工具调用和验证成本；
- checkpoint 恢复、Replay 和 Fork 正确率；
- Handoff 次数、Agent 协调开销和冲突率；
- Memory 命中率、采用率、质量提升和错误传播率。

### 10.3 安全与治理

- 图事件和快照统一经过安全清洗；
- 权限判断继续基于 Effect Plan 和不可绕过的 Policy Engine；
- 凭据只通过短 TTL、资源和动作限定的 Broker 句柄进入执行环境；
- 共享、导出、Replay、Fork 和 Memory 检索都必须执行数据范围检查。

### 10.4 前端信息架构

未来能力应在现有三层模型上渐进扩展：

```text
Plan Graph
  └─ Runtime / Coordination overlay
       └─ Node Trace
            └─ Evidence and Provenance
```

不应把所有 Runtime 事件、Agent 消息和 Memory 节点默认铺在主图中。主视图保持可扫描，复杂关系通过检查器、过滤器和专门工作台按需展开。

## 11. 阶段推进规则

每个后续阶段开始前必须：

1. 用生产或代表性测试数据证明当前阶段存在真实限制；
2. 建立现状质量、延迟、成本和安全基线；
3. 创建独立 OpenSpec，明确新增领域模型和迁移策略；
4. 完成威胁分析、失败恢复和权限边界设计；
5. 定义可以自动验证的完成门槛；
6. 保留不依赖新能力的降级路径；
7. 在进入下一阶段前完成协议、文档和测试收敛。

阶段编号表达依赖顺序，不承诺具体发布日期。若业务需求只需要阶段一或阶段二，Astra 不应为了追逐 Graph 形式而提前引入多 Agent 或跨 Run Memory 的复杂度。

## 12. 当前决策

- 阶段一 Trusted Execution Graph 已继续演进；每项后续能力仍使用独立 OpenSpec 管理。
- 阶段五的关系型 Deep Memory 与治理基础已由 `add-deep-memory-agent-evolution` 实现，但 active cross-session recall、AutoDream 调度和 production evolution promotion 默认关闭。
- 阶段二至阶段四以及真正的 Graph/semantic indexing 继续按各自进入条件推进，不因关系型 Memory 基础而视为完成。
- 在离线指标证明收益前，不启用自动发布或执行 approved candidate；安全下限、来源与删除传播不参与优化。
