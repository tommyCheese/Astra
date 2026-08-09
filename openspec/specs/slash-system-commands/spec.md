# slash-system-commands Specification

## Purpose
TBD - created by archiving change add-context-window-management. Update Purpose after archive.
## Requirements
### Requirement: The server exposes a system command catalog
The system SHALL expose a catalog of registered, user-invocable slash commands with stable names, localized descriptions, effects, and availability, and SHALL initially register only `compact` and `clear`.

#### Scenario: Discover preset commands
- **WHEN** a client requests the system command catalog
- **THEN** the response contains `/compact` and `/clear`
- **THEN** each entry explains its context effect

### Requirement: Only registered system commands can execute
The system MUST resolve command execution through the server registry and MUST reject unknown, disabled, or unavailable names without interpreting them as arbitrary server operations.

#### Scenario: Execute a registered command
- **WHEN** the user executes a command present and available in the catalog
- **THEN** the registry dispatches its predefined handler
- **THEN** the response identifies the executed command and its result

#### Scenario: Execute an unknown command
- **WHEN** a client requests execution of an unregistered command
- **THEN** the request fails with a classified command-not-found error
- **THEN** no context or conversation state changes

### Requirement: System commands are visible without becoming model input
The client and server SHALL execute host system commands without creating a Run, invoking a model, or binding a Skill, and SHALL preserve the executed slash invocation as a command-styled user timeline entry that is excluded from model context.

#### Scenario: Execute compact from the Composer
- **WHEN** the user selects or submits `/compact`
- **THEN** the slash query is consumed by command execution
- **THEN** a command-styled user timeline entry containing the `/compact` prefix is created
- **THEN** no model-visible user message or model Run is created

#### Scenario: Highlight a command prefix
- **WHEN** an executed command is shown in a user timeline entry
- **THEN** the slash command prefix is visually distinguished from its arguments

### Requirement: Commands support their declared argument mode
The command catalog SHALL distinguish commands with no arguments, optional arguments, and required arguments. `/compact` SHALL accept an optional direction string and expose a useful default direction, while `/clear` SHALL execute with no user-message body.

#### Scenario: Compact with the default direction
- **WHEN** the user selects `/compact` without writing a direction
- **THEN** the Composer stages the catalog-provided default direction
- **THEN** execution records the full command invocation in the timeline

#### Scenario: Clear without a message body
- **WHEN** the user selects or submits `/clear`
- **THEN** the context is cleared immediately without requiring additional user text

### Requirement: System commands coexist with Skill slash options
The Composer SHALL use one command-boundary detector and one accessible option list for registered system commands and eligible Skills, while preserving their distinct execution semantics.

#### Scenario: Open the root slash menu
- **WHEN** the user types `/` at a supported command boundary
- **THEN** matching system commands and eligible Skills are shown with distinguishable kinds

#### Scenario: Select a Skill option
- **WHEN** the user chooses a Skill result
- **THEN** the existing Skill token selection lifecycle is used
- **THEN** no system command executes

#### Scenario: Select a command option
- **WHEN** the user chooses a system command result
- **THEN** that command executes immediately against the current conversation
- **THEN** no Skill token is added

### Requirement: Command interaction is accessible and recoverable
The Composer SHALL support pointer and keyboard command selection, SHALL prevent duplicate submission while execution is pending, and SHALL provide success or classified failure feedback.

#### Scenario: Execute with Enter
- **WHEN** a system command option is active and the user presses Enter
- **THEN** the command executes instead of submitting the Composer
- **THEN** focus returns to the Composer after completion

#### Scenario: Command execution fails
- **WHEN** a system command fails
- **THEN** the UI presents the classified error
- **THEN** the command text remains available for retry

### Requirement: 系统注册 Subagent Run 创建命令
系统 SHALL 在 slash command catalog 中注册参数化 `/subagent <task>`，并 SHALL 将其标识为创建 trusted Run 的命令而不是普通 host context 操作。

#### Scenario: 用户选择 Subagent 命令
- **WHEN** 用户从统一 slash 菜单选择 `/subagent`
- **THEN** Composer 插入 `/subagent ` 并保留焦点以输入必需任务文本

#### Scenario: Subagent 功能不可用
- **WHEN** 用户关闭 `swarm`、kill switch active 或策略仅允许 shadow
- **THEN** catalog 将 `/subagent` 标记为 unavailable 并提供稳定原因

### Requirement: Subagent 命令创建必需委派 Run
系统 SHALL 消费 `/subagent` 命令文本，以参数作为用户目标创建 trusted、auto-plan Run，并 SHALL 冻结 `subagent_mode = required`；系统 MUST NOT 将 slash 原文写入用户消息或模型上下文。

#### Scenario: 提交有效命令
- **WHEN** 用户提交 `/subagent 比较三个候选方案`
- **THEN** 系统创建 goal 为 `比较三个候选方案` 的 trusted Run
- **THEN** 根 Agent 必须通过受治理 `swarm` built-in 创建至少一个 child 后才能成功完成

#### Scenario: 命令缺少参数
- **WHEN** 用户提交没有任务文本的 `/subagent`
- **THEN** 系统显示命令用法且不创建 Run

#### Scenario: Run 创建失败
- **WHEN** `/subagent` 的 Run 创建、资格或策略验证失败
- **THEN** Composer 保留完整任务参数以供修正或重试
- **THEN** 不留下部分 Run 或 child

### Requirement: 系统命令支持声明式参数模式
系统 SHALL 允许注册命令声明无参数或参数化模式、usage 和副作用分类，同时保持命令名称与处理器的封闭注册。

#### Scenario: 发现参数化命令
- **WHEN** 客户端请求系统命令目录
- **THEN** `/schedule` 与 `/heartbeat` 包含参数模式、usage 和副作用元数据

#### Scenario: 无参数命令保持兼容
- **WHEN** 客户端发现或执行 `/compact` 或 `/clear`
- **THEN** 其立即执行行为与现有上下文语义保持不变

### Requirement: 参数化命令使用确定性解析和 host 执行
系统 MUST 在服务端解析注册命令的参数，MUST 拒绝未知 subcommand/flag，且 MUST NOT 把命令文本作为用户消息发送给模型。

#### Scenario: 提交完整参数化命令
- **WHEN** 用户提交 `/schedule list` 或 `/heartbeat status`
- **THEN** 客户端消费完整命令文本并向 host command API 发送 name 与 arguments
- **THEN** 不创建用户消息、Skill 绑定或模型 Run

#### Scenario: 参数解析失败
- **WHEN** 参数包含未闭合引号、未知 flag 或额外位置参数
- **THEN** 系统返回分类的 command usage error 且不产生控制面副作用

### Requirement: Composer 支持参数编辑态
Composer SHALL 在选择参数化命令时插入命令前缀并让用户继续输入参数，而无参数命令继续立即执行。

#### Scenario: 选择 schedule 命令
- **WHEN** 用户从 slash 菜单选择 `/schedule`
- **THEN** Composer 保留 `/schedule ` 并把光标置于其后
- **THEN** Enter 提交完整 host command 而不是普通对话

#### Scenario: 参数命令执行失败
- **WHEN** host command 返回权限或用法错误
- **THEN** Composer 保留完整文本供修改重试并展示分类错误

### Requirement: Subagent Run 创建命令遵循当前回答模式
系统 SHALL 消费 `/subagent <task>` 命令文本，以参数作为用户目标并冻结 `subagent_mode = required`；当前回答模式为 standard 时 SHALL 创建无 Plan 的快速 Run，当前回答模式为 trusted 时 SHALL 创建自动执行 Plan 的 trusted Run。

#### Scenario: 快速模式提交 Subagent 命令
- **WHEN**用户在 standard 模式提交 `/subagent 比较三个方案`
- **THEN**系统创建 goal 为 `比较三个方案`、`answer_mode = standard`、`subagent_mode = required` 且无 `plan_execution` 的 Run
- **THEN**slash 原文不进入用户消息或模型上下文

#### Scenario: 可信模式提交 Subagent 命令
- **WHEN**用户在 trusted 模式提交 `/subagent 比较三个方案`
- **THEN**系统创建 goal 为 `比较三个方案`、`answer_mode = trusted`、`subagent_mode = required` 且 `plan_execution = auto` 的 Run

