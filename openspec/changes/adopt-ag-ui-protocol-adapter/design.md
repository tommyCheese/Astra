## 背景

Astra 当前已经具备丰富且持久化的 `RunEvent` 日志、在同一请求中创建 Run 并返回 SSE 的端点、事件重放端点、权威 `RunView` 快照，以及用于回答、过程条目、计划图、审批和 Subagent 的 React reducer。这套契约能够有效服务第一方应用，但具有明显的应用专属性：浏览器需要理解内部事件名，主聊天组件同时协调传输和投影，标准客户端无法安全地发现或消费 Astra。

AG-UI 提供类型化的 `RunAgentInput` 请求，以及流式生命周期、消息、工具、推理、状态、Activity、Interrupt 和错误事件。它是一种公开交互协议，而不是 Astra Runtime、授权、持久化、审计模型或 React 设计系统的替代品。其尚未进入 1.0 的 SDK，以及快速演进的 Interrupt 与能力发现接口，要求我们进行显式版本管理和边界隔离。

本次实现会涉及 FastAPI 边界、Run 应用服务、事件投影、协议关联持久化、前端传输与状态组织、安全策略和灰度遥测。在引入 AG-UI 路径的过程中，现有原生客户端和正在进行的 OpenSpec 变更必须继续正常工作。

## 目标 / 非目标

**目标：**

- 在不改变内部 Runtime 事件词汇的前提下，通过符合规范的 HTTP/SSE AG-UI 端点暴露 Astra。
- 保持文本、过程、计划、工具、审批和 Subagent 更新在 React 中即时渲染。
- 为每个公开事件提供稳定标识、正确有序的生命周期语义、经过清洗的 payload 和确定性的恢复行为。
- 支持带版本的 State 与 Activity 快照、有界 RFC 6902 增量、基线校验和安全的快照回退。
- 将 AG-UI Interrupt 映射到 Astra 已有的持久化审批和等待用户续执行流程，同时不削弱冻结操作、权限、令牌或版本校验。
- 通过命名空间化的 Activity schema 和通用降级内容，保留 Astra 特有的计划图、验证、Artifact 和 Agent 谱系。
- 让原生传输和 AG-UI 传输并行运行，直到可量化的功能一致性与协议一致性得到确认。

**非目标：**

- 不使用 AG-UI 类型替换 Astra `RunEvent`、`RunView`、Runtime 状态机、ToolRouter、权限引擎或审计持久化。
- 不使用 CopilotKit 或其他预制聊天 UI 替换现有 React 视觉系统。
- 不允许 Astra 执行任意客户端提供的工具。
- 首个版本不实现 A2UI、Open-JSON-UI、MCP-UI、语音、AG-UI 二进制传输、WebSocket 传输或远程 Agent-to-Agent 联邦。
- 不暴露隐藏思维链、凭据、私有路径、未经限制的原始工具 payload 或内部异常堆栈。
- 本变更不删除原生 `/api/runs/stream` 或 `/api/runs/{id}/events` 端点。

## 设计决策

### 1. Astra 事件保持权威，AG-UI 是公开投影

持久化的 Astra 事件和 Run 快照继续作为事实来源。新增的接口层投影器消费已经提交的 Astra 事件，并发出零个、一个或多个 AG-UI 事件；它也可以根据权威 Run 投影构建经过清洗的公开快照。

这样可以避免标准协议的最小公分母削弱 Astra 特有概念，例如计划版本、节点尝试次数、continuation token、Agent 局部序列、证据和受治理审批。我们不选择仅存储 AG-UI 事件，因为它们不足以支持内部恢复、权限审计和 Trusted Runtime 执行。

依赖方向为：

```text
React 展示层/Store       -> AG-UI 客户端契约
AG-UI 路由/投影器        -> Astra 应用服务和读取模型
Astra Runtime/Domain     -X-> AG-UI 和 React
```

### 2. 增加并行的 HTTP/SSE 协议入口

`POST /api/ag-ui` 接收经过校验的 AG-UI `RunAgentInput`，返回经过 SSE 编码的 `BaseEvent` 对象。`GET /api/ag-ui/capabilities` 返回带版本的能力声明。对于 AG-UI 尚未标准化的显式 Astra 控制能力，包括持久化的服务端取消，使用命名空间化端点或通过能力声明公布的扩展 URL，而不能假装关闭 HTTP 流就等于 Runtime 已被取消。

首个版本只支持文本 SSE。原生路由保持不变，AG-UI 路由由配置开关控制。我们不选择反向代理或独立 TypeScript shim，因为 Astra 已经拥有 Python/FastAPI 流式边界，增加第二个服务会引入额外延迟和生命周期歧义。

### 3. 校验并白名单化入站输入

输入适配器将 `threadId` 映射到当前主体有权访问的 Astra Task/Conversation，并将每个协议 Run 映射到一个内部 Run 或续执行绑定。适配器只提取权威会话历史中尚未出现的新用户输入。`forwardedProps.astra` 必须通过显式且带版本的 schema 校验，只允许回答模式、计划执行方式、模型选择、Skill 标识和 Subagent 模式等字段。

默认情况下，`RunAgentInput.tools` 不会转换为可执行后端工具。在另一个独立的受治理工具注册设计完成之前，能力发现会声明不支持客户端提供工具的执行。未知 forwarded property 按已公布的协议 profile 被拒绝或忽略，并且绝不会隐式进入模型提示或 Runtime 配置。

### 4. 使用带状态的防腐投影器和确定性标识

投影器维护每条流的协议状态，包括内部 Run 和协议 Run 标识、活动消息标识、尚未闭合的工具调用、已经发出的终态事件、公开 Activity 投影、revision，以及最后一个源事件游标。标识尽可能从持久化 Astra 标识确定性派生，例如 `astra-answer:<run-id>` 和 `astra-plan:<plan-id>`；因此重放或重连不会创建重复 UI 对象。

投影器强制保证以下协议顺序：

```text
RUN_STARTED -> TEXT_MESSAGE_START -> TEXT_MESSAGE_CONTENT* -> TEXT_MESSAGE_END
RUN_STARTED -> TOOL_CALL_START -> TOOL_CALL_ARGS* -> TOOL_CALL_END -> TOOL_CALL_RESULT
RUN_STARTED -> events* -> RUN_FINISHED | RUN_ERROR
```

没有公开价值的内部事件不产生 AG-UI 事件。Astra 特有但可以安全公开的事件使用 `ACTIVITY_*` 或 `CUSTOM`，并采用 `astra.*` 命名空间。我们不选择一对一重命名事件，因为多个 Astra 事件必须经过聚合、拆分或抑制，才能满足公开生命周期和安全规则。

### 5. 区分公开 State 与时间线 Activity

`STATE_SNAPSHOT` 和 `STATE_DELTA` 只包含当前共享交互状态：公开 Run 阶段、回答模式、已公布的控制能力和待处理 Interrupt 摘要。它们不包含完整 `RunView`、凭据、continuation token、内部 AgentState 或私有策略解释。

结构化时间线单元使用带版本的 Activity schema：

- `astra.plan`
- `astra.agent_tree`
- `astra.verification`
- `astra.artifact`
- `astra.tool_activity`

每个 Activity 都包含 `schemaVersion`、`revision`、稳定实体标识、紧凑标题/摘要和 `fallbackText`。实体集合使用 `order` 加 `byId` 映射，确保单个实体变化时 JSON Patch 路径保持稳定。

### 6. 先建立快照基线，再发送增量；无法确认时封闭式重新同步

每条新的 AG-UI HTTP 流都必须先通过快照建立公开 State 和已有 Activity 的基线，然后才能发送对应增量。在基线已知的情况下，投影器先将 Astra 事件归约为一个新的完整公开对象，再比较前后两个安全对象，最后生成有界的 RFC 6902 Patch。

Delta 元数据在 Astra 扩展 envelope 中记录 `baseRevision`、`revision` 和 `sourceEventId`。客户端只有在 Activity 类型、schema 版本和 base revision 均匹配时才应用增量。以下情况都会触发权威快照，而不是继续发不确定的增量：缺少基线、事件缺口、Patch 失败、计划版本变化、权限变化、schema 变化、大规模结构变化，或 Patch 大小接近完整快照。

首个版本不声明支持跨连接的可恢复 Delta。重连后重新接收完整快照，再继续实时 Delta。与假设浏览器内存一定在断线后保留相比，这种方式更简单也更安全。只有在一致性测试和事件压缩行为得到验证后，才考虑公布基于 cursor 的增量重放。

### 7. 在持久化 Astra 状态之上将审批与用户输入暂停建模为协议 Interrupt

当 Astra 进入符合条件的 `waiting_user` 状态时，适配器先发出必要的 `STATE_SNAPSHOT` 和 `MESSAGES_SNAPSHOT`，然后发出带 Interrupt outcome 的 `RUN_FINISHED`。工具审批使用 `reason: "tool_call"`，绑定原始 `toolCallId`，并暴露只代表 Astra 实际允许决策的安全 response schema。

AG-UI 恢复会在同一 thread 上创建一个新的协议 Run。持久化绑定将这个协议 Run 和 interrupt ID 关联到同一个暂停中的 Astra 内部 Run 以及对应审批/等待记录。随后，入站适配器使用服务端持有的 continuation 数据和版本校验调用现有审批或续执行服务。重复或过期的协议响应仍保持幂等，且不能导致冻结操作执行两次。

持久化协议绑定与核心 `RunEvent` 事实分开存储。临时公开投影可以重建并允许缓存，但 Interrupt 关联必须在进程重启后仍然存在。我们不选择“每次 Interrupt 都创建一个新的 Astra 内部 Run”，因为这会破坏现有计划、审批和完成判定的不变量。

### 8. 保持安全的推理与工具边界

只有显式生成且长度有界的推理摘要才映射为 AG-UI reasoning 事件。标记为隐藏的供应商推理或原始思维链会被抑制；推理不可用时只能通过安全元数据表达。工具参数和结果只有在现有公开清洗器移除凭据、受保护路径、私有 Workspace 数据、未经验证的 Artifact 链接和无界输出后才允许发出。

协议投影器不能授权或执行工具。工具执行继续由 Astra 负责注册、权限、审批、沙箱、凭据和效果检查。

### 9. 在 AG-UI 与 React 组件之间增加传输无关的投影 Store

前端引入用于启动、取消和解决交互的传输契约。AG-UI 实现使用经过审核的精确版本 `@ag-ui/core` 和 `@ag-ui/client`；原生实现继续用于灰度和回滚。

标准消息/State 和 Astra Activity 会被归约为稳定的 View State。组件接收完整 View Model，而不是原始网络事件。文本从首个 content 事件开始渲染，Activity 从首个快照开始渲染，后续 Delta 只更新相关 View Model。未知 Activity 类型和 Interrupt 原因使用安全的通用降级组件，不能让整个会话失败。

高频文本、reasoning 和 Activity 更新会被累积，并且最多每个动画帧提交一次；首个可显示内容、终态、错误、Interrupt 和 Artifact 可用事件仍需立即处理。流结束只负责完成状态收敛，不是首次渲染的前置条件。

### 10. 对集成进行版本管理并固定依赖

集成会公布独立于 Activity schema 版本的 Astra AG-UI profile 版本。尚未进入 1.0 的 AG-UI npm 包固定为经过审核的精确版本，初始版本为 `0.0.57`；只有在协议 fixture、类型、Interrupt 和前端回归测试通过后才允许升级。AG-UI 库类型只能存在于适配器包中，不得泄漏到 Astra Domain 契约。

### 11. 使用黄金事件流、协议一致性、双栈一致性和故障注入进行验证

测试覆盖普通文本、推理摘要、工具成功/失败、Trusted 计划执行、并行 Subagent、审批 Interrupt/Resume、自由输入、取消、Runtime 错误、断线、重复源事件、缺失 revision、畸形 Patch、过期 resume 和重启恢复。

黄金测试断言准确的公开事件顺序和经过清洗的 payload。双栈一致性检查比较用户可见结果，而不要求两个协议拥有完全相同的事件数量。浏览器测试验证首事件渲染、按帧批处理、通用降级、重新同步和最终收敛。安全测试尝试注入工具、forwarded property、secret、路径、超大 payload 和无效关联标识。

## 风险 / 权衡

- [AG-UI 0.x 改变事件或 Interrupt 契约] → 固定精确版本、公布 profile 版本、隔离 SDK 类型，并要求升级前审核黄金事件流。
- [双协议逐渐产生行为偏差] → 从同一组已提交 Astra 事实生成两种投影，度量投影一致性，并为每个受支持流程保留一致性 fixture。
- [协议 Run 与内部 Run 标识造成审计混乱] → 持久化显式绑定，只在经过授权的诊断界面同时暴露两类标识。
- [JSON Patch 破坏客户端投影] → 要求基线 revision，使用稳定 `byId` 路径，校验 Patch，按 Activity 隔离失败，并回退到快照。
- [公开投影泄漏私有数据] → 只在已经清洗的公开对象之间计算 Delta，使用字段白名单和 payload 限制，并测试 secret/路径清洗。
- [重连时 Snapshot 流量较大] → 首先为正确性接受这部分带宽成本，只有遥测证明有必要时再增加 cursor 重放。
- [React 重构引入回归] → 保留现有组件和原生传输，逐个投影领域迁移，并在 feature flag 下比较浏览器 fixture。
- [关闭 AG-UI HTTP 请求被误认为服务端取消] → 公布并使用显式取消扩展；除非取消命令成功，否则文档明确 abort 只代表传输结束。
- [客户端工具绕过治理] → 声明不支持，并拒绝包含执行意图的客户端工具定义。
- [投影工作增加首 Token 延迟] → 生命周期和文本事件不等待非关键 Activity 快照，限制清洗和 Patch 计算成本，并保持现有帧与 flush 预算。

## 迁移计划

1. 添加协议 schema、能力声明、依赖固定策略、黄金 fixture 和默认关闭的 AG-UI 路由。
2. 实现入站校验、持久化 Run/Interrupt 绑定、公开清洗器以及生命周期/文本/错误投影。
3. 添加 State 和带版本的 Activity 快照，然后实现同一流内的安全 Delta 与重新同步行为。
4. 添加工具、reasoning、审批/输入 Interrupt、取消扩展、计划、Subagent、验证、Artifact 和重启恢复流程。
5. 引入前端 Transport/Store 边界，先使用 fixture 验证，再连接真实 AG-UI 路由。
6. 在保留原生传输的情况下，为开发环境和选定 cohort 启用 AG-UI，收集延迟、错误、重新同步和一致性指标。
7. 只有在协议一致性、安全、恢复、无障碍和延迟门槛全部通过后，才将 AG-UI 设为第一方默认传输。删除原生传输需要单独的破坏性变更。

回滚时关闭 AG-UI feature flag，并将 React Transport 切回原生实现。Astra Runtime 事件、Run 持久化、审批状态和原生端点保持不变，因此回滚不需要反向数据迁移。持久化的协议绑定记录可以保留用于审计，也可以在后续迁移中移除。

## Review Decisions Based on the Current Implementation

- 第一阶段只把 AG-UI 作为第一方浏览器的 feature-gated transport；在外部 API 的认证、租户隔离、兼容承诺和滥用控制完成独立审核前，不声明为公共兼容 API。
- `threadId` 与 protocol Run binding 必须绑定当前 authenticated principal；任何跨 principal/thread 查询继续返回不泄漏存在性的统一失败。
- 显式取消沿用 capabilities 中公布的 Astra endpoint；transport abort 只关闭连接。未来标准控制事件成熟后另提兼容变更。
- 当前代码中的事件、Activity、reasoning、输入和 Patch 比例上限作为开发基线；生产上限只能通过 12.x 性能/故障数据校准，不在审核时凭经验放宽。
- 生成式 UI 不属于本提案。先完成固定 schema Activity、fallback、恢复与主聊天接入，再单独评估。
