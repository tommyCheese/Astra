## ADDED Requirements

### Requirement: Agent 只在委派产生可验证收益时创建子 Agent
系统 SHALL 要求父 Agent 将委派决策绑定到顶层成功准则，并说明独立工作范围、预期收益、成功标准和停止条件；Runtime SHALL 在创建前执行确定性适用性门控。

#### Scenario: 父 Agent 提出并行委派
- **WHEN** 多个子问题可以独立完成和验证且结果将在明确 fan-in 汇合
- **THEN** 父 Agent 可提出多个 DelegationContracts，并保持最终合成责任

#### Scenario: 委派不能帮助成功准则
- **WHEN** child 目标无法映射到任何未满足的顶层成功准则
- **THEN** Runtime 拒绝委派并要求父 Agent 继续现有计划或重规划

### Requirement: 父 Agent 验证并合并子 Agent 结果
系统 SHALL 将 SubagentResult 视为带 provenance 的观察而非可信最终事实，并 SHALL 在合并前验证 schema、完成决定、证据引用、冲突和 join 完整性。

#### Scenario: siblings 结果一致
- **WHEN** required children 返回 schema 有效、证据充分且互不冲突的结果
- **THEN** 父级可把验证后的结果提升为共享事实并用于顶层完成评估

#### Scenario: siblings 结果冲突
- **WHEN** children 对同一关键声明给出不兼容结果
- **THEN** 父级保留结构化 conflict set，并在预算内验证、改派或向最终结果披露不确定性

### Requirement: 子 Agent 的推理状态彼此隔离
系统 SHALL 阻止父子或 sibling 直接修改对方的 AgentState、plan revision、scratchpad 和局部事实，并 SHALL 仅通过版本化委派输入、问题回答和 SubagentResult 交换状态。

#### Scenario: child 发现新线索
- **WHEN** child 在执行中发现超出自身范围但可能有价值的信息
- **THEN** 它将线索作为 open issue 或 evidence ref 返回父级，而不是直接修改 sibling 计划

