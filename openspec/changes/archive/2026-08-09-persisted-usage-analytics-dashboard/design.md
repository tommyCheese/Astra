## Context

当前用量弹窗仅根据当前 `RunView` 在浏览器内估算 Token，并把 Agent Turn 当成模型调用次数；模型客户端也没有保留供应商响应中的 usage。Astra 已经持久化 Run、Turn、ToolCall、Memory、SandboxJob 与 Artifact，因此本变更需要补上模型调用事实表，再由后端统一聚合，保证 SQLite、PostgreSQL 和服务重启场景下采用同一统计口径。

## Goals / Non-Goals

**Goals:**

- 每个真实模型 HTTP 请求尝试均形成可审计、可持久化的调用记录。
- 优先使用供应商返回的精确 Token 字段，缺失字段保持未知并暴露覆盖率。
- 提供全部历史、时间范围、当前对话和单次 Run 的统一用量查询。
- 看板只消费后端事实数据，服务重启后可以完整回显已落库记录。
- 保持现有 Agent 结构化输出协议与 Docker 工具沙箱行为不变。

**Non-Goals:**

- 不根据文本长度推算 Token，也不补算历史请求。
- 不在没有持久化价格快照的情况下推算费用。
- 第一版不引入 Redis、时序数据库、OLAP 引擎或预聚合任务。
- 不把已有 Run、ToolCall 等事实复制到通用 usage 表。

## Decisions

### 1. 新增模型调用事实表

新增 `model_invocations` 表，以一次实际 HTTP 尝试为一行，保存 run/turn 关联、provider、model、operation、attempt、status、时间、耗时、供应商 request id、错误信息、原始 usage，以及 input、cached input、output、reasoning、total Token 的规范化可空字段。选择逐尝试记录而不是仅记录最终结果，可准确反映重试带来的调用量和失败率；已有实体继续使用各自事实表，避免双写和口径漂移。

### 2. 在模型客户端边界采集 usage

为模型客户端注入调用记录器。客户端在请求开始、成功、失败时报告同一个调用生命周期；流式 OpenAI-compatible 请求发送 `stream_options.include_usage=true` 并解析终止 usage chunk，非流式响应读取顶层 `usage`。记录器由每个 Run 的执行上下文绑定数据库 Repository，避免客户端依赖 Web 层或前端状态。

未提供的 Token 字段保存为 `NULL`，仅在供应商明确返回 total 时采用其值；否则只在组成字段均已知时计算 total。原始 usage JSON 一并保存，便于未来适配新字段。

### 3. 后端集中定义聚合口径

新增 usage service 和 `/api/usage/summary`。查询参数支持 `scope=all|task|run`、`task_id`、`run_id`、`from`、`to` 和时区。响应包含总览、Token 分类、按日趋势、模型明细、工具明细和数据覆盖率。

模型调用次数来自 `model_invocations`；Agent Turn、工具、记忆、沙箱任务和产物分别从现有表聚合。工具成功率的分母仅为 succeeded + failed，不把 running 计入；当前对话按 task id 聚合其全部 runs。第一版直接查询并聚合事实表，以索引控制成本，数据规模增长后再评估 rollup。

### 4. 看板始终从 API 加载

打开用量界面时立即请求 API，提供全部历史、最近 7 天、最近 30 天、当前对话和当前 Run 范围。界面展示加载、错误、空数据和部分覆盖状态；不再读取当前 Run 计算估算值。供应商未返回 Token 的调用会计入调用数，但 Token 显示为未知/部分覆盖，而非零。

### 5. 中断记录显式处理

已成功或失败的调用在终态立即提交。服务启动时将长期处于 running 的旧记录标记为 interrupted，聚合响应单独呈现，避免把中断误判为失败或成功。

## Risks / Trade-offs

- [供应商 usage 字段差异] → 保留原始 JSON，以兼容映射器规范化常见字段，并用覆盖率暴露未知值。
- [流式连接结束但终止 usage chunk 缺失] → 保留成功调用记录，Token 字段为空且覆盖率下降，不进行估算。
- [直接聚合随数据增长变慢] → 为 run、task、created_at、provider/model/status 建索引；后续基于真实负载引入 rollup。
- [客户端重试产生更多记录] → 这是精确账本的预期行为，attempt 字段和状态用于区分逻辑调用与实际尝试。
- [并发结束状态写入失败] → 记录器使用独立短事务；业务调用仍返回，但日志明确报告计量持久化错误。

## Migration Plan

1. 运行 Alembic migration 创建事实表和索引，不改写现有数据。
2. 部署后端采集、聚合 API，再部署前端看板。
3. 历史记录仍可展示非 Token 指标；Token 覆盖率明确显示历史缺口。
4. 回滚时先回滚前端和采集代码，再由 Alembic 删除新表；既有业务表不受影响。

## Open Questions

- 费用统计需要未来引入带生效时间的模型价格快照，本次不显示推算费用。
- 对不兼容 `include_usage` 的 OpenAI-compatible 服务，适配器将退回无 Token 明细但保留调用事实。
