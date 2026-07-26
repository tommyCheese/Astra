## ADDED Requirements

### Requirement: 并行节点事件使用有界增量批处理
系统 SHALL 保留每个 NodeExecution 事件的 Run 级顺序、Plan 版本和 attempt 身份，并 SHALL 合并短时间窗内的并行节点增量，避免事件风暴触发高频数据库提交、完整 RunView 请求或重复 React 渲染。

#### Scenario: 多个并行节点同时更新
- **WHEN** 多个 Worker 在一个短时间窗内发出开始、等待或完成事件
- **THEN** 服务端按持久化 RunEvent 序列发送可区分 execution 的增量
- **THEN** 客户端在一个动画帧内最多提交一次可见图谱状态更新

#### Scenario: 并发事件存在版本缺口
- **WHEN** 客户端收到未知 Plan 版本、未知 execution attempt 或不连续事件 ID
- **THEN** 客户端停止猜测合并并请求一次权威快照
- **THEN** 快照到达后替换不完整的活动 execution 集合

### Requirement: 最终回答等待并行完成屏障
系统 SHALL 允许节点级进度和安全摘要实时传输，但 MUST NOT 将任一分支的候选答案作为最终回答输出，直到 CompletionGate 确认全部必要并行分支和汇合节点完成。

#### Scenario: 一个分支提前生成候选答案
- **WHEN** 其他必要分支仍在运行或等待审批
- **THEN** 系统可以流式推送该分支的公开进度摘要
- **THEN** 系统不发送 Run 级 `answer.completed`

#### Scenario: 并行屏障完成
- **WHEN** 所有必要 execution 达到允许终态且 CompletionGate 通过
- **THEN** 系统从已接受的完整分支证据生成一次最终回答
- **THEN** 客户端按现有完成收敛合同退出 streaming 状态
