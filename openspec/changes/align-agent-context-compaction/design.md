## Context

Astra 目前存在三种彼此割裂的上下文行为：

1. `ConversationContextManager` 在创建 Run 前按字符估算容量，把旧 Run 的 `conversation_goal` 与 `summary` 拼接后保留末尾固定字符；它没有重新理解完整历史，也不能在 Run 内压缩。
2. root Agent loop 在 standard/trusted 路径中持续累积 observations，没有 pre-model/post-tool 的窗口滚动。
3. child 已有最小 `ContextManifest` 和 `SubagentContextCheckpoint` schema，但 executor 不调用压缩服务，`local_summary/local_facts` 也没有进入下一次模型上下文；恢复 checkpoint 仍保存完整 observations。

本设计采用截至 2026-08 的公开实践作为方向依据：

- [OpenAI 对 Codex 长循环的说明](https://openai.com/index/equip-responses-api-computer-environment/) 验证了在模型调用前和工具循环中进行语义 checkpoint、保留必要状态并开启新窗口的价值；Astra 只借鉴其生命周期和窗口形态，不采用 OpenAI 专有压缩端点或参数。
- [LangChain SummarizationMiddleware](https://docs.langchain.com/oss/python/langchain/middleware/built-in) 把 trigger、keep 和摘要模型抽象成可复用中间件，使每个 Agent 独立管理窗口。
- [Anthropic multi-agent research](https://www.anthropic.com/engineering/multi-agent-research-system) 使用独立子上下文、阶段摘要、外部 Memory 和 artifact-first 交换，避免父子之间复制大结果。

Astra 需要对齐这些原则，同时保持自身的治理约束：数据库中的 TaskContract、Plan、AgentState、Permission、Artifact 和 Evidence 才是规范事实；模型摘要是有损的模型输入，不得成为授权或完成证明。

## Goals / Non-Goals

**Goals:**

- 为 conversation、root execution 和 child execution 提供同一套 Token 预算、触发、压缩、安装、恢复和审计框架。
- root 使用 Astra 管理的结构化语义摘要达到与 Codex 相同的长工具循环窗口滚动能力，同时保持 Provider 无关。
- child 使用独立窗口和专用 checkpoint，只保留委派连续性需要的信息，维持父子/兄弟隔离和权限衰减。
- 保留少量近期原始上下文与完整审计历史，降低多次摘要的信息损失。
- 对大型工具输出执行先截断/外置、后压缩，并用稳定 Artifact/Evidence 引用保持可恢复性。
- 让压缩在并发、取消、进程重启、模型切换和 checkpoint schema 升级时安全、幂等、可观察。

**Non-Goals:**

- 不保存、恢复或展示隐藏 chain-of-thought。
- 不让 checkpoint 替代 TaskContract、Plan、权限、预算、Evidence、ToolCall 或 Completion Gate 的规范记录。
- 不把父 Agent 的完整会话或私有 scratchpad复制给 child。
- 不保证模型生成摘要可逆或逐字还原历史；完整历史继续由审计存储承担。
- 不在本变更中引入向量数据库或把压缩 checkpoint 自动晋升为长期 Memory。
- 不调用或存储任何 Provider 专有 compaction 参数、端点、trigger、opaque/encrypted item 或返回格式。

## Decisions

### 1. 一个共享压缩引擎，三个角色策略

新增 `AgentContextCompactionService`，接收标准化 `ContextEnvelope` 与 `CompactionPolicy`：

```text
ContextEnvelope
├── protected_prefix       # 从规范状态重新构造，永不由摘要替代
├── prior_checkpoint       # 上一窗口的累积语义状态
├── compactable_body       # 消息、observations、旧 Run 投影
├── recent_tail            # 近期原始内容
├── reference_manifest     # Artifact/Evidence/ToolCall 稳定引用
└── accounting             # window、usage、budget、window number
```

策略分为：

- `conversation`: 保护当前用户请求、模型身份和平台指令；摘要跨 Run 的用户意图、决策、结果和未决事项。
- `root_execution`: 保护当前请求、TaskContract、有效 Profile/Skill identities、权限、Plan/AgentState 版本、预算与 Completion Gate；压缩 root observations 和历史决策输入。
- `child_execution`: 保护 DelegationContract、role protocol、衰减权限/Catalog digests、Workspace scope、局部 Plan、预算和终止条件；压缩 child-local observations。

共享引擎统一 Token、生命周期和存储，角色策略拥有不同 schema、保留优先级和预算。替代方案是让 root/child 共用一段自由文本摘要；拒绝，因为会混淆全局与局部事实、破坏权限隔离并使恢复不可验证。

### 2. 使用“受保护前缀 + checkpoint + 近期原文”的 Memento 形态

每次模型请求由三部分构成：

```text
canonical protected prefix
+ cumulative semantic checkpoint
+ recent raw tail
```

受保护前缀每次从数据库规范状态重新构造，不从旧 prompt 复制。checkpoint 明确标记为模型生成的有损 continuation data。近期原文按 Token 倒序选择，保持时间顺序后注入：root/conversation 默认上限 64K tokens，child 默认 8K tokens，同时不得超过可用输入窗口的 20%；均可由部署策略调低。

重复压缩把 prior checkpoint 与本窗口新增 body 一起输入，生成新的累积 checkpoint；不得简单丢弃上一 checkpoint，也不得无限嵌套摘要。替代方案是只保留摘要；拒绝，因为最近工具错误、用户修正和精确参数最容易在纯摘要中损失。

### 3. 压缩协议完全由 Astra 拥有

所有 Provider 使用同一条调用路径：

```text
Astra ContextEnvelope
→ Astra role-specific compaction prompt
→ existing generic model generation call
→ Astra text/JSON extraction and repair
→ Astra schema/reference/security validation
→ Astra checkpoint installation
```

模型客户端只需暴露已有的普通生成能力、模型上下文窗口和 usage；压缩调用不得发送 Provider 专有 compaction 参数，不得调用专用 compact endpoint，也不得接受 opaque/encrypted compaction item 作为 Astra 状态。

root、conversation 与 child 分别使用版本化 prompt template 和 JSON schema。Astra 首先尝试解析模型返回的纯 JSON或 fenced JSON，并执行有限的本地语法修复；Provider 支持 JSON schema/structured output 时可以作为普通生成质量优化，但实现 MUST NOT 依赖该参数，关闭后行为仍须正确。默认使用当前活动模型，部署方可显式配置普通 compaction model route，但必须记录模型身份、数据驻留、预算和输出质量。

如果普通摘要调用失败或输出无法通过校验，Astra 可构造 `deterministic_emergency` checkpoint：只从规范状态、已验证引用和有界的规范化 observations 提取强类型字段，不推断新事实。该降级 checkpoint 仍须满足角色 schema、隔离与恢复水位；否则走容量错误，不静默删除历史。

所有 checkpoint 都是 Astra JSON 数据，因此 Provider 或模型切换只需重新计算窗口预算，不需要 compatibility hash 或专有格式转换。

### 4. root 与 child 使用不同 checkpoint schema

`RootContextCheckpointV2`：

```text
user_intent
current_constraints
key_decisions
verified_facts[{text, evidence_refs}]
global_progress[{criterion_or_node, status}]
workspace_changes[{artifact_or_path_ref, summary}]
child_results[{execution_id, result_ref, summary}]
recent_failures[{fingerprint, disposition}]
open_issues
next_steps
```

`ChildContextCheckpointV2`：

```text
agent_execution_id
manifest_hash
contract_hash
local_progress
completed_steps
local_facts[{text, provenance_ref, confidence}]
evidence_refs
artifact_refs
recent_failures
open_issues
next_action
remaining_budget
continuation_answers
```

生成后必须做 schema、长度、引用存在性、child 数据标签/用途、contract/manifest hash 和 forbidden-context 校验。模型声明的事实只有在引用已接受 Evidence 或规范状态已包含时才进入 `verified_facts`；其他内容保持 `local_facts` 或不可信摘要。父级合并仍通过现有 fan-in/promote 流程。

### 5. Token 计量和触发与 Codex式长循环对齐

可用输入预算为：

```text
usable_input = context_window - output_reserve - compaction_output_reserve
```

优先使用普通模型响应返回的 usage 或已配置 tokenizer；缺失时使用 Astra 估算器并标记 `estimated=true`。每个窗口记录 `prefill_tokens`，支持两种 scope：

- `total`: 全部活动上下文计入阈值。
- `body_after_prefix`: 只计算受保护前缀和上一 checkpoint 之后的增长，同时完整窗口仍是硬上限。

默认在 80% usable input 触发，保留现有部署配置；检查点位于：

- conversation：创建 Run 前及手动 `/compact`。
- root/child：每次模型调用前、每次工具结果规范化/外置后、恢复后第一次模型调用前。
- 模型或 Provider 改变、切换到更小窗口时：用相同 Astra JSON checkpoint 重新计算预算，并在超阈值时通过当前通用模型路径再次压缩。

压缩后必须低于目标恢复水位，默认 55%；否则减少 recent tail 并重试一次。不得在没有为摘要输出预留空间时等到硬窗口溢出才触发。

### 6. 工具结果先做内容治理，再参与压缩

工具适配层为结果计算 Token 和字节大小。超过 inline 限额时：

1. 完整内容写入受权限控制的 Artifact/Evidence/ToolCall output storage；
2. observation 仅保留状态、工具、关键字段、截断预览、checksum、稳定引用和错误分类；
3. compactor 读取规范化 observation，不把任意大 payload 复制进摘要请求；
4. child 只能保留其 identity 可访问且用途匹配的引用。

这延续 Astra artifact-first 设计，也避免用模型摘要承担原始数据保存职责。替代方案是在溢出后从最旧工具结果开始静默删除；拒绝，因为会丢失来源并破坏审计。

### 7. 压缩安装是版本化、幂等的条件提交

压缩调用可能耗时，不能持有数据库事务。流程为：

```text
snapshot(state_version, window_number, input_digest)
→ emit compaction.started
→ call generic model generation outside transaction
→ validate output and post-compaction budget
→ compare-and-swap install checkpoint
→ emit compaction.completed
```

幂等键为 `(owner_type, owner_id, window_number, input_digest, policy_version)`。若期间出现新 observation、取消 epoch 变化或 checkpoint 被其他 Worker 安装，当前结果标记 superseded，不覆盖新状态。持久化 checkpoint 保存 source item IDs、policy/schema version、生成模型身份、Token 前后值和 recent-tail boundary。

恢复时先校验 schema、policy compatibility、manifest/contract/catalog digests 和引用可访问性。所有 checkpoint 均可由 Astra直接读取；损坏或版本不兼容时，从完整审计历史与 ContinuationManifest 重新生成，而不是直接丢弃状态。

### 8. 失败时保守降级，不静默丢历史

压缩失败分三层处理：

- 软阈值以下：记录失败并继续当前窗口，下一安全边界重试。
- 已过软阈值但仍有输出空间：截短 recent tail/超大工具预览后重试一次，模型摘要失败可切换 deterministic emergency 路径。
- 接近硬上限且无法形成有效 checkpoint：root Run 返回分类容量错误或进入安全 waiting/blocked；child 返回 budget-limited/blocked 的结构化结果；不得发送必然溢出的模型请求。

checkpoint 校验失败不会安装；原活动历史和审计记录保持不变。连续压缩必须设置每 turn/window 上限，防止摘要仍超限导致循环。

### 9. 压缩质量是可测试的运行时契约

除单元测试外建立 deterministic fixture 与模型 eval：

- 关键用户约束、否定决策、当前计划、已验证事实、精确路径/参数、未决事项的保留率；
- forbidden parent/sibling/private content 在 child checkpoint 中为零；
- Evidence/Artifact 引用存在、可访问、checksum 匹配；
- 压缩后 Token 达到恢复水位；
- 连续 3 次以上压缩仍能完成长程任务；
- Provider switch、模型 downshift、进程恢复、并发压缩和取消不重复工具副作用；
- 与旧字符折叠基线比较任务成功率、成本、延迟和摘要遗漏率。

上线门槛以任务连续性和关键字段保真为主，不以摘要文字相似度作为唯一指标。

## Risks / Trade-offs

- [模型摘要会遗漏或改写细节] → 保留近期原文、结构化 schema、规范状态重注入、引用校验和完整审计历史；用任务连续性 eval 门控。
- [不同模型不稳定地输出 JSON] → Astra 本地提取、有限修复、严格 schema 校验和 deterministic emergency；从不依赖 structured-output 专有参数。
- [额外模型调用增加成本和延迟] → 提前外置大结果、按窗口而非每轮摘要、独立计量和缓存幂等结果。
- [protected prefix 自身过大] → Profile/Skill/Tool 使用 progressive disclosure 和稳定引用；若强制前缀无法放入窗口则 fail closed，而不是摘要权限或契约。
- [child 摘要把局部事实错误提升给父级] → checkpoint 与 fan-in promotion 分离，只有已验证引用可进入共享事实。
- [并发 Worker 覆盖新 checkpoint] → state version、cancellation epoch、input digest 和 CAS 安装。
- [旧摘要迁移后携带低质量信息] → 标记 legacy/unverified，仅作为首次 V2 压缩输入；不自动转换为 verified facts。
- [不同 Provider Token 计量差异] → 优先真实 usage，估算值带来源与安全余量，硬上限始终采用 Provider/model catalog 最小可信值。

## Migration Plan

1. 增加 V2 checkpoint schema、压缩事件和数据库字段；旧 reader 保持可用。
2. 实现共享 Token accounting、ContextEnvelope 和 policy，但先以 shadow mode 计算触发与预期压缩量，不改变模型输入。
3. 接入工具输出外置/截断和 root/child pre-model、post-tool 安全边界。
4. 启用 Astra 管理的通用模型摘要与 deterministic emergency 路径，先用于测试 Provider和内部 eval。
5. 将 conversation `/compact` 与自动折叠切换到 V2；首次读取 V1 `summary/folded_run_ids` 时构造 `legacy_summary` 输入并写入 V2，不删除旧字段和 Run。
6. 按 feature flag 分别启用 root、standard、trusted、quick child、trusted child；比较成功率、Token、成本和延迟。
7. 达到门槛后停止写 V1 字符摘要，保留回读与回滚能力至少一个发布周期。

回滚时关闭 V2 feature flags，继续读取完整审计历史并恢复 V1 conversation 投影；V2 checkpoint 保留但不注入。不得通过回滚删除 V2 期间产生的 Run、Turn、ToolCall、Artifact、Evidence 或 AgentExecution。

## Review Decisions Based on the Current Implementation

- recent tail 已按 `min(configured, 20% usable_input)` 计算；64K 只是配置上限，不是所有模型的固定保留量。最终默认值仍由 10.6 模型族评测校准。
- 默认继续使用当前活动模型的通用生成端口。独立 compaction model route 只有在补齐最低能力、数据驻留、成本归属和 fallback 策略后才能开放。
- deterministic emergency 按角色策略显式启用，并且只在 protected prefix 与 verified facts 足以安全继续时安装；不得用它摘要权限、契约或未知事实。
- UI 只展示经过安全裁剪的摘要与审计元数据，不直接渲染模型 continuation checkpoint。
- Subagent checkpoint schema 已进入现有 common/application 边界；本变更直接兼容当前 `ContextManifest`、contract/manifest hash 与 continuation 字段，不再依赖旧变更的归档顺序。
