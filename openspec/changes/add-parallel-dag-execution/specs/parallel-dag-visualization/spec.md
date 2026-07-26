## ADDED Requirements

### Requirement: 图谱准确展示多个活动节点
系统 SHALL 在可信执行图谱中同时展示所有权威活动 execution，并 SHALL 使用节点文字、图标、边框和汇总信息区分 running、waiting_resource、waiting_approval、committing 和 terminal 阶段。

#### Scenario: 三个分支并行运行
- **WHEN** 当前 Plan 有三个 active NodeExecution
- **THEN** 三个对应 PlanNode 同时显示为活动状态
- **THEN** 图谱标题区显示活动节点数和已使用/总并发槽位

#### Scenario: 并行度降为一
- **WHEN** 策略或资源冲突使同一时间只有一个 execution 活动
- **THEN** UI 显示实际活动数量而不把 DAG 分支虚假呈现为正在并行

### Requirement: 并行路径和等待原因可以直接识别
系统 SHALL 突出当前并行分支及其相关依赖边，并 SHALL 为尚未执行的节点展示资源冲突、并发槽位、审批或依赖屏障等公开等待原因。

#### Scenario: 节点等待资源写锁
- **WHEN** ready 节点因另一个 execution 持有冲突资源租约而未被认领
- **THEN** 节点显示“等待资源”及安全的资源类别摘要
- **THEN** UI 不泄露宿主路径、凭据或未经清洗的工具输入

#### Scenario: fan-in 等待最后一个分支
- **WHEN** 汇合节点的必要依赖仅完成 N/M
- **THEN** 节点显示“等待汇合 N/M”
- **THEN** 未完成的必要分支可以从图谱依赖边识别

### Requirement: 分支级失败和取消不误导总体状态
系统 SHALL 分别展示失败、受阻、取消和仍在运行的分支，并 SHALL 仅在权威 Run 状态改变时更新总体终态。

#### Scenario: 一个分支失败而其他分支继续
- **WHEN** 一个活动节点失败且两个无关节点仍在运行
- **THEN** 失败分支及其受阻后继显示失败传播
- **THEN** 其他两个节点继续显示 running，Run 不被提前呈现为终态

#### Scenario: 取消传播中
- **WHEN** Run 已接受取消请求但部分 Worker 尚未确认停止
- **THEN** UI 显示“正在取消”及剩余活动 execution 数
- **THEN** 所有 execution 达到终态后才显示最终 cancelled 状态

### Requirement: 并发图谱更新保持稳定且可访问
系统 SHALL 合并短时间窗内的并行节点事件，保持节点位置稳定，并 SHALL 为键盘、屏幕阅读器、暗色、高对比和 reduced-motion 用户提供等价状态信息。

#### Scenario: 多个节点在同一时间窗更新
- **WHEN** 客户端在一个动画帧内收到多个 execution 状态增量
- **THEN** 图谱最多提交一次可见状态更新
- **THEN** 仅状态变化不会触发 DAG 重新布局或视口跳动

#### Scenario: 屏幕阅读器观察并行变化
- **WHEN** 多个节点相继开始或完成
- **THEN** live region 合并播报活动数量和重要状态变化
- **THEN** 用户仍可聚焦每个图谱节点读取 execution 阶段、依赖和等待原因

#### Scenario: 用户启用 reduced-motion
- **WHEN** 多个节点同时 running 且系统偏好减少动画
- **THEN** 所有并行状态通过静态图标、文字、边框和线型表达
- **THEN** UI 不依赖流动边或脉冲动画传达并行关系

### Requirement: 历史与恢复视图保留并行执行事实
系统 SHALL 在 Run 快照和历史图谱中保留节点 execution attempt、重叠时间区间、调度批次和终态摘要，并 SHALL 在重连后恢复与权威状态一致的当前并行视图。

#### Scenario: 用户打开已完成的并行 Run
- **WHEN** 历史 Run 包含多个时间重叠的 NodeExecution
- **THEN** 图谱允许用户查看各节点 attempt 和执行时间
- **THEN** 历史视图不把并行分支重写为虚假的串行时间线

#### Scenario: SSE 断线后恢复
- **WHEN** 客户端错过多个并行节点事件并重新获取快照
- **THEN** 快照替换不完整的本地 execution 集合
- **THEN** 活动节点、等待原因和并发槽位与服务端权威状态一致
