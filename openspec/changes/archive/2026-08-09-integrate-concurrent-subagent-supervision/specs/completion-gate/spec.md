## ADDED Requirements

### Requirement: 根完成门验证并发子 Agent 汇合已消费
系统 SHALL 在 trusted 根 Run 成功完成前确认所有强制 descendant 已进入允许终态、required 和 first-success Join 已成功消费、child 预算已结算、必要审批已解决且不存在未处理的阻塞合并冲突。

#### Scenario: Child 已完成但 Join 尚未消费
- **WHEN** 所有 required child 已完成但其 Join 仍处于 ready 或 merging
- **THEN** CompletionGate 返回 continue_run
- **THEN** 根 Agent 不得发布最终成功答案

#### Scenario: Optional child 失败
- **WHEN** optional child 失败且不影响任何强制成功准则或 required Join
- **THEN** CompletionGate 可在记录 warning 后继续评估成功

#### Scenario: 合并结果存在阻塞冲突
- **WHEN** required child 的已验证结果产生尚未处理且会影响强制成功准则的 conflict
- **THEN** CompletionGate 不返回 completed
- **THEN** 根 Agent 必须解决冲突、补充验证、重新委派或进入 blocked
