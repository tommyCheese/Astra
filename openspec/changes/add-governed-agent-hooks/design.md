## Context

Astra 当前已经拥有四块可复用基础：Run/Event 持久化与 SSE、固定阶段的 `InvocationPipeline`、统一 `PermissionEngine`、以及带来源/版本/摘要的 Tool Provider Plugin Catalog。但这些机制只服务核心代码和工具贡献点，外部自动化无法在生命周期边界获得稳定输入、表达限制性决策、被 Run 快照冻结，或在崩溃后安全恢复。现有安全规范虽然已经把 Hook 列为扩展供应链对象，运行时却尚无 Hook contract、registry、dispatcher 或 execution record。

截至 2026-08，公开实现呈现出几条成熟方向：

- [Claude Code Hooks](https://code.claude.com/docs/en/hooks) 已提供 session、prompt、tool、permission、compaction、subagent、task 和 stop 等细粒度事件，支持 command、HTTP、MCP、prompt、agent handler，结构化 JSON 结果、matcher、并行执行、异步执行与 managed-only policy。它也暴露了需要规避的风险：shell 与宿主同权限、不同 transport 的失败语义不一致、项目 Hook 可能被 Agent 自行修改。
- [VS Code Agent Hooks](https://code.visualstudio.com/docs/agent-customization/hooks) 采用相近的 PascalCase 事件和 stdin/stdout JSON 协议，并明确支持本地、后台与云 Agent；这说明常用事件名和 command-hook 配置正形成互操作惯例，但其预览实现仍存在 matcher 兼容差异，不能直接作为 Astra 的规范语义。
- [OpenAI Agents SDK lifecycle hooks](https://openai.github.io/openai-agents-python/ref/lifecycle/) 区分 Run 级与 Agent 级 hook，并在 agent、LLM、tool、handoff 的 start/end 边界传递强类型上下文；适合借鉴作用域与上下文形态，但其回调默认是应用内代码，不足以覆盖 Astra 的多租户隔离、授权和恢复要求。
- [LangChain middleware](https://docs.langchain.com/oss/python/langchain/middleware/custom) 明确了 before 顺序、after 逆序、wrap 嵌套、状态更新与可组合 guardrail；适合参考组合模型，但 Astra 不采用任意 middleware 包裹核心调用，因为这会让安全阶段和恢复点变得不可判定。
- [Kubernetes admission webhook](https://kubernetes.io/docs/reference/access-authn-authz/extensible-admission-controllers/) 的短超时、显式 failure policy、匹配规则、mutation 后重新验证与有限 reinvocation 是受治理 admission 的成熟范式。
- [CloudEvents](https://github.com/cloudevents/spec/blob/main/cloudevents/spec.md) 用 `specversion`、`id`、`source`、`type` 和可选 schema 描述可去重事件；[OpenTelemetry SpanProcessor](https://opentelemetry.io/docs/specs/otel/trace/sdk/) 则将同步 start/end 处理与异步批量导出分开，并要求处理器并发安全和有界 flush。

因此，Astra 不应只增加一个 `callbacks: list[Callable]`，也不应照搬编辑器产品中“项目脚本直接继承宿主权限”的模型。需要把 Hook 作为受版本、身份、权限、数据标签、预算、恢复与审计共同治理的运行时能力。

## Goals / Non-Goals

**Goals:**

- 为 Run、prompt、model、tool、approval、compaction、subagent 和 completion 提供稳定、版本化、可测试的生命周期事件。
- 同时支持低延迟的限制性 admission 和可靠的异步 observation，而不把通知故障变成主执行故障。
- 让所有 Hook 来源、配置、handler、决策和副作用可追踪、可冻结、可恢复、可禁用和可重放。
- 保持 Permission Engine 是唯一最终授权者，Hook 只能维持或缩小既有权限。
- 为 Claude Code/Copilot 风格的常用 command hooks 提供安全的显式导入路径，而非 Workspace 自动执行。
- 在未配置 Hook 时保持当前运行时结果、事件顺序和延迟基本等价。

**Non-Goals:**

- 第一阶段不支持 prompt/LLM 判定型或 agent/subagent 型 Hook handler。
- 不让 Hook 注册新工具；新增工具仍由 Tool Provider Plugin 系统负责。
- 不允许 Hook 替换 Policy Gate、Permission Engine、Effect Analyzer、Catalog Freeze、Completion Gate 或规范数据库状态。
- 不保证完整兼容任一外部产品的全部事件、matcher、退出码和环境变量；兼容层只导入可安全映射的子集。
- 不提供公开 Hook 市场、自动下载或从 Task Workspace 导入可执行代码。
- 不把 Run Event 流直接当作对外可靠投递队列；可靠 observation 使用独立 outbox。

## Decisions

### 1. 将 Hook 建模为独立贡献面，而不是 Tool Plugin 或任意 middleware

新增版本化 `HookManifest` 与不可变 `HookBinding`：

```text
HookManifest
├── hook_id / version / digest / protocol_version
├── source_identity / trust_tier / enabled
├── event_type / event_schema_version
├── mode                  admission | observation
├── selector              tool/capability/agent/scope/status labels
├── handler               managed | isolated_command | http
├── decision_capabilities deny | ask | patch_input | add_context | observe
├── failure_policy / timeout / output_limit
├── data_access / effect_ceiling / credential_refs
└── priority / rollout / configuration_revision
```

Hook Catalog 复用 Plugin Catalog 的 verified source、digest、lifecycle 和确定性装配基础，但保持独立 registry 和接口。Tool Plugin 贡献“模型可选择的能力”，Hook 订阅“宿主已经定义的生命周期”；把 Hook 塞进 `PluginContribution.result_processors` 会遗漏 prompt、model、subagent 和 completion 边界，也会错误暗示 Hook 可继承工具权限。

替代方案是通用 wrap middleware。拒绝原因是任意包裹会改变关键阶段相对顺序，使 Effect 冻结、审批暂停和 exactly-once 恢复无法建立稳定不变量。

### 2. 事件面采用固定语义目录和 CloudEvents-inspired envelope

第一版事件目录：

```text
run.before_start          run.started
prompt.before_accept      prompt.accepted
model.before_request      model.responded | model.failed
tool.before_authorize     tool.execution_started
tool.succeeded            tool.failed | tool.blocked
approval.requested        approval.decided
context.before_compact    context.compacted | context.compaction_failed
subagent.before_start     subagent.started | subagent.stopped
run.before_complete       run.completed | run.failed | run.cancelled
```

每个 `HookEventEnvelopeV1` 至少包含：`spec_version`、`event_id`、`event_type`、`event_schema_version`、`source`、`occurred_at`、`correlation_id`、`causation_id`、`trace_id`、Run/Task/Conversation/AgentExecution 引用、attempt、scope、payload、data labels 与可访问 reference manifest。`source + event_id` 在生产者范围唯一；重投保持 event ID，使 handler 能去重。

payload 是事件专用的最小投影，不提供 transcript 文件路径、数据库 session、全局 repository 或任意宿主对象。大内容通过带权限的 Artifact/Evidence/ToolCall reference 访问；handler 只有 manifest 声明且当前 principal 获准的数据标签。

事件 schema 只做向后兼容增加时保留版本；删除、改名或改变决策语义必须升级 major schema。未知字段可忽略，未知 major 版本不得执行 admission handler。

### 3. 同步 admission 与异步 observation 使用两条执行路径

```text
                        ┌─ admission dispatcher ─ decision aggregate ─ core action
canonical occurrence ──┤
                        └─ transactional outbox ─ delivery worker ─ observers
```

- `admission` 在核心动作提交前同步执行，只允许事件目录明确授权的决定；不自动重试，使用短超时，并在调用外部代码时不持有数据库事务。
- `observation` 只接收已发生事实，不能改变该事实；与规范状态在同一事务写入 outbox，提交后异步投递，支持有界指数退避、幂等、dead-letter 和授权重放。
- 同一 manifest 不得把 observation 事件声明为阻塞型，也不得让 admission 在超时后继续后台返回迟到决定。

这比一个统一 event bus 更清晰：安全决策需要确定的截止点，通知/审计需要可靠投递但不能扩大关键路径延迟。

### 4. Admission 结果采用限制性 lattice，而不是“最后一个返回值获胜”

统一 `HookAdmissionResultV1` 可表达：

- `continue`：不增加额外限制；不是权限 allow。
- `deny`：拒绝当前动作并返回安全 reason code。
- `ask`：要求 Permission Engine/审批流程进入交互或 unattended fail-closed。
- `patch_input`：仅在允许的 pre-input 事件提出 RFC 6902 子集补丁。
- `add_context`：追加带来源、数据标签、用途和 token 上限的非规范上下文。
- `stop_run`：只在允许的 Run/prompt/completion 边界终止或阻止完成。

组合优先级为平台/managed deny > 其他 deny > ask > accepted patch/context > continue。Hook 的 `allow` 兼容值规范化为 `continue`，不能覆盖 Permission Engine。多个补丁按确定顺序应用；若两个 handler 对同一 JSON Pointer 产生不兼容写入，当前 admission fail closed，而不是静默 last-write-wins。

`run.before_complete` 可阻止完成并附加结构化 remediation observation，但每个 Run/Hook 的 completion block 有次数上限；达到上限后进入可解释的 blocked/failed 状态，禁止无限 Stop 循环。

### 5. 工具 Hook 位于解析之后、Effect 授权之前，补丁必须重新走完整安全管线

当前 Invocation Pipeline 调整为：

```text
resolve + input schema validate
→ tool.before_authorize admission
→ apply accepted patch (at most one mutation round)
→ schema validate again
→ trusted effect analysis + freeze effect hash
→ Permission Engine authorize / ask / deny
→ execute
→ persist result
→ tool.succeeded|failed outbox
```

Hook 看见规范化 ToolSpec 身份和候选输入，但不能看见未授权凭据。若补丁改变输入，旧的 candidate digest、Effect Plan、approval preview 和授权结果全部作废。补丁完成后不再次触发同一 pre-hook，避免递归；宿主记录原输入摘要、补丁来源、最终输入摘要和重新分析结果。

`tool.before_authorize` 的 deny/ask 会作为 Permission Engine 的额外约束输入。即使所有 Hook 返回 continue，平台/managed deny 仍然生效。工具执行后 Hook 只能生成 observation、告警或其自身的另一个受授权副作用，不能改写 `ToolResultEnvelope`、Evidence 或 ToolCall status。

### 6. prompt、model、subagent、compaction 与 completion 各自使用窄能力

- `prompt.before_accept`：可拒绝输入或添加有界上下文；不得静默改写用户原文，原 prompt 始终作为规范记录保留。
- `model.before_request`：第一版只可观察 metadata 或添加已标记的 bounded context，不可任意替换 protected prefix、Tool Catalog、权限或消息历史。
- `subagent.before_start`：可 deny/ask 或进一步缩小 DelegationContract；不得增加工具、数据、凭据、预算、网络或深度。
- `context.before_compact`：可发出同步的短时 snapshot/export 通知，但不能改写受保护前缀或 checkpoint；压缩后走可靠 observation。
- `run.before_complete`：可基于已提供的验证摘要阻止完成并提出 remediation，但不能伪造验证成功或直接把 Run 标记 complete。

这些窄能力保留业界常见用例，同时避免把所有事件都变成任意 state mutation API。

### 7. 确定性顺序按信任层、优先级和身份冻结

新 Run 从 application Hook Catalog 解析有效集合并保存 `RunHookSnapshot`，包含 manifest/handler/schema/config digest、selector 编译结果、failure policy、顺序与兼容适配版本。顺序键为：

```text
(trust_tier: platform → managed → user → activated_component,
 priority ascending,
 hook_id,
 version,
 digest)
```

Admission handler 顺序执行，因为补丁和上下文有依赖；纯 observation handler 可在同一 priority bucket 内并行，但最终 execution record 仍按冻结顺序投影。Run 恢复继续使用冻结 binding；安全语义或 handler digest 漂移时，未执行 admission fail closed，observation delivery 使用原冻结版本或进入 dead-letter，不静默切换新版。

不采用 after-hook 逆序的 wrapper 语义，因为 Astra Hook 不是嵌套资源管理器；需要成对清理的 managed handler 应使用同一 correlation ID 管理自身状态。

### 8. handler 后端分为 managed、isolated command 与 restricted HTTP

- `managed`：只允许平台或管理员固定摘要的宿主组件；接收窄接口，不能持有 request-scoped DB session。
- `isolated_command`：默认使用 exec argv 而非 shell 字符串，在独立 runtime/profile 中运行，stdin/stdout 为 JSON；cwd 是只读或显式授权的 Task Workspace projection，环境变量 allowlist，凭据通过短期 reference/broker 注入。
- `http`：只允许管理员策略接受的 HTTPS origin 或受管内网 service identity，启用 DNS/IP 重绑定防护、redirect 禁止/限制、mTLS 或 credential reference、请求/响应上限和 `Idempotency-Key`。

Admission 默认超时 2 秒、平台可上调但硬上限 10 秒；observation 默认 30 秒并由 deployment policy 设置总重试期限。所有输出执行 schema 校验和字节/token 上限；stderr、HTTP body 和诊断先脱敏再持久化。取消 Run 会取消尚未开始的 handler，正在执行的 admission 必须在 deadline 内终止。

第一版不开放 prompt/agent handler，因为它们引入额外模型成本、非确定性决定、递归 Agent 与独立权限/上下文问题；未来可作为独立受治理 handler type 增加。

### 9. failure policy 由事件类别和信任策略约束

每个 binding 的有效 failure policy 是 manifest 请求与平台上限的交集：

- 安全、合规、权限和输入 mutation admission 默认并强制 `fail_closed`；低信任来源不能改为 fail-open。
- 仅提供辅助上下文的 admission 可由管理员设为 `fail_open_with_audit`，失败时不注入任何部分输出。
- observation 固定 `continue_and_retry`；失败不回滚已发生事实。
- handler 明确返回 deny 与 handler 自身故障分开记录；显式 deny 永远按 deny 处理，不受 failure policy 改写。

这借鉴 admission controller 的 failure policy，但避免把它交给不可信 Hook 自行选择。

### 10. Hook 自身副作用使用独立 principal，并禁止递归扩权

每次执行创建 `hook:<hook_id>@<digest>` principal，权限上限为 manifest `effect_ceiling`、来源策略、Run/Task 继承范围和事件允许动作的交集。命令写文件、HTTP egress、通知、Artifact 读取和凭据使用均生成 Effect/PermissionRequest；不能借用触发它的 Agent 或工具 Grant，也不能批准自己的请求。

Hook 派生副作用携带原 event 的 causation ID 和 `hook_depth=1`。这些动作可以产生审计事件，但默认不再触发用户 Hook；平台内部 telemetry 不经过 Hook dispatcher。超过深度、同一 causation 重入或 handler 再注册/启用 Hook 都会被拒绝并审计，防止事件风暴和自修改控制面。

### 11. 持久化执行记录、outbox 与管理面

主要记录：

```text
HookDefinitionRecord / HookVersionRecord
RunHookSnapshotRecord / RunHookBindingRecord
HookExecutionRecord        input digest, decision, duration, status, diagnostics
HookOutboxRecord           event envelope, binding, attempts, next delivery
HookDeadLetterRecord       terminal reason, safe payload ref, replay lineage
```

规范状态变更与 outbox 插入同事务；worker 使用 claim/fencing lease，handler 以 `(event_id, hook_binding_digest)` 作为幂等键。重放创建新的 delivery attempt/lineage，但保持原 event ID，不重做核心动作。数据库不保存 handler 可访问的明文 secret 或未经截断的 stdout。

API/UI 提供：Catalog、来源与摘要、有效能力/failure policy、启停、配置校验、导入 preview、合成事件 dry-run、执行/延迟/失败率、dead-letter、授权重放和 Run 时间线。Dry-run 永不执行真实副作用，只验证匹配、输入 schema、有效权限/failure policy 和模拟结果解析。

### 12. 外部配置通过显式、非执行的兼容导入层进入

兼容适配器读取 Claude Code/Copilot 风格常用事件与 command 配置，映射：

```text
SessionStart       → run.before_start
UserPromptSubmit   → prompt.before_accept
PreToolUse         → tool.before_authorize
PostToolUse        → tool.succeeded
PreCompact         → context.before_compact
SubagentStart/Stop → subagent.before_start / subagent.stopped
Stop               → run.before_complete
```

导入器只产生标准化 preview、无法映射字段的诊断、handler 文件摘要和请求能力，不加载脚本、不继承外部产品的隐式权限，也不承诺相同退出码。用户或管理员必须把 handler 复制/安装到受管不可变存储、选择 runtime profile、审查权限和确认 digest 后才可启用。Task Workspace 中检测到的 Hook 文件只显示为未信任候选；Run 不自动读取。

## Risks / Trade-offs

- [同步 Hook 增加每轮和工具调用延迟] → 短超时、精确 selector、无 Hook fast path、编译索引、仅 admission 顺序执行，并持续记录 p50/p95/p99。
- [补丁组合导致不可预测输入] → 限制 RFC 6902 操作、确定顺序、冲突拒绝、最多一个 mutation round、重新 schema/Effect/授权。
- [项目 Hook 被 Agent 修改后执行] → Workspace 永不作为可执行发现源；安装到受管不可变存储并固定摘要，漂移后重新审查。
- [HTTP/command handler 形成新的 RCE、SSRF 或数据外泄面] → 隔离 runtime、exec argv、网络/环境 allowlist、credential broker、数据标签、Effect ceiling 与统一 Permission Engine。
- [fail-closed Hook 故障造成系统不可用] → 启用前 dry-run/canary、健康状态、部署级 break-glass disable、清晰诊断；安全 Hook 禁用本身是受保护控制面动作。
- [异步投递至少一次造成重复副作用] → event/binding 幂等键、handler contract 要求幂等、fencing lease、可见 replay lineage。
- [事件和执行记录显著增加数据库量] → payload 最小化、引用大内容、分类 retention、批量 outbox 清理；安全审计按现有保留策略治理。
- [兼容导入给用户“完全兼容”错觉] → preview 显示语义差异和未映射字段；只承诺受支持子集，不直接执行源配置。
- [Hook 阻止完成形成无限循环] → 每 Run/Hook block cap、remediation fingerprint 去重、超过上限进入显式 blocked/failed。
- [与进行中的 Tool Plugin/Context Compaction 变更冲突] → Hook 依赖其稳定 Catalog/Invocation/compaction boundary；实现按契约适配，不复制第二条工具或压缩管线。

## Migration Plan

1. 增加 Hook schemas、事件目录、空 Catalog 与无 Hook fast path；用 characterization tests 证明默认行为等价。
2. 增加 definitions、Run snapshots、execution/outbox 表和只读 Catalog/诊断 API；先只注册平台 observation Hook。
3. 实现 reliable observation worker、fencing、重试、dead-letter、重放和 OpenTelemetry 指标，接入 Run/tool/subagent/completion post events。
4. 实现 managed admission dispatcher，先接 `tool.before_authorize` 的 deny/ask，不开放补丁。
5. 增加受限补丁、冲突检测、重新 schema/Effect/授权与 approval resume integrity 测试。
6. 接入 prompt、model、compaction、subagent 和 completion 边界，并实现 block cap 与数据投影。
7. 上线 isolated command 与 restricted HTTP backend；完成 RCE、SSRF、secret、输出炸弹、超时与取消测试后再允许 managed external Hooks。
8. 上线管理 UI、dry-run 和兼容导入 preview；用户/Workspace Hook 默认关闭，管理员策略可要求 managed-only。
9. 以 application/tenant cohort canary 开启，观察 admission latency、block/ask 率、fail-open、dead-letter 和 Run 成功率后扩大范围。

回滚时关闭 external Hook Catalog，只保留空 registry 或平台内建 observation；已冻结 Run 若缺失强制安全 Hook则 fail closed 或由管理员显式取消，不能静默绕过。新增表和事件保留可读，旧 Run 无 Hook snapshot 时按空集合恢复。

## Open Questions

- 第一版是否允许 user-scope HTTP observation Hook，还是仅允许 deployment-managed Hook？建议先 managed-only，稳定后开放带 destination allowlist 的 user scope。
- completion admission 达到 block cap 后默认应标记 `blocked` 等待用户，还是直接 `failed`？建议交互 Run 为 blocked、unattended Run 为 failed。
- 兼容导入是否第一版支持 Claude 的 matcher 正则/permission-rule 子集？建议只支持精确 tool name 与显式列表，其他模式在 preview 中标记需人工转换。
