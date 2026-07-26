## Context

Astra 已经为 trusted Run 持久化 `PlanRecord`、`PlanNodeRecord` 和 `PlanEdgeRecord`，通过 `PlanView` 将当前 Plan 投影到 `RunView.plan_graph`，并产生 `plan.created`、`plan.node.updated`、`reasoning.state_initialized` 和 `reasoning.state_updated` 等事件。前端已有 SSE、低频快照恢复、实时 ProcessStream reducer、等待计划确认卡和终态审计视图。

当前问题主要是投影和交互边界：`PlanConfirmationCard` 使用 `<ol>` 顺序渲染节点，`ReasoningAuditSummary` 继续线性排列 PlanNode，而 `ProcessPanel` 将推理、工具和验证按时间顺序混合展示。用户看到的是执行日志，却看不到后台实际调度的依赖结构。前端 `plan_graph` 类型还窄于后台 `PlanNodeView`，且 RunView 只暴露当前图，无法可靠比较历史版本。

本变更涉及图谱数据合同、SSE reducer、版本化修订、聊天信息架构、新前端依赖和可访问性，必须保持现有权限、Plan 校验、隐藏思维链保护和 standard/trusted 模式边界。

## Goals / Non-Goals

**Goals:**

- 让规范 Plan DAG 成为 trusted Run 从确认到完成的主要可视过程。
- 在一个稳定组件中表达并行、汇合、当前路径、状态、阻塞和总体进度。
- 将计划结构、节点执行 Trace 和证据分层，保持可扫描性和审计完整性。
- 通过类型化快照与增量事件实时更新图谱，并可在断流后确定性恢复。
- 支持查看版本沿袭和结构差异，并允许用户在确认前通过自然语言请求一个新版本。
- 提供桌面、移动、键盘、屏幕阅读器、暗色和 reduced-motion 的等价体验。

**Non-Goals:**

- 不构建通用低代码工作流设计器。
- 不允许拖拽位置、任意连线或前端局部补丁直接改变规范 Plan。
- 不将 runtime 反思循环编码为 Plan 中的环；每个 Plan 版本仍是 DAG。
- 不在本变更中实现任意 checkpoint replay/fork、跨 Run Graph Memory 或多 Agent 网络编辑。
- 不展示供应商隐藏推理、未经清洗的工具输入或内部路径。

## Decisions

### 1. 使用三层模型，而不是把所有信息放入一张图

界面分为：

1. `Plan Graph`：规范节点、依赖、状态、版本和当前路径。
2. `Node Trace`：选中节点关联的 AgentTurn、ToolCall、Reflection、Evaluation 和审批。
3. `Evidence`：Artifact、成功准则、验证结果和失败信息。

跨节点的完整线性 ProcessTimeline 继续作为次级“运行记录”。这样图谱表达承诺结构，Trace 表达真实时间顺序，避免工具调用和推理轮次把主图膨胀成不可读的执行网。

考虑过将每个 AgentTurn 和 ToolCall 都作为主图节点，但这会混淆计划节点与运行事件，并使一次重试改变用户对计划结构的理解，因此不采用。

### 2. 规范计划保持不可变版本 DAG，动态循环由 Runtime State Graph 管理

Plan vN 创建后不原地修改。反思、失败恢复或用户修订产生完整校验后的 Plan vN+1，并通过 `supersedes_plan_id` 和节点 lineage 关联。反思循环、审批恢复和行动重试仍属于 Agent Loop 状态机，不作为 DAG 环显示。

这种分层保留了 DAG 的可验证性和可解释性，同时允许运行时动态行为。采用可循环的单一状态图会使用户难以区分“计划结构”和“执行控制”，因此不采用。

### 3. 建立统一 `TrustedExecutionGraph` 生命周期组件

同一组件接收规范 `PlanGraphSnapshot` 和运行关联数据，并通过模式属性展示：

- `planning`：受控占位，不制造节点。
- `confirming`：完整 DAG、版本与确认/修订操作。
- `executing`：实时状态和当前路径。
- `waiting_user`：在对应节点或 Gate 上展示等待原因。
- `terminal`：保留终态图谱和证据。
- `historical`：只读旧版本，不覆盖当前状态。

聊天内使用紧凑概览；展开后使用较宽工作台。Plan 确认卡和终态审计不再维护各自的节点渲染逻辑。

### 4. 采用确定性分层布局与受控图形交互

前端采用 `@xyflow/react` 处理视口、节点选择、键盘焦点和边渲染，采用 `@dagrejs/dagre` 根据依赖生成稳定的 top-to-bottom 分层布局。布局输入仅包含节点尺寸、稳定排序和依赖；布局坐标不写入数据库，也不构成执行语义。

选择 Dagre 是因为当前规范图规模预期有界，且需要轻量、同步、确定性的层级布局。ELK 对复杂端口和大规模复合图更强，但包体、异步布局和配置复杂度更高；自制 SVG 会重复实现平移缩放、选择和边路由，因此第一版不采用。若实际 Plan 规模或子图需求超出 Dagre，再以相同布局接口替换。

节点默认按依赖 rank、`index`、`node_key` 稳定排序。图谱提供 fit view、缩放、平移、聚焦当前节点和全屏；不开放节点拖动。移动端默认显示可滚动概览和等价列表，避免要求精细画布操作。

### 5. 定义独立且带 Schema 版本的图谱投影

前后端共享概念合同：

```text
PlanGraphSnapshot
├─ schema_version
├─ plan: id, run_id, version, status, supersedes_plan_id
├─ nodes[]
│  ├─ stable identity and lineage
│  ├─ plan fields
│  ├─ status and timestamps
│  └─ evidence/failure references
└─ edges[]
   ├─ predecessor_node_id
   ├─ successor_node_id
   └─ dependency_type
```

`RunView` 返回当前快照和轻量版本摘要。历史完整版本通过按需 Run-scoped Plan API 获取，避免每次 RunView 都携带所有历史图。现有 `depends_on` 可在迁移期继续返回，但前端图谱以稳定节点 ID 的显式 edges 为准。

PlanNode 的 AgentTurn、ToolCall 和 Artifact 继续通过已有 `plan_node_id` 关联；Evaluation 和审批投影补齐同一稳定引用。Run 级验证不强行绑定最后节点。

### 6. 使用领域事件 reducer，而不是通用 JSON Patch 直接改规范状态

保留现有 Run SSE，新增或完善：

- `plan.graph.snapshot`
- `plan.version.created`
- `plan.version.activated`
- `plan.node.updated`
- `plan.revision.started`
- `plan.revision.completed`
- `plan.revision.rejected`

每个增量携带 event ID、Run ID、Plan ID、Plan version 和必要的旧/新值。前端 `PlanGraphStreamState` 按动画帧合并事件，并拒绝错误版本或未知节点的增量；发现缺口时合并触发一次权威快照请求。

选择领域事件而不是直接把任意 RFC 6902 Patch 施加到规范图，是为了限制可变字段、便于审计并阻止错误路径修改 Plan 结构。快照仍是最终事实来源。

### 7. `ready` 是可推导展示状态，不新增持久化节点状态

当 pending 节点的全部必要前置节点为 completed 或允许的 skipped 时，前端和后端共享的投影规则将其标记为 `ready`。数据库继续保存规范状态集合，避免 ready 因并发更新成为需要协调的第二事实来源。

若某前置节点 failed 或 blocked，则依赖传播规则显示受影响节点和边；权威 blocked 状态仍由调度器持久化。

### 8. 用户修订复用版本绑定 continuation 协议

等待 `plan_confirmation` 时，用户可以提交：

```text
kind = plan_revision
request
continuation_token
expected_plan_id
expected_plan_version
expected_state_version
```

服务端一次性消费当前 token，在不执行外部行动的情况下生成和校验完整新 Plan，创建 vN+1，并再次进入带新 token 的 `plan_confirmation`。失败时保留原计划并签发可继续使用的等待状态；过期请求不产生记录变化。

不提供前端 edge/node patch API。自然语言修订让模型负责语义拆解，但 PlanValidator、能力、预算、安全和无环校验仍由系统拥有。

### 9. 版本差异依赖 lineage 和结构字段，不使用标题启发式

服务端或共享纯函数根据相邻版本生成：

- added
- removed
- unchanged
- modified
- inherited-completed
- edge-added
- edge-removed

节点匹配优先使用 `lineage_node_id`，同一版本内使用稳定 node ID。标题相同不代表同一节点。历史版本可查看但始终带 `superseded` 标记；实时事件只更新当前版本。

### 10. 图谱状态不能仅靠颜色和动画表达

节点同时使用文字、图标、边框和线型；边有方向箭头和依赖语义。工作台维护 roving tabindex 或等价键盘导航，并提供同步的结构化列表。屏幕阅读器列表按层级朗读节点标题、状态、依赖和版本。

`prefers-reduced-motion` 下关闭节点脉冲、边流动等持续动画。图谱全屏是增强视图，不是访问确认操作或节点详情的唯一方式。

### 11. 图谱只展示公开计划和安全 Trace

快照与事件复用现有安全摘要合同。节点检查器可以展示 `reasoning_summary`、结构化决策、清洗后工具结果和授权可见 Artifact，但不展示 provider reasoning、凭据、原始敏感输入或宿主路径。

版本差异和用户修订记录也需清洗；修订提示作为用户输入审计，但不得被复制到未经授权的共享视图。

### 12. 图谱使用对话级独立悬浮窗格

trusted Run 的当前图谱在聊天表面只挂载一次，默认停靠在对话输出右侧的独立悬浮窗格，而不再嵌入 Plan 确认卡或“思考/过程”折叠区。这样收起过程记录、滚动回答或等待后续节点时，规范计划仍保持可见；线性 ProcessTimeline 则只承担跨节点的实际运行审计。

宽屏采用不遮挡对话的右侧双栏布局，窄屏降级为可收起的覆盖窗格。紧凑窗格展示图谱概览和显式的放大、缩小、适应视图、全屏控件；节点检查器在全屏工作台中展开。计划确认、调整和取消仍留在确认卡中，避免把执行授权与图谱视口操作混为一谈。

同一会话出现后续 trusted Run 时，右侧窗格切换到最新 Run 的权威快照；此前每个 Run 的图谱继续保存在对应过程条目的 `run_snapshot` 中，并以默认收起的小图标入口按需展开。历史入口不订阅当前 Run 的增量事件，因而不会被新图覆盖或错误推进状态。

## Risks / Trade-offs

- [DAG 在聊天宽度内过于密集] → 聊天只提供 fit-to-view 概览和当前路径，完整交互进入宽屏工作台，同时保留等价列表。
- [图布局在状态更新时跳动] → 坐标只在拓扑或节点尺寸改变时重算；单纯状态变化复用现有布局。
- [历史版本增加 RunView 体积] → RunView 只携带当前图与版本摘要，旧版本按需加载并缓存。
- [SSE 乱序或迟到事件污染当前版本] → 每个增量绑定 Plan ID/version/event ID，冲突时丢弃并重新请求快照。
- [自然语言修订被误解为直接编辑] → UI 明确显示“生成新版本”，并要求重新确认；不暴露可写节点/边 API。
- [新增图形依赖影响包体和维护] → 锁定最小依赖、懒加载全屏工作台、用纯数据 adapter 隔离库 API，并设置 bundle 检查。
- [Lineage 不完整导致差异错误] → 后端创建新 Plan 时强制记录保留节点 lineage；缺失时显示“无法确定沿袭”，不以标题猜测。
- [图谱看起来像隐藏思维链] → 文案统一使用计划、执行、证据和审计语义，推理只展示既有安全摘要。
- [右侧窗格挤压小屏对话] → 宽屏使用独立网格列，窄屏切换为可收起覆盖层；任何视口操作都不改变计划语义。

## Migration Plan

1. 扩展 Plan 投影 Schema、版本查询和 lineage 字段，同时保留现有 `plan_graph.nodes[].depends_on` 供旧前端读取。
2. 增加领域事件及后端合同测试；旧前端忽略未知事件，现有 RunView 刷新继续兜底。
3. 引入前端图谱类型、reducer、布局 adapter 和只读工作台，先替换等待确认与终态审计的线性 Plan 列表。
4. 接入执行中节点状态、节点 Trace、证据和版本差异，再启用自然语言修订入口。
5. 完成桌面、移动、暗色、键盘、屏幕阅读器、reduced-motion、断流恢复和大图视觉验证。
6. 若图谱出现严重回归，可通过前端 feature flag 回退到现有线性 Plan 投影；后台新增快照和事件保持向后兼容。

## Open Questions

- 第一版全屏工作台使用对话内 modal，后续若需要跨 Run 比较或复杂版本分析，再评估独立路由。
- checkpoint replay/fork、层级子图、多 Agent 所属关系和关键路径预测保留为后续独立变更，不阻塞当前实现。
