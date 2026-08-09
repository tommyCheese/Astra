# agent-heartbeat Specification

## Purpose
TBD - created by archiving change add-scheduled-jobs-heartbeat. Update Purpose after archive.
## Requirements
### Requirement: Heartbeat 是系统托管的持久化计划
系统 SHALL 为工作区维护至多一个全局 heartbeat desired state，并将其物化为受保护的系统托管定时任务；desired state 保存当前目标主会话。

#### Scenario: 启用 heartbeat
- **WHEN** 用户为目标主会话配置有效周期并启用 heartbeat
- **THEN** 系统 upsert 稳定标识 `heartbeat:global` 的 system-managed schedule、更新目标主会话并计算下一触发

#### Scenario: 从另一会话重新配置
- **WHEN** 用户已在会话 A 启用 heartbeat，随后从会话 B 更新 heartbeat
- **THEN** 系统更新同一全局 heartbeat 并将目标切换为会话 B，不创建第二条 heartbeat

#### Scenario: 普通 API 尝试修改 heartbeat
- **WHEN** 客户端通过普通 schedule 更新或删除 heartbeat 记录
- **THEN** 系统拒绝请求并要求使用 heartbeat API

#### Scenario: 禁用后重新启用
- **WHEN** 用户禁用再重新启用 heartbeat
- **THEN** 系统保留其配置与历史，并从重新启用时间计算下一触发

### Requirement: Heartbeat 在目标主会话中执行周期检查
系统 SHALL 在 heartbeat 到期时向目标主会话创建受约束的 agent turn，并明确区分 heartbeat 与普通重复任务。

#### Scenario: 到期检查
- **WHEN** heartbeat 在活动时间内到期且会话空闲
- **THEN** 系统使用配置的 heartbeat prompt 在目标 Task 中创建带 heartbeat 来源的 Run

#### Scenario: 不从旧聊天推断任务
- **WHEN** heartbeat prompt 未明确包含重复工作且没有持久化待处理事项
- **THEN** agent 不得把旧对话中的临时请求当作新的周期任务执行

### Requirement: Heartbeat 支持活动时间窗和繁忙延后
系统 SHALL 按配置时区执行活动时间窗检查，并在目标会话存在活动自动化或用户 Run 时避免并发 heartbeat。

#### Scenario: 活动时间窗之外
- **WHEN** heartbeat 在配置活动时间窗之外到期
- **THEN** 系统记录 skipped_inactive_window 并推进到窗口内的下一次计划检查

#### Scenario: 目标会话繁忙
- **WHEN** heartbeat 到期时目标会话存在活动 Run
- **THEN** 系统记录 deferred_busy，且不与该 Run 并发执行 heartbeat

### Requirement: 无需关注的 heartbeat 保持静默
系统 SHALL 将仅包含规范化 `HEARTBEAT_OK` 的结果记录为静默成功，而不向用户对话增加可见 Agent 消息。

#### Scenario: 静默确认
- **WHEN** heartbeat Run 的最终有效内容只有 `HEARTBEAT_OK`
- **THEN** schedule run 记录 `silent_ok` 且用户不会收到提醒

#### Scenario: 发现需要关注事项
- **WHEN** heartbeat 返回不只是静默确认的有效内容
- **THEN** 系统在目标会话显示结果并保留关联 Run 审计信息

### Requirement: Heartbeat 具有成本和噪音安全默认值
系统 SHALL 默认禁用 heartbeat，限制最小执行间隔，并使其继续服从权限、工具、并发和运行预算。

#### Scenario: 周期过短
- **WHEN** 用户配置低于系统最小值的 heartbeat 周期
- **THEN** 系统拒绝配置并返回允许的最小周期

#### Scenario: 权限不满足
- **WHEN** heartbeat 需要的能力不在保存的权限包中
- **THEN** heartbeat 进入 blocked 且不自动扩展权限

### Requirement: 用户可以通过 heartbeat 系统命令管理检查
系统 SHALL 注册参数化 `/heartbeat` host command，并支持确定性的状态、启用、禁用和手动检查子命令。

#### Scenario: 查看 heartbeat 状态
- **WHEN** 用户执行 `/heartbeat status`
- **THEN** 系统返回当前会话的启用状态、周期、活动时间窗、下一检查和最近结果

#### Scenario: 启用 heartbeat
- **WHEN** 用户执行带有效周期、时区及可选活动时间窗的 `/heartbeat on`
- **THEN** 系统更新 desired state 并返回下一检查时间

#### Scenario: 禁用 heartbeat
- **WHEN** 用户执行 `/heartbeat off`
- **THEN** 系统禁用系统托管计划但保留配置和历史

#### Scenario: 命令不能绕过最小周期
- **WHEN** `/heartbeat on` 请求的周期低于系统最小值
- **THEN** 系统拒绝命令且不修改 desired state

