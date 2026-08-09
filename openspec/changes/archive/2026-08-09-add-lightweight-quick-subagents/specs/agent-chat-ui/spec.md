## ADDED Requirements

### Requirement: 快速 Subagent 使用紧凑过程呈现
系统 SHALL 在 standard Run 创建 child 后显示共享 Subagent 活动摘要和可折叠详情，并 MUST NOT 为 standard Run 创建、展示或占位可信执行 DAG。

#### Scenario: 快速 Subagent 正在运行
- **WHEN**standard Run 的 `subagent_summary.total` 大于零
- **THEN**聊天过程显示 running、waiting、completed 数量及关键等待原因
- **THEN**用户可以查看 child 目标、状态、预算摘要、结果或失败

#### Scenario: 快速 Subagent Run 完成
- **WHEN**standard Run 的 children 和 Join 已收敛并生成最终答案
- **THEN**紧凑 Subagent 记录保留在对应对话过程内
- **THEN**对话级可信 DAG 窗格保持隐藏
