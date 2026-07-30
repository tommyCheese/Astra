# 深度记忆、AutoDream 与 Agent 自进化运维

本文描述 Astra 当前已经实现的深度记忆基础、后台 AutoDream consolidation 和受治理的 Agent evolution candidate。它们共享来源审计与删除传播，但具有不同的信任等级：

```text
Run / Turn / ToolCall / Artifact（事实来源）
                  │
                  ▼
        Memory candidate → active version
                  │
          bounded retrieval / feedback
                  │
                  ▼
       AutoDream consolidation proposal
                  │
        manual publish / audited rollback
                  │
                  ▼
        Evolution candidate + offline eval
                  │
            explicit approve only
                  ╳
       production promotion is disabled
```

Memory、AutoDream 输出和 evolution candidate 都是非授权数据。它们不能启用 Tool、扩大权限、提供凭据、跳过审批、降低 sandbox/retention/security 下限，也不能直接改写运行中的 Skill、Profile 或路由策略。

## 1. Memory 命名空间与生命周期

每个 Memory 版本同时拥有语义 `scope` 和不可为空的物理命名空间：

| 类型 | `namespace_id` | 跨 Session 行为 |
|---|---|---|
| `run` | Run ID | 仅当前 Run 可见 |
| `task` | Task/Conversation ID | 同一 Conversation 的后续 Run 可见 |
| `workspace` | 明确的 Workspace ID | 只有携带相同非空 Workspace 身份的 Run 可见 |
| `user` | 明确的创建者 ID | 只有携带相同非空用户身份的 Run 可见 |

缺失 Workspace 或用户身份时，系统拒绝相应持久化写入，不会退化到共享的空命名空间。旧数据迁移时，无法安全映射的记录保留为隔离的 Run Memory。

支持的类型为 `semantic_fact`、`user_preference`、`episodic_experience`、`procedure`、`failure_pattern` 和 `evaluation_feedback`。生命周期为：

```text
candidate → active → superseded | revoked | expired | quarantined
    └──────────────→ quarantined | revoked
quarantined → candidate | revoked
```

内容更新创建同一 `memory_key` 的新不可变版本；旧版本进入 `superseded`，不会原地覆盖。状态变更使用 `expected_state_version` 做乐观并发检查。

## 2. 提取、召回与审计

模型提取结果先经过规范化：限制 scope/kind、生成或验证稳定 key、裁剪置信度和重要度、丢弃未知类型，并强制从 `candidate` 开始。运行时根据真实 Run/Task 身份派生命名空间，模型不能自行指定租户身份。包含 permission、credential、approval、system prompt、sandbox 或 Tool allowlist 等受保护字段的候选会被拒绝；单个候选失败不会让整个 Run 失败。

召回先执行硬过滤，再评分：

1. 命名空间和来源可访问性；
2. `active` 生命周期、有效时间、TTL 和撤销状态；
3. 类型、最低置信度、provenance 和必需结构化 tag；
4. Latin/CJK 规范化分词与 lexical overlap；
5. kind/tag、recency、confidence、importance、受限 utility；
6. 可选的批量 semantic scorer；
7. 稳定排序以及 item、字符和 token 完整条目预算。

注入模型的 `memory_context` 被明确标为 `untrusted_memory_data`、`authority: none`。AgentTurn 只审计 Memory ID、版本、命名空间和分数组件，不复制原始内容。召回事件保存查询 SHA-256 指纹、policy version、候选/选择/排除原因和 shadow 标志，不保存查询明文。

## 3. Rollout flags

默认配置保持旧读取路径，不开启跨 Session 注入，也不启动 AutoDream：

```text
AGENT_MEMORY_WRITE_ENABLED=true
AGENT_MEMORY_CROSS_SESSION_ENABLED=false
AGENT_MEMORY_CROSS_SESSION_SHADOW=false
AGENT_MEMORY_RETRIEVAL_POLICY_VERSION=memory-retrieval-v1
AGENT_MEMORY_RETRIEVAL_CANDIDATE_LIMIT=100
AGENT_MEMORY_RETRIEVAL_MAX_ITEMS=8
AGENT_MEMORY_RETRIEVAL_MAX_CHARACTERS=8000
AGENT_MEMORY_RETRIEVAL_MAX_TOKENS=2000
AGENT_MEMORY_RETRIEVAL_MIN_CONFIDENCE=0.2
AGENT_MEMORY_RETRIEVAL_MIN_SCORE=0.05
```

推荐发布顺序：

1. 保持两个 cross-session flag 关闭，只写新 schema 与审计；
2. 设置 `AGENT_MEMORY_CROSS_SESSION_SHADOW=true`，计算并记录选择但不注入；
3. 检查 namespace leakage、stale use、negative transfer、token/latency；
4. 关闭 shadow 并设置 `AGENT_MEMORY_CROSS_SESSION_ENABLED=true`；
5. 出现回归时立即关闭 enabled；数据和审计无需回滚。

## 4. AutoDream consolidation

`AUTODREAM.md` 只会进入绑定了 consolidation job ID 的后台模型操作；同步 Run 无法选择该操作。Job 冻结输入 Memory ID、版本、来源、命名空间、Profile 摘要和规范化哈希。确定性去重不需要模型；如果以后允许模型调用，其输出仍受大小、schema、来源覆盖、命名空间、版本、指令隔离和受保护 authority 校验。

配置：

```text
AGENT_MEMORY_AUTODREAM_ENABLED=false
AGENT_MEMORY_AUTODREAM_SCAN_SECONDS=3600
AGENT_MEMORY_AUTODREAM_COOLDOWN_SECONDS=86400
AGENT_MEMORY_AUTODREAM_MIN_CANDIDATES=2
AGENT_MEMORY_AUTODREAM_MAX_RECORDS_PER_JOB=100
AGENT_MEMORY_AUTODREAM_MAX_MODEL_CALLS=0
AGENT_MEMORY_AUTODREAM_LEASE_SECONDS=120
AGENT_MEMORY_AUTODREAM_BATCH_SIZE=4
```

`MAX_MODEL_CALLS=0` 表示只执行确定性 consolidation。后台 worker 使用数据库 lease、idempotency key、冷却期、批次和单 Job 故障隔离；启动时恢复过期 lease。即使调度关闭，本机管理员仍可显式创建并审查 Job。

Job API：

- `POST /api/memory/consolidation/jobs`
- `GET /api/memory/consolidation/jobs`
- `GET /api/memory/consolidation/jobs/{id}`
- `POST /api/memory/consolidation/jobs/{id}/publish`
- `POST /api/memory/consolidation/jobs/{id}/rollback`

Publish 在同一事务内创建新 generation、建立来源与 supersession，并使旧版本失效。Rollback 同样要求期望状态版本和原因，只恢复该 Job 的受控变更，不重写原始 Run。

## 5. Evolution candidate

当前实现支持不可变的 procedure 和受限 policy recommendation candidate、来源记录、离线 evaluation manifest、CAS review 与 rollback metadata。评估必须包含 baseline/candidate 对照、代表性和 held-out case、最小样本、安全指标、成本与延迟阈值。

API 前缀为 `/api/agent-evolution/candidates`，包含创建、列表、详情、附加评估、approve、reject、promotion 和 rollback。所有响应都返回：

```json
{
  "executable": false,
  "production_promotion_enabled": false
}
```

Shadow、Canary 和 promoted 请求始终故障闭合并返回 `EVOLUTION_PROMOTION_DISABLED`。Approved 只表示通过离线治理审查，不影响服务中的 Prompt、Skill、Tool、权限、调度或模型路由。

## 6. 管理与审计 API

Memory 管理 API：

- `GET /api/memories`
- `GET /api/memories/{id}`
- `POST /api/memories/{id}/revoke`
- `POST /api/memory-recalls/{event_id}/feedback`

列表支持 lifecycle、kind、run 和显式 namespace 过滤；详情包含来源、版本历史、召回分数和 lifecycle audit。撤销与 feedback 都有界并可审计。Astra 默认只允许 loopback 访问 `/api`；在完整账号/组织授权模型落地前，不应把本地管理 API 直接暴露到公网。

前端的“记忆”工作台提供 Memory、AutoDream 和自进化三个面板。所有来自 Memory/candidate 的文本都按数据渲染，不执行 HTML 或指令；生产晋升控件固定禁用。

## 7. 删除、过期与恢复

Conversation 删除在删除 Run/Turn/Artifact 之前解析 `memory_sources` 和 `agent_evolution_sources`：

- 有其他独立来源的 Memory/candidate 移除已删除来源并保留；
- 失去全部来源的 active Memory 先进入 `revoked`；
- 失去全部来源的 draft/evaluating/approved candidate 先进入 `rejected`；
- rollout candidate 会记录 rollback 状态；
- source row 和召回事件随后在同一删除事务内处理。

查询时始终检查 TTL 和 source accessibility，因此正确性不依赖后台清理。`materialize_expired()` 只把已过期的 active row 批量落为 `expired` 并写 audit。

## 8. 评估与回滚门槛

固定夹具位于 `backend/tests/fixtures/deep_memory_retrieval_cases.json`，同时比较：

- `no_memory`
- `legacy_recency`
- `cross_session`
- `consolidation`

报告记录 precision/recall、task success、token cost、latency、stale use、harmful feedback 和 namespace leakage。启用 active recall 前至少要求 namespace leakage 为零，且 task success/relevance 相比 legacy 有稳定改善；任何删除传播失败、安全回归或显著 negative transfer 都应关闭 active flag。

数据库升级：

```bash
cd backend
alembic upgrade head
alembic heads
```

回滚服务行为优先使用 feature flag 和 Job rollback，不建议直接 downgrade 已写入新 Memory 的数据库。迁移是 additive 的，保留旧 Run 读取兼容。

## 9. 当前延期项

首版使用关系数据库过滤与确定性评分，不要求 vector database 或 graph database。以下工作明确延期：

- embedding 生成、向量索引和语义召回实现；
- Graph Memory 节点/边投影与图查询；
- 跨用户、跨 Workspace 或组织级共享；
- 自动执行 approved evolution candidate；
- 在线训练、模型权重更新或服务内自改写；
- 自动 Shadow/Canary/production promotion。

这些能力必须复用当前 namespace、source、lifecycle、evaluation 和 deletion contract，而不能绕过它们。
