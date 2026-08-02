## Context

Astra 的 Run 当前由 `POST /api/runs` 创建，并在 FastAPI 进程内以 `asyncio.Task` 执行。状态、事件、权限包和工具审计已经持久化，但没有“何时创建 Run”的持久化控制面。现有 conversation retention 是单进程周期协程，不能提供任务级租约、错过触发、幂等运行历史或多实例互斥。

OpenClaw 的最新架构把 heartbeat 实现为系统托管 cron job：heartbeat 是主会话的周期检查，而明确的周期工作仍是普通 cron；计划、运行状态和历史持久化在共享 SQLite。APScheduler 建议任何需要跨重启保留的 schedule 使用持久化 data store；Temporal 的 Schedule 也把 overlap、catch-up、pause 和 manual trigger 作为一等策略。Astra 首版采用相同语义，但保留轻量、自托管和 SQLite-first 的部署特征。

## Goals / Non-Goals

**Goals:**

- 一次性、固定间隔与 cron 计划在重启后仍可恢复，并按 IANA 时区计算。
- 通过数据库唯一键和短租约保证“同一计划时间最多创建一个执行记录”，支持单实例和多实例。
- 将错过触发、重叠执行、暂停、手动触发、失败与历史保留定义为显式策略。
- 复用现有 Task/Run、权限包、审批、取消和审计链路，不创建旁路执行权限。
- 将 heartbeat 建模为系统托管 schedule 的 desired state，并实现活动时间窗、繁忙延后和静默确认。
- API 和服务内核可独立测试，后续可无缝增加 Web 管理界面。

**Non-Goals:**

- 首版不引入 Temporal、Celery、Redis 或独立 worker 服务。
- 不承诺外部副作用 exactly-once；保证的是 trigger/run 创建幂等，工具副作用仍依赖现有幂等与审批机制。
- 不在 heartbeat 中推断旧对话中的隐含重复任务。
- 不允许自动化绕过交互式审批；无人值守执行必须保存并校验显式权限包。
- 首批实现不完成复杂日历表达式、节假日规则或跨设备通知渠道。

## Decisions

### 1. 使用 Astra 自有持久化调度控制面

新增 `scheduled_jobs` 与 `scheduled_job_runs`。前者保存定义和下一触发时间，后者保存每个逻辑 fire time 的状态与关联 Task/Run。`(job_id, scheduled_for)` 和 `idempotency_key` 唯一，先落执行记录再创建 Run。

采用自有薄调度层而不是直接嵌入 APScheduler：Astra 需要把 permission bundle、目标会话、Run 来源、heartbeat desired state 和审计纳入同一事务边界；APScheduler 4 的持久层会形成第二套领域模型。未来可把扫描器替换成外部队列而保持 API 与表结构。

### 2. 计划计算和时间语义

所有时间以 aware UTC 存储。输入时区必须是 IANA zone。支持：

- `once`: RFC 3339 时间，成功领取后自动禁用；
- `interval`: 秒数加基准时间，下一次基于原计划时间推进，避免执行耗时造成漂移；
- `cron`: 标准五字段表达式，由 `croniter` 计算，DST 解释使用所选 `zoneinfo` 时区。

默认 `misfire_policy=skip`、`misfire_grace_seconds=300`、`overlap_policy=skip`。可选 `fire_once` 合并恢复窗口内的多次错过触发；首版不提供无限 catch-up，避免重启后触发风暴。

### 3. 数据库领取租约与幂等边界

每个调度器实例有稳定的 `instance_id`。扫描器按 `next_fire_at` 找到到期且租约为空/过期的记录，用带旧版本/租约条件的原子 UPDATE 领取。领取成功后在同一事务中：

1. 插入唯一的 `scheduled_job_runs`；
2. 计算并保存下一触发时间；
3. 提交后才创建 Astra Run。

进程在 1–2 之间崩溃时，reconciler 会重新处理 `claimed` 记录；唯一键防止重复 fire record。进程在外部工具执行中崩溃时沿用现有 Run 恢复语义，不宣称外部副作用 exactly-once。

### 4. 调度器是生命周期服务，执行与扫描解耦

`SchedulerService` 接入 FastAPI lifespan。一个短周期 scanner 只领取/派发，不等待 agent 完成；每个派发通过有上限的并发槽执行。shutdown 停止新领取并等待短暂 drain，已有 Run 仍由现有运行机制收敛。

配置包括 enabled、poll interval、lease duration、batch size、max dispatch concurrency 和 history retention。liveness 只检查 event loop/service 是否存活；readiness 可报告数据库扫描是否持续成功，避免把临时模型或外部网络故障当成进程死亡。

### 5. 自动化通过专用 Run 创建服务复用安全入口

HTTP 与 scheduler 复用同一套 Run 创建和执行入口。普通 schedule 保存目标对话，并保存 answer mode、模型选择、skill IDs 和签名 permission bundle 快照；每次触发都使用目标对话的 `task_id` 创建新 Run。由于 Task 与 `TaskWorkspaceRecord` 是一对一关系，dispatcher 会先解析该 Task 已有的 workspace，并把 workspace id 写入 trigger 元数据，禁止为自动化创建独立 Task 或 workspace。这样文本、生成文件、持久化副作用摘要和审计状态都落在目标对话原有工作空间。触发时再次验证权限包的签名和有效期；失效则把 schedule run 标为 `blocked`，不降级为交互执行。

从对话命令创建 schedule 时，当前对话同时作为无人值守权限包来源和默认结果对话。从全局管理页创建时，用户必须选择已有对话或创建一个新的专用结果对话；若目标对话尚无有效权限包，可使用工作区最近仍有效的无人值守执行配置。任务仍在工作区全局管理，绑定不形成管理权限边界。

Run 的 `execution_profile`/事件记录 `trigger={type, job_id, scheduled_for, schedule_run_id, target_task_id, workspace_id}`，便于审计、验证工作空间绑定，并避免把自动消息误计为用户活跃。

### 6. Heartbeat 是全局且受保护的系统托管 schedule

工作区最多一个 `kind=heartbeat` job，稳定键为 `heartbeat:global`。heartbeat API 写入 desired state，并记录当前选定的目标主会话；从其他会话重新配置时更新该目标，而不是创建第二个 heartbeat。普通 schedule CRUD 对该记录返回冲突。升级时旧的 `heartbeat:<task-id>` 记录按最近更新时间收敛为全局记录，其余记录停用并保留历史。

将 heartbeat 物化到共享调度表只是持久化实现细节，不代表它与普通定时任务是同一种产品类型。API、命令和 UI 必须保持独立入口与模型：heartbeat 固定使用 interval 表示系统检查周期，并具有活动时间窗、繁忙延后和静默确认；普通定时任务按 once、interval 或 cron 执行用户明确配置的任务指令，不得继承 heartbeat 的检查或静默语义。

heartbeat prompt 的默认契约是检查未完成事项并在没有需用户注意的内容时返回 `HEARTBEAT_OK`。纯确认结果不生成用户可见消息，但运行历史仍记录 `silent_ok`。活动时间窗在配置时区判断；agent 同一会话有活动 Run 或队列时将本次标为 `deferred_busy`，且不会与明确 cron 工作抢占同一会话。

### 7. API 以版本控制保护并发编辑

提供 `/api/schedules` CRUD、pause/resume、manual-run、runs history，以及 `/api/heartbeat` desired-state API。更新请求携带 `version`，过期版本返回 409。删除采用软删除/禁用并保留历史；手动触发使用独立 idempotency key，可选择 `force` 是否绕过 due 检查，但不能绕过权限校验或 overlap policy。

### 8. 自动化进入参数化系统命令注册表

注册两个稳定 host command：`/schedule` 和 `/heartbeat`。它们与 `/compact`、`/clear` 共用服务端注册表、发现 API、错误契约和 Composer 选项列表，但声明 `argument_mode=required` 与 usage。选择参数化命令时 Composer 插入规范命令前缀并保留输入焦点；用户提交完整命令后，客户端把 command name 与原始 arguments 发送给命令执行 API。

服务端使用确定性 tokenizer/parser，只允许注册的 subcommand 和 flag：

- `/schedule list|show|create|pause|resume|run|delete`
- `/heartbeat status|on|off|run`

命令处理器调用 schedule/heartbeat 应用服务，绝不把命令文本交给模型。创建命令使用当前 conversation 作为结果对话并从其中获取有效的签名 permission bundle；从任意会话执行 `list/show/pause/resume/run/delete` 仍操作同一工作区清单。无人值守权限配置必须来自显式参数或仍有效的签名 permission bundle，否则 fail closed 并引导用户打开自动化设置。查询命令只读；暂停、恢复、删除、启停和手动运行在注册表中标记为写副作用并写审计日志。

替代方案是为每个 subcommand 注册独立 slash 项，选项会迅速膨胀且难以表达参数；让模型解析自然语言则会把控制面变成非确定行为，并弱化权限边界。

### 9. 制品是按执行归属的可交付结果集合

定时任务详情把每次 schedule run 的持久化输出统一呈现为“制品”，但不新增第二套文件存储。没有文件的 Run 使用已持久化的最终 `Run.result.summary`/`Run.summary` 形成结果制品，因此简单文本、命令输出摘要或查询结论也有稳定的可见结果；产生文件的 Run 则从该 Run 的 workspace change 和 Artifact 记录中收集已通过安全检查的可交付文件。

制品必须带 `schedule_run_id`、`run_id` 和 `task_id` 来源，文件继续保存在目标对话的 workspace 或既有 Artifact store 中。列表只收录本次 Run 产生/修改的文件，不把目标对话工作空间中的历史文件全部归入当前定时任务；结果制品导航回目标对话，文件制品使用受校验的内容接口打开。

## Risks / Trade-offs

- [SQLite 在高并发下写锁竞争] → scanner 小批量、短事务、已有 WAL/busy timeout；后续 PostgreSQL 可使用 `FOR UPDATE SKIP LOCKED` 优化。
- [租约过短导致重复派发] → fire record 唯一约束、派发前状态 CAS，并让租约显著长于扫描临界区；运行本身不依赖扫描租约。
- [DST 导致 cron 时间不存在或重复] → 固定使用 IANA 时区和 croniter 的 aware datetime；测试春季跳时与秋季重复时段，并以 UTC fire time 唯一。
- [权限包在长期计划中到期] → 触发时重新验证并明确阻塞，UI/API 提供更新凭据的路径，不静默扩权。
- [heartbeat 产生噪音或成本] → 默认关闭、活动时间窗、busy defer、静默确认、最小间隔和每会话单实例约束。
- [命令参数歧义或注入] → 不使用 shell，不执行任意字符串；确定性 tokenizer、封闭 subcommand/flag 集合和 Pydantic 二次校验，未知参数直接拒绝。
- [应用内 scanner 随 API 扩缩容] → 数据库租约支持多实例；需要严格隔离时可用相同 service 入口迁移到独立 worker。

## Migration Plan

1. 增加表、索引和配置；在执行链路与恢复测试完成后默认启用 scheduler，使已启用任务在应用重启后继续运行。
2. 交付 Repository、计划计算器、API 与单元测试，再启用 scanner 的 dry/无任务路径。
3. 接入 Run 创建服务和执行记录收敛；在 SQLite 上验证重启、并发领取、misfire 与重复触发。
4. 增加 heartbeat desired-state 与主会话静默/繁忙逻辑。
5. 最后接入前端管理页并在单实例默认部署中开启调度器。

回滚时先关闭调度器，再回滚应用；保留新表不会影响旧版本。若必须降级数据库，可在确认无启用任务后执行迁移 downgrade。

## Open Questions

- 首版 heartbeat 的用户可见投递先复用目标 Task 的对话消息；跨渠道推送留给后续 connector/delivery capability。
- 多用户认证尚未进入 Astra 当前边界，因此 owner 字段先保留 nullable principal，待身份系统落地后收紧。

## Research References

- [OpenClaw Scheduled tasks](https://docs.openclaw.ai/cron)
- [OpenClaw Heartbeat](https://docs.openclaw.ai/gateway/heartbeat)
- [APScheduler persistent data stores](https://apscheduler.readthedocs.io/en/master/userguide.html)
- [Kubernetes liveness, readiness and startup probes](https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/#container-probes)
