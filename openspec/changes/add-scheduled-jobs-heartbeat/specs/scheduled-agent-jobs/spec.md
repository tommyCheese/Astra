## ADDED Requirements

### Requirement: 用户可以管理持久化定时任务
系统 SHALL 提供创建、读取、列出、更新、暂停、恢复和删除定时代理任务的 API，并在后端重启后保留定义与状态。

#### Scenario: 创建 cron 定时任务
- **WHEN** 用户提交有效的五字段 cron 表达式、IANA 时区和权限包
- **THEN** 系统持久化任务、计算下次 UTC 触发时间并返回版本号
- **THEN** 任务保存目标结果对话，每次触发在该对话创建新的 Run

#### Scenario: 并发更新冲突
- **WHEN** 客户端使用过期版本更新定时任务
- **THEN** 系统返回冲突且不覆盖较新的定义

#### Scenario: 暂停和恢复
- **WHEN** 用户暂停后再恢复定时任务
- **THEN** 暂停期间不产生新执行，恢复时按当前时间和 misfire 策略重新计算下一触发

### Requirement: 系统支持明确的计划时间语义
系统 SHALL 支持一次性、固定间隔和 cron 计划，将时间以 UTC 持久化，并使用指定的 IANA 时区解释本地 cron。

#### Scenario: 固定间隔不随执行耗时漂移
- **WHEN** 一个固定间隔任务的执行耗时超过普通执行耗时
- **THEN** 下一触发基于原计划时间计算而不是基于完成时间计算

#### Scenario: 非法时区被拒绝
- **WHEN** 用户提交未知或非 IANA 时区
- **THEN** 系统拒绝定义且不创建定时任务

#### Scenario: 一次性任务被领取
- **WHEN** 一次性任务到达计划时间并被成功领取
- **THEN** 系统创建一个执行记录并自动禁用后续触发

### Requirement: 到期触发具有持久化幂等边界
系统 SHALL 通过数据库条件领取、租约和唯一执行键，保证同一任务的同一逻辑计划时间最多创建一个 schedule run 记录。

#### Scenario: 两个调度器同时扫描
- **WHEN** 两个实例同时发现同一个到期任务
- **THEN** 只有一个实例成功领取并创建该计划时间的执行记录

#### Scenario: 领取后进程重启
- **WHEN** 实例在持久化 claimed 执行记录后、创建 Astra Run 前退出
- **THEN** 恢复器重新派发同一执行记录且不创建第二个逻辑 fire 记录

### Requirement: 错过触发和重叠执行策略是显式的
系统 SHALL 为每个定时任务持久化 misfire grace、misfire policy 和 overlap policy，并采用防止恢复风暴的安全默认值。

#### Scenario: 错过时间超过 grace
- **WHEN** 调度器恢复时某计划时间已超过 misfire grace 且策略为 skip
- **THEN** 系统记录 skipped_misfire 并推进到未来的下一触发时间

#### Scenario: 合并错过触发
- **WHEN** 多个触发在停机期间错过且策略为 fire_once
- **THEN** 系统仅创建一个合并执行并推进到未来触发时间

#### Scenario: 跳过重叠运行
- **WHEN** 同一任务已有活动执行且 overlap policy 为 skip
- **THEN** 新触发记录为 skipped_overlap 且不创建 Astra Run

### Requirement: 自动化执行复用 Run 安全与审计链路
系统 SHALL 通过与交互请求相同的 Run 创建与执行服务启动自动化，并重新验证保存的显式权限包。

#### Scenario: 有效权限包触发 Run
- **WHEN** 到期任务的权限包签名与有效期仍然有效
- **THEN** 系统创建关联 Task/Run，并记录 job id、逻辑计划时间和 schedule run id

#### Scenario: 复用目标对话工作空间
- **WHEN** 定时任务或 heartbeat 绑定目标对话并触发 Run
- **THEN** Run 使用目标对话的 task id 和同一个 workspace id，不创建独立工作空间
- **THEN** 生成文件、工作空间变更和运行结果可从目标对话继续访问

#### Scenario: 权限包失效
- **WHEN** 到期任务保存的权限包无效或过期
- **THEN** 执行记录进入 blocked 且系统不创建降权或绕过审批的 Run

### Requirement: 用户可以手动触发并检查运行历史
系统 SHALL 支持幂等手动触发，并返回计划执行的状态、时间、关联 Run 和结构化失败原因。

#### Scenario: 手动触发
- **WHEN** 用户用新的 idempotency key 手动触发已启用任务
- **THEN** 系统立即排队一个 schedule run 并返回其标识符

#### Scenario: 重复手动触发请求
- **WHEN** 用户重复提交相同 idempotency key
- **THEN** 系统返回原 schedule run 而不创建重复 Run

#### Scenario: 查询运行历史
- **WHEN** 用户查询某定时任务的执行历史
- **THEN** 系统按时间倒序返回终态、耗时、关联 Run 和错误摘要

#### Scenario: 查询任务制品
- **WHEN** 用户查询定时任务的制品
- **THEN** 系统返回带 schedule run、Run 和目标 Task 来源的最终结果文本及安全可交付文件
- **THEN** 文件来自对应 Run 的工作空间变更或 Artifact 记录，不包含目标对话中无关的历史文件

### Requirement: 用户可以通过 schedule 系统命令管理任务
系统 SHALL 注册参数化 `/schedule` host command，并通过确定性 subcommand 调用与 HTTP API 相同的定时任务应用服务。

#### Scenario: 查询工作区任务
- **WHEN** 用户执行 `/schedule list`
- **THEN** 系统返回工作区全部定时任务摘要且不创建模型 Run

#### Scenario: 创建固定间隔任务
- **WHEN** 用户执行包含有效 `create --every`、时区和 prompt 的 schedule 命令且存在有效无人值守权限包
- **THEN** 系统创建全局可管理且绑定当前结果对话的持久化任务，并返回标识符与下一触发时间

### Requirement: 普通定时任务具有稳定的结果交付对话
系统 SHALL 将从任意入口创建的普通定时任务纳入同一个工作区清单，并保存目标结果对话；该绑定用于交付文本、生成文件和运行状态，不限制任务的全局管理范围。

#### Scenario: 对话中创建任务
- **WHEN** 用户在会话 A 创建任务后从会话 B 或全局管理页查询、暂停或手动运行该任务
- **THEN** 系统允许该操作，任务仍绑定会话 A 交付结果，触发时在会话 A 创建 Run

#### Scenario: 管理页创建结果对话
- **WHEN** 用户在管理页选择创建新的结果对话
- **THEN** 系统先创建可持久化对话并将新定时任务绑定到该对话

#### Scenario: 删除仍被自动化绑定的对话
- **WHEN** 用户尝试删除仍被定时任务或 heartbeat 使用的结果对话
- **THEN** 系统拒绝删除并要求先更换绑定或删除自动化任务

#### Scenario: 执行生命周期子命令
- **WHEN** 用户执行有效的 `pause`、`resume`、`run` 或 `delete` 子命令
- **THEN** 系统通过版本化应用服务执行操作并返回结构化结果

#### Scenario: 拒绝未知参数
- **WHEN** schedule 命令包含未知 subcommand、flag 或格式无效的时间
- **THEN** 系统返回命令用法错误且不改变任务或会话状态
