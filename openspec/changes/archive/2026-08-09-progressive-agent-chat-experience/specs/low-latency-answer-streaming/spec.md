## ADDED Requirements

### Requirement: 过程增量使用低开销传输与渲染
系统 SHALL 对 `reasoning.summary.delta` 使用有界聚合，并 SHALL 使客户端在一个浏览器动画帧内最多提交一次可见过程文本更新。

#### Scenario: 模型连续输出推理摘要 chunks
- **WHEN** 模型在一个渲染帧内输出多个可审计摘要 chunks
- **THEN** 服务端可合并事件且客户端最多执行一次过程文本 state 更新

### Requirement: 过程事件不得触发高频完整快照刷新
客户端 SHALL 直接归约允许的过程事件，并 SHALL 对必要的 RunView 刷新进行合并；摘要 delta、heartbeat 和 stream ready MUST NOT 各自触发完整快照请求。

#### Scenario: 运行产生大量过程增量
- **WHEN** 客户端连续收到多个 `reasoning.summary.delta`
- **THEN** 客户端实时更新过程文本但不为每个 delta 请求 RunView
- **THEN** 稳定阶段事件或完成事件触发至多一次合并后的快照刷新

### Requirement: 首个过程反馈具有明确延迟预算
在 Run 创建成功并建立 SSE 后，系统 SHALL 立即提供 optimistic 过程状态；服务端从启动一个受控运行阶段到提交对应阶段事件的 Astra 额外处理时间 SHALL 小于 100ms。

#### Scenario: 首轮模型调用耗时较长
- **WHEN** 模型尚未返回任何决策内容
- **THEN** 用户已经可以看到本地 optimistic 状态和随后到达的服务端阶段状态
