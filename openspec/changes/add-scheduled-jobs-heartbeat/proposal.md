## Why

Astra 目前只能由用户即时发起 Run，无法可靠地在指定时间执行周期性代理任务，也不能像 OpenClaw 一样让 agent 在主会话中定期检查待办并仅在有必要时提醒用户。需要一个重启可恢复、可审计且与现有 Run 安全边界一致的自动化底座。

## What Changes

- 新增持久化定时任务，支持一次性、固定间隔和 cron 表达式，并明确时区、错过触发、并发重叠和启停语义。
- 新增调度控制面 API，可创建、查询、更新、暂停、恢复、删除、手动触发任务并查看独立运行历史。
- 调度器通过数据库租约领取到期任务，以稳定的幂等键创建 Astra Task/Run；进程重启后可恢复，多实例部署时避免重复执行。
- 新增全局系统托管 heartbeat：工作区只维护一个 desired state，按配置周期在其目标主会话发起受限 agent turn，支持活动时间窗、静默确认和忙碌时延后。
- 将 `/schedule` 与 `/heartbeat` 注册为参数化系统命令，使用户可在 Composer 中查询、创建、暂停、恢复和手动触发自动化，而不把命令文本发送给模型。
- heartbeat 复用普通定时任务的持久化触发与历史，但其系统托管定义只能通过 heartbeat API 修改，避免两套调度状态漂移。
- 为调度和 heartbeat 增加可观测状态、结构化失败原因、运行保留与安全默认值；自动化执行继续服从现有权限、审批、工具和工作区约束。

## Capabilities

### New Capabilities

- `scheduled-agent-jobs`: 定义持久化计划、触发领取、并发/错过策略、Run 创建、管理 API 和运行历史。
- `agent-heartbeat`: 定义系统托管 heartbeat、主会话检查、活动时段、静默结果、繁忙延后和配置一致性。

### Modified Capabilities

- `task-runner`: 允许由可信调度触发器幂等创建 Run，并记录自动化来源与触发身份。
- `agent-chat-ui`: 展示和管理自动化任务、heartbeat 配置及其最近运行状态。
- `slash-system-commands`: 支持带参数的确定性 host command，并注册 schedule 与 heartbeat 命令族。

## Impact

- 后端：新增调度/heartbeat 数据模型、Alembic 迁移、Repository、后台调度服务、API schema/router，并接入 FastAPI lifespan 与现有 RunEngine。
- 命令系统：扩展命令注册元数据、参数解析、执行请求和结果契约；自动化命令调用同一应用服务，不复制调度逻辑。
- 前端：增加全局“已安排任务”一级入口、计划编辑器、运行历史和 heartbeat 设置；从任意会话创建的任务都在此统一管理。
- 数据库：新增 schedules、schedule_runs 与 heartbeat desired-state 相关字段/索引；SQLite 和 PostgreSQL 均需支持。
- 运维：新增调度轮询、租约、运行超时与历史保留配置和日志指标；不引入独立队列服务作为首版强依赖。
- 安全：自动化执行不绕过现有审批；创建与修改自动化属于显式用户操作，系统托管 heartbeat 不允许通过普通 schedule API 篡改。
