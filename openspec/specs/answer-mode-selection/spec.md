# answer-mode-selection Specification

## Purpose
TBD - created by archiving change add-trusted-answer-mode. Update Purpose after archive.
## Requirements
### Requirement: 用户可以选择快速回答或可信模式
系统 SHALL 提供 `standard` 快速回答和 `trusted` 可信模式，首次使用 SHALL 默认为快速回答，并 SHALL 在聊天输入区持续显示当前模式。

#### Scenario: 首次打开应用
- **WHEN** 用户尚未保存回答模式偏好
- **THEN** 系统选择快速回答
- **THEN** 聊天输入区显示可信模式处于关闭状态

#### Scenario: 用户开启可信模式
- **WHEN** 用户在聊天输入区开启可信模式
- **THEN** 下一次新建 Run 使用 trusted 模式
- **THEN** 当前正在执行的 Run 不改变模式或策略

### Requirement: 模式偏好与 Run 模式快照分别持久化
系统 SHALL 持久化用户首选回答模式，并 SHALL 为每个 Run 保存创建时不可变的 answer mode 与生效运行 profile。

#### Scenario: 重启后恢复偏好
- **WHEN** 用户选择可信模式后重启应用
- **THEN** 系统从数据库恢复可信模式偏好
- **THEN** 已保存的可信对话策略保持原值

#### Scenario: 运行期间修改模式
- **WHEN** 用户在某个 Run 创建后切换模式
- **THEN** 当前 Run 继续使用原 answer mode 和 profile 快照
- **THEN** 后续新 Run 使用新的首选模式

#### Scenario: 恢复等待中的运行
- **WHEN** 用户继续一个处于 `waiting_user` 的 Run
- **THEN** 系统沿用该 Run 原有模式和 profile
- **THEN** 当前界面的模式开关不会改变续跑的验证语义

### Requirement: 两种模式共享通用 Agent 能力
系统 SHALL 让快速回答和可信模式共享模型选择、执行审批、工具、文件与 Artifact、流式过程、会话、取消、记忆和分享能力，并 MUST NOT 维护第二套 Agent runtime。

#### Scenario: 快速回答调用工具
- **WHEN** 快速回答判断需要已授权工具才能响应用户
- **THEN** 系统通过与可信模式相同的 ToolRouter 和权限门执行工具
- **THEN** 工具事件进入相同的会话与过程流

#### Scenario: 两种模式取消运行
- **WHEN** 用户取消任一模式下的活动 Run
- **THEN** 系统使用相同取消协议和终态语义停止运行

### Requirement: 可信结果状态可感知且不承诺绝对正确
系统 SHALL 在可信 Run 的过程或回答中显示完整校验状态，并 MUST NOT 将可信模式描述为保证答案绝对正确。

#### Scenario: 完整校验通过
- **WHEN** trusted Run 的完整校验通过且无 warning
- **THEN** UI 显示“已校验”或等价状态

#### Scenario: 校验带警告或阻塞
- **WHEN** trusted Run 产生 warning 或未通过完整校验
- **THEN** UI 显示对应的带警告或未通过状态
- **THEN** 用户可以访问相关 VerificationReport 或终止原因

### Requirement: Both answer modes support frozen Skills without changing mode semantics
The system SHALL allow both `standard` quick-response Runs and `trusted` trusted-execution Runs to use built-in or custom Skill revisions, while preserving the fixed planning, execution, and verification lifecycle of the selected answer mode.

#### Scenario: Quick Run uses a Skill
- **WHEN** a `standard` Run explicitly or automatically activates a Skill
- **THEN** it follows the Skill through the quick Agent Loop without creating a TaskContract, Plan, PlanNode, PlanEdge, or trusted Completion Gate
- **THEN** shared tool, effect, approval, sandbox, artifact, cancellation, and error boundaries remain active

#### Scenario: Trusted Run uses a Skill
- **WHEN** a `trusted` Run requires one or more Skills
- **THEN** the system resolves and loads their frozen instructions before TaskContract and initial Plan DAG generation
- **THEN** trusted planning and full verification incorporate the applicable Skill workflow

### Requirement: Skill activation cannot switch answer mode
The system MUST NOT allow Skill instructions, compatibility declarations, scripts, or activation decisions to change a Run from quick response to trusted execution or from trusted execution to quick response.

#### Scenario: Quick Skill recommends trusted execution
- **WHEN** an active Skill appears to require a long, multi-deliverable, or strongly verified workflow
- **THEN** Astra may present a recommendation to start a trusted Run
- **THEN** the current quick Run does not create a DAG or silently switch modes

### Requirement: Draft tests explicitly select quick or trusted mode
The system SHALL require every Skill Draft test Run to select `standard` or `trusted` and SHALL label the Run as using an unpublished test snapshot without weakening the selected mode's safety boundaries.

#### Scenario: Start a Draft test
- **WHEN** the administrator starts a Skill Draft test from the workbench
- **THEN** the request identifies the answer mode and exact Draft digest before the first model operation

### Requirement: 系统只提供快速响应与可信执行两种产品模式
系统 SHALL 只接受 `standard` 快速响应和 `trusted` 可信执行两种回答模式，并 SHALL 根据回答模式确定唯一的规划与验证生命周期。

#### Scenario: 快速响应创建运行
- **WHEN** 用户以 `standard` 模式创建 Run
- **THEN** 系统直接进入共享 Agent Loop 的快速分支
- **THEN** 系统不创建 TaskContract、Plan、PlanNode、PlanEdge 或可信验证对象

#### Scenario: 可信执行创建运行
- **WHEN** 用户以 `trusted` 模式创建 Run
- **THEN** 系统在首次外部行动之前创建并持久化完整的规范 Plan DAG
- **THEN** 系统按 DAG 节点执行并运行完整验证与完成门

### Requirement: 每个 Run 持久化不可变的模式 Profile
系统 SHALL 在创建 Run 时持久化严格版本化且不可变的回答模式、可信计划执行选择与执行 Profile，并 SHALL 在续跑时使用该 Profile。

#### Scenario: 运行期间切换首选模式
- **WHEN** 用户在已有 Run 创建后切换回答模式
- **THEN** 已有 Run 的模式和 Profile 不发生变化
- **THEN** 后续新建 Run 使用新的首选模式

#### Scenario: 继续等待中的新版本 Run
- **WHEN** 用户继续一个由当前 Profile 版本创建的 `waiting_user` Run
- **THEN** 系统使用该 Run 原有的模式和 Profile 恢复

### Requirement: 可信用户决定计划生成后是否立即执行
系统 SHALL 允许 trusted Run 选择 `auto` 或 `confirm` 计划执行行为。该选择 MUST NOT 被建模为规划策略、plan-only 模式或工具效果批准。

#### Scenario: 可信计划自动执行
- **WHEN** trusted Run 的计划执行行为为 `auto`
- **THEN** 系统在完整 DAG 校验并持久化后激活该 Plan
- **THEN** 系统可以调度首个 ready 节点

#### Scenario: 可信计划等待确认
- **WHEN** trusted Run 的计划执行行为为 `confirm`
- **THEN** 系统持久化完整 DAG 并进入 `waiting_user`
- **THEN** 在用户确认对应 Plan 版本之前不执行任何 Plan 节点

#### Scenario: 用户确认展示的计划
- **WHEN** 用户提交匹配 Run、Plan ID、Plan 版本和 continuation token 的执行确认
- **THEN** 系统一次性消费该确认并激活对应 Plan
- **THEN** 后续工具效果仍独立经过配置的审批行为

#### Scenario: 用户暂不执行
- **WHEN** 用户在计划确认卡选择暂不执行
- **THEN** Run 保持可恢复的 `waiting_user`
- **THEN** 系统不把仅生成计划表示为成功完成

### Requirement: 删除的模式输入不提供兼容行为
系统 MUST NOT 接受 `plan_only`、`adaptive`、`direct` 或 `planning_strategy` 作为新请求、偏好或运行 Profile 的有效输入，并 MUST NOT 将其静默归一化为新值。

#### Scenario: 旧客户端发送规划策略
- **WHEN** 客户端提交包含 `planning_strategy` 的新 Run 或偏好请求
- **THEN** API 返回明确的请求校验错误
- **THEN** 系统不创建或更新任何记录

#### Scenario: 数据库尚未完成升级
- **WHEN** 启动检查发现活动记录仍包含删除的模式字段或枚举值
- **THEN** 应用拒绝启动 Run worker
- **THEN** 错误明确要求执行模式升级迁移

### Requirement: 两种模式共享不可关闭的安全边界
系统 SHALL 让两种模式共享工具 Schema 校验、权限门、Effect 分析、沙箱、数据流限制、Artifact 引用清洗、取消和错误处理，并 MUST NOT 允许回答模式绕过这些边界。

#### Scenario: 快速响应请求禁止操作
- **WHEN** `standard` Run 请求执行被平台策略禁止的操作
- **THEN** 共享权限门拒绝该操作
- **THEN** 快速响应不会因为缺少 DAG 而降低安全限制

