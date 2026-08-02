## ADDED Requirements

### Requirement: 用户可以在 Chat UI 管理自动化
系统 SHALL 提供全局“已安排任务”管理入口，展示定时任务和唯一 heartbeat 的启用状态、结果对话、计划摘要、下一次运行、最近结果与历史。

#### Scenario: 查看自动化列表
- **WHEN** 用户打开自动化管理界面
- **THEN** UI 将普通定时任务与 heartbeat 分区、分别计数，并提供一个“新建”入口在配置页选择类型，同时显示各自的状态、时区、下一触发和最近结果

#### Scenario: 两种自动化保持独立语义
- **WHEN** 用户分别创建 heartbeat 和普通定时任务
- **THEN** heartbeat 仅配置固定检查间隔、活动时间窗与静默检查指令，普通定时任务配置 once、interval 或 cron 触发计划及正常执行指令
- **THEN** UI 不将 heartbeat 计入普通定时任务数量，也不向普通定时任务展示 `HEARTBEAT_OK` 语义

#### Scenario: 从统一入口选择创建类型
- **WHEN** 用户点击自动化管理页的“新建”按钮
- **THEN** 配置页先提供“定时任务”与“Heartbeat”类型选择，并根据所选类型显示对应且互不混用的字段

#### Scenario: 编辑计划
- **WHEN** 用户创建或编辑计划
- **THEN** UI 校验结果对话、计划类型、cron/间隔、时区、错过策略、重叠策略和权限包后提交版本化更新
- **THEN** 用户可选择已有结果对话，或创建新的专用对话并完成绑定

#### Scenario: 可视化配置重复计划
- **WHEN** 用户选择定时重复执行
- **THEN** UI 使用每天、工作日、每周或每月以及日期、星期、小时和分钟轮盘生成计划，不要求用户书写 cron 表达式
- **THEN** 对无法映射的旧版自定义 cron，UI 默认保留原计划，直到用户明确选择新的可视化重复方式

#### Scenario: 查看关联 Run
- **WHEN** 用户从运行历史选择一个已创建 Astra Run 的执行
- **THEN** 普通定时任务 UI 导航到绑定的结果对话及完整审计 timeline，生成文件同时进入现有 Artifact/资料库链路；heartbeat UI 导航到其目标对话

#### Scenario: 查看定时任务制品
- **WHEN** 用户打开已产生执行结果的定时任务详情
- **THEN** UI 在“制品”区域按执行展示最终结果文本和该次执行产生的可交付文件
- **THEN** 没有文件的简单输出仍显示为结果制品，并可导航到目标对话查看完整内容

#### Scenario: 查看扩展制品
- **WHEN** 一次执行生成 JSON、表格、图片、HTML 或完成外部写入
- **THEN** UI 区分结构化数据和操作回执，提供数据打开、图片预览、隔离 HTML 预览及安全外部链接
- **THEN** 操作回执不展示原始工具输入、凭据、完整输出或只读调试日志

#### Scenario: 资料库与任务详情展示同一制品
- **WHEN** 定时任务或 heartbeat 产生结果、文件、数据或操作回执
- **THEN** 资料库与对应任务详情从同一制品目录读取，并展示一致的制品 ID、来源、目标对话和内容地址
- **THEN** 资料库可按类型、时间或对话合理展示这些制品，任务详情仅展示属于该任务运行的制品

### Requirement: 用户可以配置低噪音 heartbeat
系统 SHALL 在 Chat UI 提供 heartbeat 启停、周期、活动时间窗、时区和 prompt 配置，并解释静默确认语义。

#### Scenario: 启用 heartbeat
- **WHEN** 用户保存有效 heartbeat 配置
- **THEN** UI 显示下一检查时间和 `HEARTBEAT_OK` 不会产生提醒的说明

#### Scenario: Heartbeat 周期越界
- **WHEN** 用户输入少于 5 分钟或超过 24 小时的检查间隔
- **THEN** UI 使用易读语言说明允许范围、提示如何调整，并在修正前禁用保存操作

#### Scenario: Heartbeat 被阻塞或延后
- **WHEN** 最近 heartbeat 因权限失效、非活动时间或会话繁忙未执行
- **THEN** UI 显示可区分的 blocked、skipped 或 deferred 状态及可操作原因
