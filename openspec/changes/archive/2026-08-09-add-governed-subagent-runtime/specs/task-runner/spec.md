## ADDED Requirements

### Requirement: Run 持久化独立 Agent execution 树
系统 SHALL 为每个 Run 持久化一个 root AgentExecution 及零个或多个 descendant executions，并将相关 Plan、NodeExecution、Turn、ToolCall、Approval、Artifact 和 usage 关联到实际执行 Agent。

#### Scenario: 兼容现有单 Agent Run
- **WHEN** 一个 Run 未启用或未选择子 Agent
- **THEN** 所有执行记录归属 root execution，现有 API 行为和终态保持兼容

#### Scenario: 重新加载多 Agent Run
- **WHEN** 客户端或恢复器按标识读取含 children 的 Run
- **THEN** 系统返回持久化的 Agent 树、各自状态/checkpoint、Plan/Node 状态、结果和 lineage

### Requirement: Runner 分层协调 Agent 和节点并发
系统 SHALL 先在 Run 与部署限制内调度 AgentExecution 槽，再在每个 execution 的 allowance 内调度 Plan nodes，并统一应用 provider、工具、资源租约和预算背压。

#### Scenario: Agent 与节点槽竞争
- **WHEN** 多个 children 及其内部节点同时 ready
- **THEN** Runner 不超过任何 Run、provider、Agent 或 node 并发上限，并持久化等待原因

