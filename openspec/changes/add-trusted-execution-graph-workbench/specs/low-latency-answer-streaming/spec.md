## ADDED Requirements

### Requirement: 图谱增量使用有界低开销投影
客户端 SHALL 直接归约有序 Plan 和 PlanNode 增量事件，并 SHALL 合并必要的 React 更新和权威快照刷新；单个有效节点事件 MUST NOT 强制重新加载完整 RunView。

#### Scenario: 多个并行节点相继完成
- **WHEN** 客户端在一个短时间窗内收到多个 `plan.node.updated` 事件
- **THEN** 客户端按事件顺序更新对应节点
- **THEN** 同一浏览器动画帧内最多提交一次可见图谱状态更新

#### Scenario: 新计划版本到达
- **WHEN** 客户端收到当前版本被替代的事件
- **THEN** 客户端切换到新版本快照或完整版本增量
- **THEN** 旧版本节点事件不能引起额外的错误 RunView 刷新循环

### Requirement: 图谱流使用快照校正而不依赖高频轮询
系统 SHALL 使用完整图谱快照作为恢复与校正来源，并 SHALL 使用 SSE 增量提供运行期及时反馈。

#### Scenario: 图谱 SSE 正常连接
- **WHEN** trusted Run 连续执行多个节点
- **THEN** 节点状态通过增量事件及时更新
- **THEN** 客户端不使用固定高频完整 RunView 轮询作为主要图谱更新机制

#### Scenario: 图谱 reducer 无法应用事件
- **WHEN** 客户端检测到事件 ID、Plan 版本或节点引用缺口
- **THEN** 客户端合并触发一次权威快照刷新
- **THEN** 快照到达后替换不一致的本地图谱状态
