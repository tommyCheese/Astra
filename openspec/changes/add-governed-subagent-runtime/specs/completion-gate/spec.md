## ADDED Requirements

### Requirement: Root Completion Gate 等待所有必需子 Agent
系统 SHALL 在 Run 进入成功终态前确认所有 required child joins 已达到允许终态，且不存在仍可能改变强制成功准则的活动 descendant。

#### Scenario: required child 仍在运行
- **WHEN** 父 Agent 提出终止意图但任一 required child 仍为 queued、running 或 waiting
- **THEN** Root Completion Gate 拒绝 completed 或 completed_with_warnings，并返回等待的 execution 引用

#### Scenario: 只有 optional child 未完成
- **WHEN** 所有强制准则和 required joins 已满足且仅有允许忽略的 optional child 未完成
- **THEN** Completion Gate 可按 policy 取消或分离该 child，并在结果中记录处理方式

### Requirement: Root Completion Gate 验证子结果谱系和汇合
系统 SHALL 验证被最终结果使用的 SubagentResult 已通过 child Completion Gate、output schema、Artifact/Evidence lineage、权限合规及父级冲突合并。

#### Scenario: 父级引用未验证 child 输出
- **WHEN** 顶层声明引用 failed、blocked 或 schema 无效的 child result
- **THEN** 该声明不满足对应成功准则，且 Root Completion Gate 不把自然语言摘要视为替代证据

#### Scenario: child 结果含未解决关键冲突
- **WHEN** 多个 child 对强制声明存在未解决冲突
- **THEN** Root Completion Gate 要求追加验证，或根据任务策略返回 blocked/completed_with_warnings 并明确披露冲突

