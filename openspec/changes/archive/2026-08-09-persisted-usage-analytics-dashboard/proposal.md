## Why

当前用量统计以 Agent Turn 代替模型调用，并用前端公式估算 Token，既不精确也无法形成可查询的历史账本。Astra 需要以供应商返回值和数据库记录为唯一事实来源，确保服务重启后仍能按全部历史、当前对话和时间范围准确回显。

## What Changes

- 新增持久化 `ModelInvocation` 流水，逐次记录模型请求、重试、状态、耗时、供应商 request id 与原始/规范化 Token usage。
- 从模型流式和非流式响应中采集 input、cached input、output、reasoning 与 total Token；供应商未提供的字段保持未知，不再估算为零。
- 基于现有 Run、AgentTurn、ToolCall、Memory、SandboxJob 与 Artifact 数据提供统一用量聚合 API。
- 支持全部历史、最近 7/30 天、当前对话和单次 Run 查询，并返回按日期、模型和工具的分组数据。
- 将当前小型用量弹窗升级为持久化看板，加入加载、错误、空状态、时间范围、数据覆盖率和明细表。
- 删除“前端估算 Token”逻辑，明确每项指标的统计分母和缺失数据语义。

## Capabilities

### New Capabilities

- `usage-metering`: 真实模型调用流水、Token 采集、调用终态、数据库持久化和精确聚合口径。
- `usage-analytics-dashboard`: 用量查询 API、范围过滤、趋势/分组响应和重启可恢复的前端看板。

### Modified Capabilities

无。

## Impact

- 后端新增数据库表、Alembic migration、Repository、模型调用采集器、聚合服务和 `/api/usage` API。
- Model Client 请求与流式解析需要保留供应商 usage 元数据，但不改变 Agent 的结构化输出协议。
- 前端新增用量 API 类型和看板状态，替换 `UsageModal` 中基于当前 `RunView` 的估算逻辑。
- SQLite 与 PostgreSQL 均需支持迁移和查询；第一版直接聚合事实表，不引入 Redis 或独立分析数据库。
