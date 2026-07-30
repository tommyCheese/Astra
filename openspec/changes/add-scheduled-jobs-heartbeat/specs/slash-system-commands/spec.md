## ADDED Requirements

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
