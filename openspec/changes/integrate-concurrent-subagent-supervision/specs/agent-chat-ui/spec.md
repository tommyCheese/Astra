## ADDED Requirements

### Requirement: Chat UI 清晰展示并发子 Agent 和 Join 状态
系统 SHALL 同时展示多个 child 的目标、父级、运行/等待/终态、Join 关系、预算和关键等待原因，并 SHALL 使用户能够区分并发执行与串行步骤而不暴露隐藏推理。

#### Scenario: 两个 child 同时运行
- **WHEN** Run snapshot 包含两个 running child
- **THEN** 子 Agent 面板和执行图谱同时显示两个活动分支及各自状态
- **THEN** 汇总计数、预算和等待信息与权威快照一致

#### Scenario: 一个 child 等待而另一个完成
- **WHEN** sibling child 分别处于 waiting_approval 和 completed
- **THEN** UI 分别展示等待原因和完成摘要
- **THEN** UI 不把整个 Run 错误显示为只能等待该 child

#### Scenario: Join 已 ready 但尚未消费
- **WHEN** child 已完成且 Join 处于 ready 或 merging
- **THEN** UI 将其显示为正在汇合而不是根任务已经完成
