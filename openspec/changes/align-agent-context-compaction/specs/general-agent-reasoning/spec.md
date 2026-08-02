## ADDED Requirements

### Requirement: root Agent loop 跨上下文窗口连续执行
系统 SHALL 让 standard 和 trusted root Agent loop 在每次模型调用前、工具结果后和恢复后检查活动上下文压力，并 SHALL 在达到策略阈值时通过共享压缩生命周期安装 root-specific 语义 checkpoint 后继续，而不是在单个 Run 内无界累积 observations。

#### Scenario: trusted Run 在工具循环中达到阈值
- **WHEN** trusted root 完成一个工具行动后仍有未完成 Plan nodes 且上下文达到自动压缩阈值
- **THEN** Runtime 在下一次模型决策前压缩旧 observations
- **THEN** TaskContract、Plan/AgentState、成功准则、权限、有效 Skills、预算和 Completion Gate 从规范状态重新注入

#### Scenario: standard Run 达到阈值
- **WHEN** standard root 的快速工具循环达到自动压缩阈值且仍需继续
- **THEN** Runtime 保留当前用户意图、授权边界、已验证结果和近期 observations 并安装轻量 root checkpoint
- **THEN** Runtime 不为 standard Run伪造 trusted TaskContract、Plan 或 AgentState

#### Scenario: 压缩后继续完成节点
- **WHEN** root 从 checkpoint 窗口恢复并完成后续行动
- **THEN** Completion Gate 和状态转换只使用规范 Plan、Evidence 和已提交 observations 判定完成
- **THEN** checkpoint 中的自然语言完成声明不能绕过验证

### Requirement: root checkpoint 结构化保留全局连续性
系统 SHALL 让 root checkpoint 结构化包含用户意图、当前约束、关键决策、带 Evidence 引用的已验证事实、全局进度、Workspace/Artifact 变化、已消费 child 结果、近期失败、未决事项和下一步，并 SHALL 保留预算内近期原始输入与 observations。

#### Scenario: 用户否定先前方案
- **WHEN** 最近用户输入撤销或修正旧 checkpoint 中的决定
- **THEN** 新 checkpoint 将最新明确指令作为当前约束并标记旧决定已失效
- **THEN** 近期用户原文在保留预算内继续提供给模型

#### Scenario: child 结果进入 root checkpoint
- **WHEN** fan-in 已验证并消费一个 child SubagentResult
- **THEN** root checkpoint 保存 execution/result 引用和有界摘要
- **THEN** 未经 fan-in 接受的 child local facts 不进入 root verified facts

### Requirement: root compaction 不改变行动与恢复语义
系统 SHALL 在压缩前后保持行动幂等键、ToolCall 状态、Plan/AgentState 版本、waiting continuation、cancellation epoch 和 Evidence lineage，并 SHALL 使压缩安装与 Agent 状态更新并发安全。

#### Scenario: 工具结果已提交后发生压缩崩溃
- **WHEN** ToolCall 结果已经持久化但进程在 checkpoint 安装前停止
- **THEN** 恢复器从已提交工具结果和先前有效窗口继续压缩或安装
- **THEN** 不重新执行已提交的外部行动

#### Scenario: waiting Run 恢复后需要压缩
- **WHEN** waiting_user 或 waiting_approval Run 接收 continuation 且恢复后的上下文达到阈值
- **THEN** Runtime 将 continuation 作为受保护的最新输入并在下一模型调用前安全压缩
- **THEN** 原 continuation token 和等待状态转换保持可审计

