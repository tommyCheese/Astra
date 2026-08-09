## ADDED Requirements

### Requirement: 快速控制器直接决定轻量委派
系统 SHALL 允许 eligible standard 根控制器依据任务独立性、预期并发收益、上下文压力、共享资源冲突、风险和剩余预算直接选择 `swarm`，并 SHALL 在 `subagent_mode = auto` 且收益不足时继续单 Agent 回答。

#### Scenario: 快速任务适合并发
- **WHEN**standard 请求包含多个独立、只读且可分别验证的子问题并且预算充足
- **THEN**根控制器可以在当前 AgentTurn 选择一个有界 `swarm` group
- **THEN**每个 child 收到结果导向的目标、输出合同和衰减后的能力范围

#### Scenario: 快速任务不适合并发
- **WHEN**standard 请求简单、强顺序、存在共享写热点或估计收益不足
- **THEN**根控制器继续当前快速循环而不创建 child
- **THEN**系统不为了展示 Subagent 而生成虚假 fan-out
