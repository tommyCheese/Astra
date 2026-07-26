## ADDED Requirements

### Requirement: 可信计划使用有界并行调度
系统 SHALL 在 trusted Plan 中原子认领满足全部必要依赖且不违反并发、预算或资源策略的一批 ready 节点，并 MUST NOT 将 ready 集合固定退化为只执行排序第一项。

#### Scenario: 两个独立分支同时可执行
- **WHEN** 两个 pending 节点的必要依赖均已完成、资源互不冲突且至少有两个并发槽
- **THEN** 调度器在同一调度周期认领两个节点
- **THEN** 两个节点的执行时间区间产生可验证重叠

#### Scenario: 并发槽位不足
- **WHEN** ready 节点数量大于当前 Run 的可用并发槽位
- **THEN** 调度器按依赖层级、节点 index 和稳定标识确定性认领允许数量的节点
- **THEN** 其余节点保持 pending 并在后续槽位释放后重新参与调度

### Requirement: 并行节点执行上下文相互隔离
系统 SHALL 为每个被认领节点创建独立且持久化的执行 attempt，并 SHALL 隔离其模型上下文、Agent turns、工具调用、观察、评估、重试和证据。

#### Scenario: 两个节点并发调用不同工具
- **WHEN** 两个活动节点分别产生工具决策
- **THEN** 每个决策和 ToolCall 关联到自己的 PlanNode 与 NodeExecution
- **THEN** 任一节点的临时观察不会作为另一节点尚未提交的事实进入上下文

#### Scenario: 并发节点提交冲突事实
- **WHEN** 两个节点提交相互矛盾的 Run 级事实
- **THEN** Coordinator 生成 conflict Evaluation 并保留双方 provenance
- **THEN** 系统不会通过最后写入者覆盖先前事实

### Requirement: 并行认领遵守资源和副作用冲突策略
系统 SHALL 在认领和执行节点前根据权限 effect plan、资源键、读写模式和工具并发声明获取版本化资源租约，并 SHALL 将无法安全并行的节点确定性串行化。

#### Scenario: 两个节点只读同一资源
- **WHEN** 两个 ready 节点只申请读取同一资源且策略允许并发读取
- **THEN** 两个节点可以同时获得租约并执行

#### Scenario: 两个节点写入冲突路径
- **WHEN** 两个 ready 节点申请写入相同或存在祖先子路径关系的工作区资源
- **THEN** 同一时间最多一个节点获得写租约
- **THEN** 另一个节点保持 pending 并公开 `resource_conflict` 等待原因

#### Scenario: 工具资源集合未知
- **WHEN** 一个有副作用工具无法在执行前确定资源集合或被声明为 exclusive
- **THEN** 该工具在适用的 Run 或 provider 范围内独占执行

### Requirement: 并行预算和状态提交保持原子
系统 SHALL 在节点认领时原子预留并发槽位及适用预算，并 SHALL 使用 Plan 版本、execution attempt 和状态版本校验提交，防止重复认领、重复消费和丢失更新。

#### Scenario: 两个 Coordinator 同时扫描同一 Run
- **WHEN** 两个调度事务同时尝试认领同一个 pending 节点
- **THEN** 只有一个事务成功创建当前 execution attempt
- **THEN** 另一个事务重新读取权威状态且不启动重复工具调用

#### Scenario: 并发节点接近工具预算上限
- **WHEN** 剩余工具预算不足以满足全部候选节点的预留
- **THEN** 调度器只认领预算可以原子预留的节点
- **THEN** 实际工具调用总数不会因竞态超过生效上限

### Requirement: 审批、失败和取消采用分支作用域
系统 SHALL 独立跟踪每个活动节点的审批、失败、超时和取消状态，并 SHALL 仅在依赖或资源关系要求时影响其他分支。

#### Scenario: 一个分支等待审批
- **WHEN** 一个活动节点进入 `waiting_approval` 且存在无依赖、无冲突的 ready 节点
- **THEN** 等待审批的分支暂停且其冻结行动保持版本绑定
- **THEN** Coordinator 继续执行其他安全分支

#### Scenario: 一个必要分支失败
- **WHEN** 一个非可选节点失败且无法恢复
- **THEN** 其必要后继节点传播为 blocked
- **THEN** 无依赖关系的活动分支不被误取消

#### Scenario: 用户取消整个 Run
- **WHEN** 用户请求取消包含多个活动 execution 的 Run
- **THEN** Coordinator 停止认领新节点并向全部活动 Worker 传播取消
- **THEN** 每个 execution 持久化可审计终态且资源租约最终释放

### Requirement: fan-in 和重规划使用一致性屏障
系统 SHALL 仅在全部必要前置节点达到允许终态后认领 fan-in 节点，并 SHALL 在旧 Plan 版本的活动 execution 达到可判定终态后才激活重规划版本。

#### Scenario: fan-in 仍有一个分支运行
- **WHEN** 汇合节点的三个必要前置节点中两个 completed、一个 running
- **THEN** 汇合节点保持 pending
- **THEN** 最后一个分支完成后汇合节点才进入 ready 集合

#### Scenario: 并行执行中请求重规划
- **WHEN** 运行时决定替换当前 Plan 且旧版本仍有活动 execution
- **THEN** Coordinator 停止认领旧版本新节点并进入 drain 屏障
- **THEN** 旧 execution 不可能在新版本激活后覆盖当前 Plan 状态

### Requirement: 并行执行可以确定恢复
系统 SHALL 持久化 execution attempt、heartbeat、幂等键、工具结果、预算预留和资源租约，使进程重启后的恢复不依赖原协程存活。

#### Scenario: 并行工具执行后进程重启
- **WHEN** 某个 attempt 已记录工具结果但尚未提交节点终态
- **THEN** 恢复器复用已记录结果完成提交
- **THEN** 工具不会因恢复而重复执行

#### Scenario: 非幂等行动结果未知
- **WHEN** 重启后无法判断某个非幂等并行行动是否已生效
- **THEN** 对应 execution 进入 `waiting_user` 或 `blocked`
- **THEN** 其他可安全恢复的分支仍按策略继续
