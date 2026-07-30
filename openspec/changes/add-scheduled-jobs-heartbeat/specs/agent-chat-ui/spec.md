## ADDED Requirements

### Requirement: 用户可以在 Chat UI 管理自动化
系统 SHALL 提供自动化管理入口，展示定时任务和 heartbeat 的启用状态、计划摘要、下一次运行、最近结果与历史。

#### Scenario: 查看自动化列表
- **WHEN** 用户打开自动化管理界面
- **THEN** UI 区分普通定时任务与 heartbeat，并显示其状态、时区、下一触发和最近结果

#### Scenario: 编辑计划
- **WHEN** 用户创建或编辑计划
- **THEN** UI 校验计划类型、cron/间隔、时区、错过策略、重叠策略和权限包后提交版本化更新

#### Scenario: 查看关联 Run
- **WHEN** 用户从运行历史选择一个已创建 Astra Run 的执行
- **THEN** UI 可以导航到对应对话及完整审计 timeline

### Requirement: 用户可以配置低噪音 heartbeat
系统 SHALL 在 Chat UI 提供 heartbeat 启停、周期、活动时间窗、时区和 prompt 配置，并解释静默确认语义。

#### Scenario: 启用 heartbeat
- **WHEN** 用户保存有效 heartbeat 配置
- **THEN** UI 显示下一检查时间和 `HEARTBEAT_OK` 不会产生提醒的说明

#### Scenario: Heartbeat 被阻塞或延后
- **WHEN** 最近 heartbeat 因权限失效、非活动时间或会话繁忙未执行
- **THEN** UI 显示可区分的 blocked、skipped 或 deferred 状态及可操作原因
