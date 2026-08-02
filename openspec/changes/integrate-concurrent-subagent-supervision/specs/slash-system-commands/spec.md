## ADDED Requirements

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
