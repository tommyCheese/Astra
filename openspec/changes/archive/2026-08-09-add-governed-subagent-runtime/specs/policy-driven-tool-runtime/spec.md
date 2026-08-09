## ADDED Requirements

### Requirement: 工具候选按实际 Agent execution 衰减
系统 SHALL 为每个 AgentExecution 从父级冻结 Tool Catalog、显式 delegated scope、Task/Run policy 和当前基础设施状态解析候选，并 SHALL NOT 因 root Agent 可用而向 child 暴露工具。

#### Scenario: child Catalog 构建
- **WHEN** 子 Agent 启动或从 checkpoint 恢复
- **THEN** Runtime 提供带版本和 digest 的衰减 Catalog，并拒绝恢复期间发生的权限扩大

#### Scenario: child 选择非候选工具
- **WHEN** 模型提出未出现在当前 child Catalog 的工具调用
- **THEN** Tool Router 拒绝执行、记录 identity 和原因，并不把该选择转为隐式审批

### Requirement: 子 Agent 工具调用保留完整委派执行上下文
系统 SHALL 在 ToolExecutionContext 和 PermissionRequest 中包含 child identity、agent execution、delegation chain、budget envelope、Context/DataFlow state 和 Workspace scope。

#### Scenario: 授权子 Agent 工具调用
- **WHEN** child 调用一个候选工具
- **THEN** `authorize_invocation()` 基于实际 child subject 和冻结 effect plan 返回唯一 allow、ask 或 deny 决策

#### Scenario: 工具 provider 尝试使用 root 身份
- **WHEN** child invocation 的 provider 或 adapter 丢弃 child identity 并尝试以 root subject 执行
- **THEN** Runtime fail closed 并记录 execution-context integrity 错误

