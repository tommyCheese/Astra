## ADDED Requirements

### Requirement: 活动计划状态参与完成判定

系统 SHALL 在允许 `completed` 或 `completed_with_warnings` 前检查活动计划的节点和依赖状态，并 MUST NOT 在必需节点仍为 pending、running、failed 或 blocked 时将运行表示为成功完成。

#### Scenario: 成功准则满足但计划仍在执行

- **WHEN** TaskContract 的强制准则已满足，但活动计划仍存在 running 或必需 pending 节点
- **THEN** CompletionGate 返回 continue 而不是 completed
- **THEN** 运行继续从 PlanScheduler 选择允许的节点

#### Scenario: 必需计划节点失败

- **WHEN** 一个必需节点进入 failed 或 blocked 且没有可用的合法替代计划
- **THEN** CompletionGate 返回 blocked 或 failed
- **THEN** 终止原因包含节点和未满足成功准则引用

#### Scenario: 计划、契约和验证全部完成

- **WHEN** 活动计划全部必需节点完成、强制成功准则满足、必需验证通过且不存在等待状态
- **THEN** CompletionGate 可以返回 completed 或符合策略的 completed_with_warnings

