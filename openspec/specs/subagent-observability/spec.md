# subagent-observability Specification

## Purpose
TBD - created by archiving change add-governed-subagent-runtime. Update Purpose after archive.
## Requirements
### Requirement: 所有子 Agent 事件携带稳定执行谱系
系统 SHALL 为子 Agent 事件附带 run、agent execution、parent agent execution、可空 node execution、Run sequence、Agent sequence 和 causation id，并 SHALL 支持从持久化记录重放。

#### Scenario: 并发 child 事件到达
- **WHEN** 多个 children 同时产生状态、工具或结果事件
- **THEN** 客户端可按 Run sequence 应用事件并按 Agent sequence 检查局部连续性

#### Scenario: 事件缺失或乱序
- **WHEN** 客户端检测到序列缺口或重连
- **THEN** 系统提供包含完整 Agent lineage 和当前状态的权威快照用于校正

### Requirement: UI 提供摘要优先的子 Agent 树
系统 SHALL 在主会话中提供紧凑的子 Agent 活动摘要，并在可信执行图或详情视图中提供可折叠的父子树和每个 Agent 的内部 Plan DAG。

#### Scenario: 多个 children 正在运行
- **WHEN** Run 有多个活动 child executions
- **THEN** UI 显示活动/等待/完成数量、总体预算和关键等待原因，而不逐条刷出所有内部事件

#### Scenario: 用户展开 child
- **WHEN** 用户选择一个子 Agent
- **THEN** UI 展示委派目标、创建原因、状态、允许能力摘要、预算、工具、Artifacts、结果和安全错误

### Requirement: 子 Agent 可观测信息不得泄露隐藏推理或 secret
系统 SHALL 对事件、快照、日志和 UI 投影进行清洗，并 MUST NOT 暴露隐藏 reasoning、长期凭据、原始敏感 Tool input、未授权 Workspace 路径或兄弟私有上下文。

#### Scenario: child 产生内部 reasoning
- **WHEN** 模型 provider 返回隐藏思考或内部 scratchpad
- **THEN** 系统只记录允许的摘要、决策类别和 usage 元数据，不把原文写入事件或前端

#### Scenario: 权限错误包含敏感资源
- **WHEN** 子 Agent 被拒绝访问 secret 或受保护路径
- **THEN** UI 展示清洗后的资源类别和原因代码，而不是 secret 值或完整路径

### Requirement: 用户可以理解并控制子 Agent 执行
系统 SHALL 展示每个 child 的创建原因、是否必需、取消影响和剩余预算，并 SHALL 允许有权用户取消允许独立取消的 child 或整个 Run。

#### Scenario: 取消 optional child
- **WHEN** 用户确认取消一个不影响 required join 的 optional child
- **THEN** UI 发送目标明确的取消请求并实时展示传播状态

#### Scenario: 尝试取消 required child
- **WHEN** 用户取消会使强制成功准则不可满足的 required child
- **THEN** UI 在确认前说明对父 Run 的影响，且后端 Completion Gate 仍负责最终终态

### Requirement: 系统记录子 Agent 效率、安全和质量指标
系统 SHALL 记录 delegation rate、拒绝原因、fan-out/depth、并行重叠、重复工作、child 成功率、父级合并失败、token/cost、延迟、取消、恢复和权限拒绝，并支持按 feature cohort 与单 Agent baseline 比较。

#### Scenario: 多 Agent 实验完成
- **WHEN** 一个启用子 Agent 的 Run 进入终态
- **THEN** 系统生成不含用户敏感内容的聚合指标，并关联 profile、policy、模型和 cohort snapshot

#### Scenario: 成本或失败越界
- **WHEN** 监控窗口中的成本、失败率、取消延迟或权限异常超过配置门槛
- **THEN** 系统能够告警并通过 kill switch 阻止新委派

### Requirement: 子 Agent 发布必须经过分层评测
系统 SHALL 在扩大启用范围前通过确定性协议测试、委派行为 eval 和相对单 Agent baseline 的端到端质量/延迟/成本评测。

#### Scenario: 只有 token 增加而无收益
- **WHEN** 多 Agent 方案相对 baseline 显著增加成本但未达到质量或延迟门槛
- **THEN** 该 cohort 不得升级为默认启用

#### Scenario: 评测路径不同但结果正确
- **WHEN** 子 Agent 使用与参考轨迹不同但合规的步骤达到可验证正确终态
- **THEN** 评测以最终状态、证据、约束和效率为主，不因轨迹不同单独判失败

