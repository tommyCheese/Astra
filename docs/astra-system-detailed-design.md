# Astra 系统详细设计

## 1. 文档目的与范围

本文描述 Astra 当前代码的系统结构和关键实现，目标是让开发者能够沿着真实调用链定位代码、理解安全边界，并据此扩展 Agent、工具、审批和交付能力。

本文以 `main@e1bdc3b` 为分析基线，覆盖：

- FastAPI 后端与 React 前端的整体分层；
- Task、Run、Plan、Turn、ToolCall 等核心领域对象；
- Run 创建、规划、执行、暂停、审批恢复、验证和终结；
- Effect-aware 权限判定、授权租约和无人值守权限包；
- Task Workspace、Docker Sandbox、Artifact 和变更审计；
- SSE 事件流及前端状态聚合；
- 当前实现边界、风险和后续演进建议。

本文不重新定义产品需求。OpenSpec 仍是需求与变更决策的来源；本文回答“当前代码如何工作”。

## 2. 设计结论摘要

Astra 不是一个简单的“模型 + 工具”循环，而是一个以持久化 Run 为执行单元、以确定性 Runtime 为控制面的 Agent 系统：

```text
用户目标
   │
   ▼
Task / Conversation ── 1:N ── Run
                              │
                              ▼
              TaskContract + PlanGraph + AgentState
                              │
                              ▼
 Model Decision ──▶ Policy Gate ──▶ Tool Runtime
                         │                 │
                    deny/ask/allow         ▼
                         │          Workspace / Sandbox
                         ▼                 │
                  Approval + Lease         ▼
                              Observation / Artifact / Event
                                         │
                                         ▼
                          Evaluation + Reflection + Verification
                                         │
                                         ▼
                                  Final Answer / Terminal State
```

核心设计选择如下：

1. **模型提出行动，Runtime 决定是否执行。** 模型不能跳过策略、权限、观察评估和完成验证。
2. **审批对象是具体行为，不是工具名。** 后端把本次调用解析为冻结的 `ActionEffectPlan`，再判定 `allow | ask | deny`。
3. **Conversation 与执行解耦。** 当前 `TaskRecord` 承载产品上的 Conversation，同一 Task 可以连续创建多个 Run。
4. **Run 可恢复。** 计划、AgentState、Turn phase、ToolCall、等待状态和事件都落库；审批或澄清后继续原 Run。
5. **文件状态属于 Task。** Task Workspace 跨同一 Task 的多个 Run 保留；Sandbox Job 是隔离的执行实例。
6. **事件是展示和审计的共同基础。** 后端持久化 `RunEventRecord`，前端通过 SSE 增量聚合答案和过程状态。

## 3. 实现成熟度

| 能力 | 当前状态 | 主要实现位置 |
|---|---|---|
| Task / Run 持久化与多轮 Conversation | 已进入主链路 | `db/models.py`、`repositories/runs.py`、`api/conversations.py` |
| 结构化规划、AgentState、反思与完成闸门 | 已进入主链路 | `runner/engine.py`、`runner/planning.py`、`runner/agent_loop.py` |
| Effect Plan 与统一权限入口 | 已进入主链路 | `permissions/effects.py`、`permissions/engine.py` |
| 一次、Run 级、Task 级审批授权 | 已进入主链路 | `api/runs.py`、`repositories/runs.py` |
| Task Workspace 与文件差异审计 | 已进入主链路 | `workspaces/runtime.py`、`repositories/workspaces.py` |
| Docker 沙箱与 Artifact 收集 | 已进入主链路，受配置和 Docker 可用性影响 | `sandbox/`、`artifacts.py` |
| Tool Catalog 冻结与 provider 信任检查 | 已进入主链路 | `runner/agent_loop.py`、`permissions/governance.py` |
| DataFlowState 更新和外发加严基础 | 部分进入主链路 | `runner/agent_loop.py`、`permissions/engine.py` |
| Credential Broker | 基础实现与测试已具备，未普遍接入工具调用 | `permissions/credentials.py` |
| 子 Agent 权限衰减委托 | 数据模型、仓储和测试已具备，主循环尚未创建子 Agent | `repositories/permissions.py` |
| MCP / 插件供应链治理 | 通用模型已设计；当前主要覆盖已注册 Tool provider | `permissions/governance.py` |

## 4. 代码分层与依赖方向

```text
frontend/src
  App.tsx / api.ts / processStream.ts
              │ HTTP + SSE
              ▼
backend/app/api
  runs / conversations / permissions / runtime / tools / usage
              │
              ▼
backend/app/runner                 backend/app/permissions
  engine / agent_loop / planning    effects / engine / governance
              │                           │
              ├──────────────┬────────────┘
              ▼              ▼
backend/app/tools       backend/app/sandbox + workspaces
              │              │
              └──────┬───────┘
                     ▼
backend/app/repositories
                     │
                     ▼
backend/app/db/models.py
                     │
                     ▼
                 SQLite / PostgreSQL
```

依赖约束：

- API 层负责协议校验、错误映射和后台任务调度，不承载 Agent 决策。
- Runner 负责编排状态机，只通过 Repository 持久化执行状态。
- Tool 使用 `ToolExecutionContext` 获取受控能力，不直接依赖 HTTP 层。
- Permission Engine 输入冻结的事实并返回确定性结果；AgentLoop 只能消费结果。
- Workspace 和 Sandbox 是 enforcement 层，不能被“用户已批准”绕过。

## 5. 核心领域模型

### 5.1 聚合关系

```text
TaskRecord（产品上的 Conversation）
 ├─ RunRecord[]
 │   ├─ PlanRecord[] ── PlanNodeRecord[] / PlanEdgeRecord[]
 │   ├─ AgentTurnRecord[]
 │   ├─ ToolCallRecord[] ── ApprovalRequestRecord?
 │   ├─ ApprovalGrantRecord[]
 │   ├─ ArtifactRecord[]
 │   ├─ SandboxJobRecord[]
 │   ├─ RunEventRecord[]
 │   ├─ AgentIdentityRecord[]
 │   ├─ ToolCatalogSnapshotRecord?
 │   └─ DataFlowStateRecord?
 ├─ TaskWorkspaceRecord?
 │   ├─ WorkspaceFileRecord[]
 │   ├─ WorkspaceChangeRecord[]
 │   └─ WorkspaceCheckpointRecord[]
 └─ ConversationShareRecord?
```

### 5.2 Task 与 Run

`TaskRecord` 保存标题、描述、置顶和共享等 Conversation 级信息。`RunRecord` 保存一次实际执行的模型策略、Agent Profile 快照、Reasoning Policy、计划、状态版本、等待状态和最终结果。

关键语义：

- 同一 Task 下的新消息可创建新 Run，并复用 Task Workspace。
- `agent_profile_snapshot` 和 `model_policy` 在创建时冻结，避免历史 Run 随配置漂移。
- `state_version` 是 AgentState 的乐观并发版本；恢复和反思补丁必须基于预期版本更新。
- `waiting_state` 保存暂停节点、continuation token 对应上下文及用户请求。

### 5.3 Plan、Turn 与 ToolCall

- `PlanRecord` 有版本和 `supersedes_plan_id`，支持反思后的计划替换。
- `AgentTurnRecord` 是可恢复执行检查点，记录 decision、observation、evaluation、reflection、phase 和 idempotency key。
- `ToolCallRecord` 保存工具输入、输出、权限声明、副作用等级和错误。
- `ApprovalRequestRecord` 与 ToolCall 一对一，冻结输入与行为分析结果。

这种拆分使“模型想做什么”“工具实际做了什么”“用户批准了什么”可以分别审计。

## 6. 一次请求的完整链路

### 6.1 创建 Run

前端 `createRun()` 调用 `POST /api/runs`。后端 `create_run()` 依次完成：

1. 校验 goal；
2. 合并数据库 Tool 开关和本次模型配置；
3. 由 `RunProfileResolver` 编译 answer mode 与 reasoning policy；
4. 对无人值守请求强制校验签名 Permission Bundle；
5. 冻结 Agent Profile 快照；
6. 创建 Task/Run 及初始事件；
7. 提交事务后使用进程内异步任务调度 `RunEngine`。

精简后的真实代码形态：

```python
profile = RunProfileResolver().resolve(payload.answer_mode, payload.reasoning_policy)
run = await repo.create_task_run(
    goal,
    run_settings.model_policy,
    payload.task_id,
    reasoning_policy=profile.reasoning_policy.model_dump(mode="json"),
    execution_profile=execution_profile,
    agent_profile_snapshot=load_agent_profile().snapshot(),
)
_schedule_run(run.id, run_settings)
```

这里的后台任务是单进程实现，适合当前本地产品形态；它不是分布式队列。

### 6.2 RunEngine 选择执行路径

`RunEngine._run_with_repo()` 根据 Profile 和已有状态选择路径：

- `standard` 且非 `plan_only`：直接进入 AgentLoop 的快速路径；
- 已有 `agent_state`：按持久化检查点恢复；
- 其他情况：先构建 TaskContract 和 Plan；
- `plan_only`：保存计划并终结，不执行持久副作用；
- 合同存在歧义：进入 `waiting_user`；
- 合同清晰：激活 Plan 并进入 AgentLoop。

### 6.3 AgentLoop 固定节点

主循环的概念顺序是：

```text
load state
   ↓
select active plan node
   ↓
assemble context + model decision
   ↓
policy gate
   ↓
effect analysis + permission decision
   ├─ deny ──▶ controlled failure / blocked
   ├─ ask  ──▶ persist approval + waiting_user
   └─ allow ─▶ execute tool
                   ↓
       observation + workspace changes + artifacts
                   ↓
          evaluate progress / reflect / replan
                   ↓
             completion gate + verification
```

模型输出只是 `Decision`。`LoopOrchestrator`、`ObservationEvaluator`、`ReflectionGate` 和 `CompletionGate` 确保节点顺序与预算约束由 Runtime 掌握。

### 6.4 终态

代码和 Schema 使用以下终态语义：

| 状态 | 含义 |
|---|---|
| `completed` | 强制成功条件和验证已通过 |
| `completed_with_warnings` | 可交付，但存在非关键缺口 |
| `waiting_user` | 等待澄清或审批，可恢复同一 Run |
| `blocked` | 目标明确，但策略、能力或预算内无可行路径 |
| `failed` | 内部错误或基础设施错误导致受控执行失败 |
| `cancelled` | 用户主动取消；后台任务和持久状态均被收敛 |

## 7. 统一工具执行管线

### 7.1 ToolSpec 是能力上限

每个 Tool 暴露 `ToolSpec`，包含 schema、permissions、risk、backend、resource profile、provider identity 和 digest。

```python
class ToolSpec(BaseModel):
    name: str
    version: str
    input_schema: dict[str, Any]
    permissions: list[str]
    execution_backend: str = "in_process"
    provider_id: str = "astra.builtin"
    provider_digest: str = "builtin"
```

`permissions` 表示工具可能使用的最大权限，不表示每次调用都自动获得这些权限。

### 7.2 调用前冻结 Tool Catalog

AgentLoop 在 Run 开始时：

1. 枚举已注册 ToolSpec；
2. 计算 input schema digest；
3. 用 `ExtensionTrustPolicy` 验证 provider；
4. 计算整个 catalog digest；
5. 写入 `ToolCatalogSnapshotRecord`。

因此 provider、版本或 schema 漂移可以被审计；不受信 provider 在进入执行前失败关闭。

### 7.3 ActionEffectPlan

`DefaultEffectAnalyzer` 按本次输入生成行为计划。例如：

- `web_search` / `web_fetch` → `network_read`；
- `chart.render` → `temporary_compute + artifact_write`，可附加 `workspace_read`；
- `bash_execute` → 由 Bash analyzer 识别读、写、删除、网络和未知程序；
- 其他工具 → 从声明权限映射 effect；无法分类时使用高风险 `process_execute_unknown`。

```python
effect_plan = DefaultEffectAnalyzer().analyze(
    tool.spec,
    decision.tool_input,
    task_id=initial_run.task_id,
)
effect_hash = effect_plan_hash(effect_plan)
```

Effect Plan 主要字段包括 tool/version、summary、cwd、effects、required permissions、network scope、analyzer version/digest 和 approval required。

### 7.4 ToolExecutionContext

授权通过后，AgentLoop 只向 Tool 注入受控上下文：

```python
ToolExecutionContext(
    run_id=run_id,
    tool_call_id=call.id,
    trace_id=call.id,
    artifact_service=artifact_service,
    sandbox_service=sandbox_service,
    task_id=initial_run.task_id,
    workspace_path=workspace_path,
    workspace_mode=workspace_mount_mode(effect_plan),
    effect_plan=effect_plan.model_dump(mode="json"),
    runtime_identity_id=runtime_identity.id,
)
```

这使 Tool 无需自行猜测目录、审计 ID 或权限结果。

## 8. 权限、审批与授权租约

### 8.1 唯一授权入口

主执行链只调用：

```python
PermissionEngine().authorize_invocation(...)
```

它统一合并：

- ToolSpec 权限上限；
- 本次 Effect Plan；
- 执行模式；
- 平台保护路径；
- Run/Task Grants；
- 一次性批准；
- DataFlowState；
- 无人值守 Permission Bundle；
- provider、schema 和身份链约束。

返回值是唯一聚合结果 `allow | ask | deny`。决策优先级为 `deny > ask > allow`。

### 8.2 身份链

每次工具调用建立或复用：

```text
main_agent
   └─ tool_provider / external_provider
         └─ tool_runtime(provider:tool@version)
```

`PermissionSubject.delegation_chain` 将三者写入权限请求。权限中心据此展示执行者、provider 和 reviewer，而不是把所有行为都归到模糊的“AI”。

### 8.3 执行模式

| Effect | `plan_only` | `request_approval` | `auto_approval` |
|---|---|---|---|
| 允许的只读行为 | 执行 | 执行 | 执行 |
| 临时计算 | 执行 | 执行 | 执行 |
| Workspace / Artifact 持久写入 | 生成 blocked observation | Grant 或审批 | 在平台边界内执行 |
| 外部写入、删除等副作用 | 不执行 | Grant 或审批 | 在平台边界内执行 |
| 平台 deny | 拒绝 | 拒绝 | 拒绝 |

`auto_approval` 只跳过交互，不等于 full host access。

### 8.4 审批冻结与恢复

当结果为 `ask` 时，系统保存：

- frozen input 与 input hash；
- frozen effect plan 与 effect plan hash；
- analyzer version 与 digest；
- tool/version、影响级别、预览和可选 matcher；
- continuation token 对应的等待状态。

用户可以：允许一次、允许当前 Run 中类似行为、明确允许当前 Task 中类似行为、拒绝。

恢复时系统重新分析调用，并逐项校验冻结值。输入、Effect Plan 或 analyzer 版本变化都会触发 `approval_integrity_error` 或重新审批，避免“批准 A，执行 B”。

### 8.5 Lease 匹配

`ApprovalGrantRecord` 不是永久布尔值，而是 capability lease。匹配至少检查：

- status、撤销和过期时间；
- Run / Task scope；
- subject；
- effect kinds；
- resource matcher；
- invocation constraints；
- max uses 与 use count。

执行前匹配，执行授权后才消费 grant 使用次数。

### 8.6 无人值守运行

`interactive=false` 时 API 强制要求签名 Permission Bundle。Bundle 校验失败或本次调用超出工具、effect、资源、预算、期限等范围时 fail closed；运行时不会隐式弹出审批并假设同意。

## 9. Task Workspace、Sandbox 与 Artifact

### 9.1 三者职责

| 概念 | 生命周期 | 职责 |
|---|---|---|
| Task Workspace | 跨同一 Task 的多个 Run | 保存可继续编辑的任务文件 |
| Sandbox Job | 单次受控计算或 ToolCall | 隔离进程、网络、资源和挂载 |
| Artifact | 可交付、可下载的版本化结果 | 保存 provenance、checksum、MIME 和安全状态 |

不能把 Workspace 等同于 Artifact：前者是工作状态，后者是交付快照。

### 9.2 Workspace 路径与配额

`WorkspaceRuntimeService.prepare(task_id)` 把目录限制在：

```text
<task_workspace_store_path>/tasks/<task_id>
```

扫描时拒绝：

- 符号链接、硬链接和非普通文件；
- 路径逃逸与不安全文件名；
- 超过深度、单文件、文件数或总容量配额；
- 修改 `.git` 等受保护路径。

默认配额以 `Settings` 为准：10,000 文件、1 GiB 总量、100 MiB 单文件。

### 9.3 并发与挂载

Workspace 写操作同时使用：

- 进程内 `asyncio.Lock`；
- 文件级 `flock`。

Effect Plan 决定 `none | read_only | read_write` 挂载模式。即使权限结果为 allow，Sandbox 仍只得到该模式对应的技术能力。

### 9.4 差异捕获

Sandbox 执行前后分别扫描 manifest，以 checksum 比较：

```text
before manifest ── execute ── after manifest
       │                              │
       └──────── compare ─────────────┘
                    │
          created / modified / deleted
                    │
      WorkspaceChangeRecord + WorkspaceFileRecord
```

每项变更记录 Run、ToolCall、路径、前后 checksum、MIME、安全状态和是否为交付候选。Run 终结时再创建 Workspace checkpoint。

### 9.5 Sandbox 约束

默认 provider 是 Docker。配置约束包括 image、wall time、memory、CPU、PID、network 和 workspace mount。沙箱服务记录 `SandboxJobRecord`，包括 runtime profile、镜像 digest、输入输出 Artifact、退出原因以及截断后的 stdout/stderr 摘要。

### 9.6 Artifact 交付

Artifact Collector 验证扩展名、magic/MIME、大小和数量，再写入本地 Artifact Store。`ArtifactRecord.provenance` 将 Artifact 关联到 Run、ToolCall、PlanNode 和 Sandbox Job。前端或 API 通过受控 content endpoint 下载，不直接暴露任意本地路径。

## 10. 推理、反思与恢复

### 10.1 Reasoning Policy

用户请求的 effort、planning、reflection、execution mode 和 budgets 会先经 `RunProfileResolver` 编译，再冻结到 Run。平台配置是硬上限：

```python
max_tool_calls = min(
    policy.budgets.max_tool_calls,
    settings.agent_max_tool_calls,
)
```

因此前端偏好只能收窄或在允许范围内选择，不能提高部署上限。

### 10.2 反思补丁

反思只有在 `ReflectionGate` 允许且未超过预算时发生。模型返回的 patch 还要通过结构校验和 AgentState 版本校验；无 actionable patch 的反思只作为观察，不静默改变计划。

### 10.3 崩溃恢复与幂等

Turn phase 区分 `executing`、`result_recorded`、`committed` 等阶段：

- 结果已记录但未提交观察：重放已保存结果，不重复调用工具；
- read-only 调用执行中断：可用相同 idempotency key 重试；
- 非只读调用结果未知：进入 `waiting_user`，不自动重试。

这是避免外部副作用重复执行的关键约束。

## 11. 前端状态与 SSE 展示链路

### 11.1 API 与事件流

前端 `api.ts` 提供 REST 请求，并用：

```ts
const source = new EventSource(`/api/runs/${runId}/events`);
```

订阅持久化 Run Events。SSE 断开时，前端可以重新获取 Run snapshot 并通过 `reconcileProcessSnapshot()` 恢复展示。

### 11.2 两条 UI 状态流

前端将事件分成：

1. **答案流**：`answer.delta` 进入字符缓冲，并按 animation frame 刷新，降低高频 React render；
2. **过程流**：reasoning、tool、approval、workspace、verification 等事件由 `reduceProcessEvent()` 聚合为时间线。

Run snapshot 是权威状态，SSE 是低延迟增量。`reconcileProcessSnapshot()` 负责消除两者暂时不一致。

### 11.3 审批交互

`pending_approval` 存在时，Composer 被禁用并显示 `ApprovalCard`。用户决定调用专用 decision endpoint，而不是把“同意”作为普通聊天文本发送。前端提交成功后先做 optimistic state 更新，再拉取 Run snapshot 校准。

### 11.4 权限中心

`auditPresentation.ts` 将底层 identity 和 permission events 转为用户可读条目，并保留工程审计信息。身份按 type/principal/trust/parent 聚合，当前 Run 优先，历史 Run 数量单独展示。

## 12. API 设计摘要

| API | 用途 |
|---|---|
| `POST /api/runs` | 创建 Task/Run 并调度执行 |
| `GET /api/runs/{id}` | 获取权威 Run snapshot |
| `GET /api/runs/{id}/events` | SSE 增量事件 |
| `POST /api/runs/{id}/cancel` | 幂等取消运行 |
| `POST /api/runs/{id}/resume` | 使用 continuation token 恢复澄清状态 |
| `POST /api/runs/{id}/approvals/{approval}/decision` | 提交结构化审批决定并恢复 |
| `POST /api/runs/{id}/activate-plan` | 激活仅规划结果 |
| `/api/conversations/*` | Conversation 列表、详情、标题、置顶、删除和分享 |
| `/api/permissions/*` | 权限中心、策略模拟、Grant 撤销和 Workspace 视图 |
| `/api/runtime/*` | Runtime profile 与依赖构建 |
| `/api/tools/*` | 工具开关 |
| `/api/usage/*` | 模型调用用量统计 |

所有 API 错误使用统一 envelope：`type`、`code`、`message`、`retryable`、`trace_id`。默认 `API_ALLOW_REMOTE=false` 时，`/api` 只接受 loopback 客户端。

## 13. 配置与部署边界

配置集中在 `backend/app/core/config.py::Settings`。重要分组：

- 模型：provider、name、API key、base URL；
- 工具：web search/fetch、chart、bash 开关；
- Agent 预算：turn/tool/reflection/replan 上限；
- 网络：`allow_network_read`；
- Workspace / Artifact：路径和配额；
- Sandbox：provider、镜像、时间、内存、CPU 和 PID；
- API：CORS 和 remote access；
- 安全：trusted providers 和 Permission Bundle signing secret。

当前代码默认模型为 `openai / gpt-5`，并非旧文档中曾描述的 mock provider。没有 API key 时应显式配置可用 provider，或在测试中注入 mock client。

## 14. 安全不变量

修改代码时必须保持：

1. Tool 输入先过 schema 和 registry 校验，再做 effect analysis。
2. Effect Plan 所需权限不得超过 ToolSpec 权限上限。
3. 所有可执行授权统一经过 `authorize_invocation()`。
4. `deny` 不能被用户批准、Task Grant 或 auto approval 覆盖。
5. 审批恢复必须校验 frozen input、effect hash 和 analyzer identity。
6. Workspace 路径必须解析在受管根目录内，并拒绝 link/path traversal。
7. 非幂等调用结果未知时不得自动重试。
8. secret 不得进入 prompt、普通日志、Workspace 或 Artifact。
9. 无人值守任务超出 Permission Bundle 时必须 fail closed。
10. 前端展示不能被当作 enforcement；真正限制必须在后端和 Sandbox。

## 15. 测试策略与变更落点

### 15.1 测试分层

- API 契约：`backend/tests/test_api.py`、`test_errors.py`；
- Agent loop：`test_agent_loop.py`、`test_engine.py`、`test_plan_runtime.py`；
- 权限审批：`test_approvals.py`、`test_permission_engine.py`、`test_effect_aware_security.py`；
- Workspace/Sandbox：`test_artifacts_sandbox.py`、`test_sandboxed_tools.py`、`test_docker_integration.py`；
- 模型与 reasoning：`test_model_client.py`、`test_model_reasoning*.py`；
- 前端状态与审计：`frontend/tests/*`。

### 15.2 常见改动应同步的位置

| 改动 | 至少检查 |
|---|---|
| 新增 Tool | ToolSpec、registry、effect analyzer、permission tests、UI presentation |
| 新增副作用类型 | `EffectKind`、PermissionRequest 映射、policy、workspace/sandbox enforcement |
| 新增 Run 状态 | Repository transition、Schema、SSE、`processStream.ts`、终态集合 |
| 修改审批 | frozen payload、continuation token、lease matcher、ApprovalCard |
| 修改 Workspace | quota、manifest、protected paths、diff、artifact promotion |
| 修改模型策略 | Resolver、snapshot、预算上限、前端偏好、恢复兼容 |

## 16. 已知边界与演进建议

### 16.1 进程内调度限制

当前 `_schedule_run()` 使用 API 进程内 asyncio task。服务多副本、滚动重启和长任务场景需要持久化 worker queue、claim/heartbeat 和幂等调度，否则恢复能力仍依赖单进程生命周期。

### 16.2 Credential Broker 尚未普遍接入

Broker、Grant 模型和测试已存在，但当前内建工具大多使用部署级配置。后续接入外部写工具时，应让 Tool 只获得短 TTL、服务/资源/动作限定的句柄，避免把长期 secret 放入 `ToolExecutionContext`。

### 16.3 子 Agent 尚未进入主循环

身份与 delegation 数据结构已具备。真正引入子 Agent 时，必须在创建时计算：

```text
child scope ⊆ parent scope ∩ task policy ∩ explicit delegated scope
```

并禁止子 Agent 审批自身提权。

### 16.4 DataFlowState 仍需端到端扩展

当前读取类 effect 会更新 trust source 和 data labels，Permission Engine 已可据此外发加严。后续需要让更多 Tool 精确声明输入数据来源、用途、目的地和保留策略，才能形成完整 DLP 链路。

### 16.5 前端组件需要继续拆分

`App.tsx` 同时承载会话、策略、审批、权限中心、Runtime 管理和展示逻辑。建议按 feature 拆为 conversation、run-stream、approval、permission-center、settings 等模块，并保留 `processStream.ts` 作为纯状态归约层。

### 16.6 规格与实现状态应自动校验

仓库同时存在多个已完成和进行中的 OpenSpec change。建议在 CI 中增加：

- 文档链接检查；
- OpenSpec 完成任务与主规格同步检查；
- API schema / frontend type 漂移检查；
- EffectKind 与 UI 文案覆盖检查；
- migration head 与模型一致性检查。

## 17. 推荐阅读顺序

1. `backend/app/api/runs.py`：请求如何变成 Run；
2. `backend/app/runner/engine.py`：执行路径如何选择；
3. `backend/app/runner/agent_loop.py`：主状态机；
4. `backend/app/permissions/effects.py`：调用如何被解释成行为；
5. `backend/app/permissions/engine.py`：行为如何被授权；
6. `backend/app/sandbox/runtime.py` 与 `backend/app/workspaces/runtime.py`：能力如何被技术强制；
7. `backend/app/db/models.py`：哪些状态可以恢复和审计；
8. `frontend/src/api.ts`、`processStream.ts`、`App.tsx`：状态如何呈现给用户。

---

维护原则：设计文档中的每个关键结论都应能指向实现或测试；如果设计已经改变但代码尚未改变，应记录在 OpenSpec change，而不是把未来行为写成当前事实。

## 18. 数据索引与章节引用

本章汇总本文涉及的数据，便于从数据名称反查设计说明。这里的“数据”包括数据库持久化实体、运行时结构、接口载荷、事件、文件元数据和配置；“出现章节”列列出正文中定义、使用或讨论该数据的章节号，不把本索引自身计入。

### 18.1 会话、运行与推理数据

| 数据 | 载体或典型字段 | 含义 | 出现章节 |
|---|---|---|---|
| 用户目标 | `goal`、`TaskRecord.description` | 用户希望 Agent 完成的原始目标 | 1、2、5.2、6.1、6.3 |
| Task / Conversation | `TaskRecord` | 产品会话聚合根，保存标题、描述、置顶、风险和创建信息 | 2、5.1、5.2、6.1、9.1、12 |
| Conversation Share | `ConversationShareRecord` | Conversation 的可撤销共享快照和访问 token | 5.1、12 |
| Run | `RunRecord` | 一次可执行、可暂停、可恢复和可审计的 Agent 运行 | 1、2、5.1、5.2、6、9.1、10、11、12 |
| Run 状态 | `RunRecord.status` | `created`、`planning`、`executing`、`waiting_user` 及各终态 | 5.2、6.2、6.4、10.3、11.1、15.2 |
| Run 等待状态 | `RunRecord.waiting_state` | 暂停节点、等待原因和恢复上下文 | 5.2、6.2、6.4、8.4、10.3 |
| Continuation Token | API 请求与等待状态中的 token | 防止过期页面或错误客户端恢复错误的 Run 状态 | 8.4、11.3、12 |
| Model Policy | `RunRecord.model_policy` | 本次 Run 冻结的 provider、model 和 base URL 选择 | 5.2、6.1、13 |
| Agent Profile Snapshot | `RunRecord.agent_profile_snapshot` | Run 创建时冻结的身份和治理配置快照 | 5.2、6.1 |
| Execution Profile | `RunRecord.execution_profile` | answer mode、交互模式、权限包和执行参数的组合快照 | 5.2、6.1、6.2、8.6 |
| Reasoning Policy | `RunRecord.reasoning_policy`、`ReasoningPolicySnapshot` | effort、规划、反思、执行模式和预算的请求值与生效值 | 1、5.2、6.1、6.2、8.3、10.1、13 |
| Task Contract | `TaskContract`、`RunRecord.task_contract` | 结构化目标、歧义状态、成功标准和验证要求 | 1、2、5.2、6.2、6.3 |
| Plan Graph | `PlanRecord`、`PlanNodeRecord`、`PlanEdgeRecord`、`RunRecord.plan_graph` | 有版本的执行计划、节点依赖和替换关系 | 1、2、5.1、5.3、6.2、6.3、10.2 |
| Agent State | `RunRecord.agent_state`、`state_version` | 当前计划节点、观察、失败、预算用量和评估的可恢复状态 | 1、2、5.2、6.2、6.3、10.1、10.2 |
| Step | `StepRecord` | 兼容性的线性步骤视图及其执行证据 | 5.1、5.3、6.3 |
| Agent Turn | `AgentTurnRecord` | 单轮决策、执行阶段、观察、评估、反思和检查点 | 1、2、5.1、5.3、6.3、10.3 |
| Decision | `AgentTurnRecord.decision`、模型结构化输出 | 模型提出的回答、工具调用、继续或终止意图 | 2、5.3、6.3 |
| Observation | `AgentTurnRecord.observation`、AgentState observations | ToolResult、模式阻断、失败或反思形成的受控观察 | 1、2、5.3、6.3、8.3、10.2 |
| Evaluation | `AgentTurnRecord.evaluation`、AgentState evaluations | 对计划节点和成功标准进展的确定性或模型辅助评估 | 2、5.3、6.3、10.2 |
| Reflection / Reflection Patch | `reflection`、`reflection_patch` | 对失败或无进展的反思结果及通过校验后可应用的状态补丁 | 1、2、5.3、6.3、10.1、10.2 |
| Failure Fingerprint | AgentState failures | 相同失败策略的稳定指纹和重试计数 | 6.3、10.1、10.3 |
| Idempotency Key | `AgentTurnRecord.idempotency_key` | 工具执行重试、恢复和重复副作用防护的稳定键 | 5.3、10.3、14 |
| Final Answer / Run Result | `RunRecord.result`、`FinalAnswer` | 最终摘要、发现、来源、限制、验证结果和错误信息 | 1、2、5.2、6.3、6.4、11.2 |

### 18.2 工具、权限与安全数据

| 数据 | 载体或典型字段 | 含义 | 出现章节 |
|---|---|---|---|
| Tool Setting | `ToolSettingRecord`、工具开关 | 数据库与部署配置合并后的工具启用状态 | 6.1、12、13 |
| ToolSpec | `ToolSpec` | 工具 schema、权限上限、风险、backend、provider 和资源声明 | 3、4、7.1、7.2、7.3、8.1、14、15.2 |
| Tool Catalog Snapshot | `ToolCatalogSnapshotRecord` | Run 开始时冻结的 ToolSpec 集合及整体 digest | 3、5.1、7.2、14 |
| ToolCall | `ToolCallRecord` | 具体工具调用的输入、输出、状态、错误和副作用级别 | 1、2、5.1、5.3、6.3、8.4、9、10.3 |
| Tool Result | Tool 输出、Observation data | 工具执行产生的数据、警告、指标和 Artifact 引用 | 2、6.3、7.4、9.4、10.3 |
| ToolExecutionContext | `ToolExecutionContext` | 注入 Tool 的 Run、审计、Workspace、Sandbox、Effect Plan 与身份上下文 | 4、7.4、16.2 |
| Effect Item / Effect Kind | `EffectItem`、`EffectKind` | 单个读取、写入、删除、网络、计算、凭据或委托行为 | 3、7.3、8.1、8.3、8.5、15.2、16.4 |
| ActionEffectPlan | `ActionEffectPlan` | 对一次 ToolCall 的行为、资源、权限、风险和网络范围的冻结分析 | 1、2、3、7.3、7.4、8.1、8.3、8.4、9.3、14 |
| Effect Plan Hash | `effect_plan_hash` | ActionEffectPlan 的规范化完整性摘要 | 7.3、8.4、14 |
| Analyzer Identity | `analyzer_version`、`analyzer_digest` | 生成 Effect Plan 的分析器版本与内容身份 | 7.3、8.4、14 |
| Agent Identity | `AgentIdentityRecord`、`PermissionSubject` | main agent、provider、tool runtime、reviewer 的可审计主体 | 2、3、5.1、7.2、7.4、8.1、8.2、11.4 |
| Delegation | `AgentDelegationRecord`、`delegation_chain` | 父子 Agent 或 Runtime 之间受限且可审计的权限委托 | 3、8.1、8.2、16.3 |
| Permission Request | `PermissionRequest` | 主体对资源执行某个 action 的规范化授权请求 | 8.1、8.2 |
| Permission Decision | `PermissionDecision` | `allow`、`ask`、`deny` 及其命中策略和原因 | 1、2、6.3、8.1、8.3、11.4、14 |
| Permission Policy Set | `PermissionPolicySet` | 平台、部署、用户、Task 和 Run 约束的规则集合 | 8.1、8.3、14 |
| Approval Request | `ApprovalRequestRecord`、`pending_approval` | 冻结输入和 Effect Plan 后等待 reviewer 决策的数据 | 2、3、5.1、5.3、6.3、8.4、11.3、12、14 |
| Approval Grant / Lease | `ApprovalGrantRecord` | 一次、Run 或 Task 范围内带资源、次数、期限和调用约束的授权 | 2、3、5.1、6.3、8.1、8.4、8.5、12 |
| Reviewer Identity | `reviewer_identity`、reviewer `AgentIdentityRecord` | 作出审批决定的用户或受控 reviewer 身份 | 5.3、8.2、8.4、11.4 |
| Permission Bundle | `PermissionBundle` | 无人值守 Run 预签名的工具、Effect、资源、预算和期限边界 | 3、6.1、8.1、8.6、13、14 |
| Credential Grant | `CredentialGrantRecord` | 服务、租户、scope、resource、action 和有效期限定的凭据授权 | 3、5.1、16.2 |
| Credential Broker 数据 | 临时 credential handle / grant metadata | 执行时按最小权限签发且不暴露长期 secret 的数据 | 3、14、16.2 |
| DataFlowState | `DataFlowStateRecord` | trust sources、data labels、允许/禁止目的地和保留策略 | 3、5.1、8.1、16.4 |
| Provider Trust Data | `provider_id`、`provider_digest`、`schema_digest`、`trust_level` | Tool provider 与 schema 的供应链身份和信任依据 | 3、7.1、7.2、8.1、8.2、14 |

### 18.3 Workspace、Sandbox 与交付数据

| 数据 | 载体或典型字段 | 含义 | 出现章节 |
|---|---|---|---|
| Task Workspace | `TaskWorkspaceRecord` | 同一 Task 多个 Run 共享的受管文件工作区及配额 | 1、2、3、5.1、9.1、9.2、9.3、9.4 |
| Workspace Manifest | `dict[path, ManifestEntry]` | 某一时点文件的 checksum、大小和 MIME 快照 | 9.4 |
| Workspace File | `WorkspaceFileRecord` | 当前文件状态、路径、checksum、安全状态和交付候选标记 | 5.1、9.2、9.4 |
| Workspace Change | `WorkspaceChangeRecord` | 某 Run/ToolCall 对文件的 created、modified、deleted 差异 | 2、3、5.1、6.3、9.4 |
| Workspace Checkpoint | `WorkspaceCheckpointRecord` | Run 边界上的完整 manifest 与 manifest hash | 5.1、9.4 |
| Workspace Mount Mode | `none`、`read_only`、`read_write` | 根据 Effect Plan 赋予 Sandbox 的实际目录能力 | 7.4、9.3、14 |
| Workspace Quota | max files、total bytes、file bytes、path depth | Workspace 文件数量、容量、单文件大小和路径深度限制 | 9.2、13 |
| Protected Workspace Paths | `.git` 等路径集合 | 即使审批通过也禁止 Tool 修改的控制或供应链路径 | 8.1、9.2、14 |
| Sandbox Job | `SandboxJobRecord` | 单次隔离执行的 runtime、资源、输入输出和退出审计 | 1、2、3、5.1、9.1、9.5 |
| Sandbox Runtime Profile | image、digest、network、CPU、memory、PID、wall time | Sandbox 的可执行环境和强制资源边界 | 7.1、9.5、13 |
| Artifact | `ArtifactRecord` | 可下载交付物的类型、位置、MIME、大小、checksum 和 provenance | 1、2、3、5.1、6.3、7.3、7.4、9.1、9.6 |
| Artifact Provenance | run/tool/plan node/sandbox job IDs | 交付物从哪次执行、哪个工具和哪个环境产生 | 9.6 |
| 文件安全状态 | `security_status` | 文件是否通过类型、magic、路径和内容边界检查 | 9.2、9.4、9.6 |
| 交付候选标记 | `deliverable_candidate` | Workspace 文件是否适合提升为用户可见交付物 | 9.4 |

### 18.4 事件、接口与前端展示数据

| 数据 | 载体或典型字段 | 含义 | 出现章节 |
|---|---|---|---|
| Run Event | `RunEventRecord` | 带自增游标、type、payload 和时间的持久化执行事件 | 2、5.1、6.1、11.1、11.2 |
| Answer Delta | `answer.delta` event | 最终答案的增量文本片段 | 11.2 |
| Process Event | reasoning/tool/approval/workspace/verification events | 前端过程时间线的增量输入 | 11.2、11.4 |
| Run Snapshot | `RunView` | REST 返回的当前权威 Run、等待、审批、结果和审计视图 | 11.1、11.2、11.3、12 |
| Process Stream State | `ProcessStreamState` | 前端归约后的过程条目、阶段和终态展示状态 | 11.1、11.2、16.5 |
| Streaming Answer Buffer | `streamingAnswer`、delta buffer | 前端按 animation frame 批量刷新的答案文本状态 | 11.2 |
| Pending Approval View | `pending_approval` | ApprovalCard 展示的行为摘要、资源、风险和可选决定 | 8.4、11.3 |
| Permission Center View | identities、grants、policy explanations、workspace summary | 面向用户的权限、身份、授权和审计聚合视图 | 3、8.2、11.4、12 |
| Audit Presentation | `AuditLogEntry`、identity groups | 将底层 permission events 与身份转换为用户可读信息 | 11.4 |
| Create/Resume/Decision API Payload | goal、task ID、policy、model、token、decision | 创建、恢复和审批 Run 的结构化请求数据 | 6.1、8.4、8.6、11.3、12 |
| API Error Envelope | `type`、`code`、`message`、`retryable`、`trace_id` | HTTP 和后台运行统一、安全的错误响应 | 12 |
| Usage Data | model invocation、scope、token/cost summary | Run、Task 或全局范围的模型调用用量统计 | 12、13、15.1 |

### 18.5 配置与治理数据

| 数据 | 载体或典型字段 | 含义 | 出现章节 |
|---|---|---|---|
| Settings | `backend/app/core/config.py::Settings` | 部署级模型、工具、预算、网络、存储、沙箱和 API 配置 | 9.2、9.5、10.1、13 |
| Model Configuration | provider、name、API key、base URL | 默认模型以及 Run 级可覆盖的模型连接配置 | 6.1、13 |
| Agent Budget | max turns/tool calls/reflections/replans | 平台上限与 Reasoning Policy 取交集后的资源预算 | 6.3、8.6、10.1、13 |
| Network Policy | `allow_network_read`、Effect network scope | Web 与 Sandbox 可以访问的网络范围 | 7.3、8.1、9.5、13 |
| Artifact Configuration | store path、max files/bytes、retention | Artifact 存储、收集和保留边界 | 9.6、13 |
| Workspace Configuration | store path、文件数和容量上限 | Task Workspace 的根目录与部署级硬配额 | 9.2、13 |
| Sandbox Configuration | provider、images、resource limits | Docker runtime 和隔离执行资源边界 | 9.5、13 |
| API Boundary Configuration | CORS、`api_allow_remote` | 浏览器来源和默认仅 loopback 访问的 API 边界 | 12、13 |
| Trusted Provider Configuration | provider-to-digest allowlist | 允许进入 Tool Catalog 的 provider 身份集合 | 7.2、13 |
| Permission Bundle Signing Secret | signing secret | 创建和验证无人值守 Permission Bundle 的部署密钥 | 6.1、8.6、13 |
| OpenSpec Change Data | proposal、design、specs、tasks 及完成状态 | 需求、设计决策和实现任务的规划事实 | 1、16.6 |

### 18.6 章节到数据的反向导航

| 章节 | 主要数据 |
|---|---|
| 1–4 | 系统边界、Task、Run、Plan、AgentState、Permission、Workspace、Artifact |
| 5 | 全部核心持久化实体及其聚合关系 |
| 6 | goal、Run profile、TaskContract、PlanGraph、Decision、Observation、终态 |
| 7 | ToolSpec、Tool Catalog、ActionEffectPlan、ToolExecutionContext |
| 8 | identity、PermissionRequest/Decision、ApprovalRequest、Grant/Lease、Permission Bundle |
| 9 | Workspace、manifest、file/change/checkpoint、SandboxJob、Artifact |
| 10 | Reasoning Policy、预算、AgentState、ReflectionPatch、idempotency key |
| 11 | RunEvent、RunView、ProcessStreamState、answer delta、pending approval、审计视图 |
| 12 | REST/SSE 载荷、Run snapshot、错误 envelope、usage data |
| 13 | Settings 中的模型、工具、网络、存储、沙箱和 API 配置 |
| 14 | 需要跨数据结构持续成立的安全不变量 |
| 15 | 数据结构发生变化时必须同步的测试与代码位置 |
| 16 | Credential、Delegation、DataFlowState 等尚需扩展的数据 |
| 17 | 各类数据的推荐源码阅读入口 |
