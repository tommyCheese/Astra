## Why

Astra 即将以空数据库开始新的开发阶段，继续保留旧数据库、旧配置和旧持久化 payload 的兼容路径只会扩大状态空间并掩盖无效数据。现在应建立唯一的当前基线，让不符合当前 schema 的持久化数据明确失败，而不是被静默迁移或猜测修复。

## What Changes

- **BREAKING**：清空当前及备份开发数据库，不保留旧业务数据。
- **BREAKING**：将 Alembic 历史收敛为一个当前 schema 基线，不再支持旧数据库逐版本升级。
- **BREAKING**：删除旧 runtime profile、Agent Profile、Agent state、plan graph、reasoning policy、Memory 和权限数据的兼容读取、别名与回填逻辑。
- **BREAKING**：删除仅用于旧数据展示的字段和前端分支；当前 API 与持久化 payload 必须严格满足现行 schema。
- 保留正常输入校验、安全 fail-closed、外部服务协议适配、模型供应商兼容协议以及 Task Workspace 等当前产品能力。
- 空数据库启动时自动建立当前 schema，并验证新建 Task、Run、Memory 和设置链路。

## Capabilities

### New Capabilities

- `clean-start-persistence`: 定义唯一当前持久化基线、严格 schema 读取和空数据库启动行为。

### Modified Capabilities

- `agent-profile-runtime-composition`: 只接受当前 Agent Profile 组合 schema。
- `general-agent-reasoning`: 只接受当前 Agent state、plan graph 和结果 schema。
- `reasoning-policy`: 删除旧 reasoning policy 到当前模型思考配置的隐式迁移。
- `memory-management`: 删除旧 Memory namespace、kind、shadow 与 workspace 元数据兼容。
- `policy-driven-tool-runtime`: 删除只为旧授权或旧审计数据保留的读取视图。

## Impact

- Alembic migration history, SQLAlchemy models, repositories and schemas, runtime profile loading, Agent Profile composition, reasoning state, plan graph serialization, Memory/audit APIs, permission projections, frontend persisted-data types, tests, and local development databases.
