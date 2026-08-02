## ADDED Requirements

### Requirement: 子 Agent Join 形成依赖范围内的计划屏障
系统 SHALL 将 durable Join 绑定到消费其结果的 PlanNode，并 SHALL 仅阻塞该消费节点及其依赖分支，不得因 Join waiting 而暂停不依赖该 Join 的 root 节点。

#### Scenario: Join 等待且存在无依赖节点
- **WHEN** 一个 required Join 仍在等待且另一个 pending root 节点的全部普通依赖与 Join 依赖均已满足
- **THEN** PlanScheduler 不选择 Join consumer 节点
- **THEN** PlanScheduler 可以选择该无依赖节点

#### Scenario: Required Join 被阻塞
- **WHEN** required Join 的必要 child 失败且没有安全重试或替代路径
- **THEN** consumer 节点进入 blocked 或触发受控 replan
- **THEN** 不相关且仍有效的完成节点和 Evidence 保持不变

#### Scenario: First-success Join ready
- **WHEN** first-success Join 的一个 child 产生验证成功结果
- **THEN** Join 可以进入 ready 并解除 consumer 节点屏障
- **THEN** 仅在 loser 无持久副作用或补偿风险时取消其余 child
