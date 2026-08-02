## ADDED Requirements

### Requirement: Subagent Run 创建命令遵循当前回答模式
系统 SHALL 消费 `/subagent <task>` 命令文本，以参数作为用户目标并冻结 `subagent_mode = required`；当前回答模式为 standard 时 SHALL 创建无 Plan 的快速 Run，当前回答模式为 trusted 时 SHALL 创建自动执行 Plan 的 trusted Run。

#### Scenario: 快速模式提交 Subagent 命令
- **WHEN**用户在 standard 模式提交 `/subagent 比较三个方案`
- **THEN**系统创建 goal 为 `比较三个方案`、`answer_mode = standard`、`subagent_mode = required` 且无 `plan_execution` 的 Run
- **THEN**slash 原文不进入用户消息或模型上下文

#### Scenario: 可信模式提交 Subagent 命令
- **WHEN**用户在 trusted 模式提交 `/subagent 比较三个方案`
- **THEN**系统创建 goal 为 `比较三个方案`、`answer_mode = trusted`、`subagent_mode = required` 且 `plan_execution = auto` 的 Run
