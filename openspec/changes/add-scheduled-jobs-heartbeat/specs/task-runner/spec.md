## ADDED Requirements

### Requirement: Runner 记录可信自动化触发来源
系统 SHALL 允许可信的内部调度服务幂等创建 Run，并在 execution profile 和 timeline 中持久化不可由 prompt 伪造的触发元数据。

#### Scenario: 定时任务创建 Run
- **WHEN** 调度服务使用已领取的 schedule run 创建 Run
- **THEN** Run 记录触发类型、job id、schedule run id、逻辑计划时间和内部 principal

#### Scenario: 重复派发同一 schedule run
- **WHEN** 调度服务因恢复而重复派发同一个 schedule run id
- **THEN** Runner 返回原有关联 Run 而不创建第二个 Run
