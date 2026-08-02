## ADDED Requirements

### Requirement: Runner 管理 SubagentSupervisor 的完整生命周期
系统 SHALL 在 eligible trusted Run 执行期间启动一个 Run-scoped SubagentSupervisor，并 SHALL 在正常完成、等待、取消、失败和进程恢复路径中协调 worker、heartbeat、fencing、Join reconciliation 和结构化关闭。

#### Scenario: Run 正常完成
- **WHEN** 根 Agent 满足完成门且所有 mandatory child 工作已消费
- **THEN** Runner 停止接受新的委派、等待 Supervisor 完成持久化协调并关闭进程内 worker
- **THEN** Run 终态与 child、Join、预算和事件状态一致

#### Scenario: 用户取消整个 Run
- **WHEN** 多个 child 正在 queued、running 或 waiting
- **THEN** Runner 先持久化 Run/child cancellation epochs 和 fencing
- **THEN** Supervisor descendant-first 取消可中断工作并保留 immutable-effect 与 result-unknown 报告

#### Scenario: Kill switch 在运行中开启
- **WHEN** kill switch 阻止新的 fan-out 且已有 child 尚未终态
- **THEN** Runner 不创建新 child
- **THEN** 已有 child 根据 drain/cancel 策略进入受控终态而不丢失 lineage
