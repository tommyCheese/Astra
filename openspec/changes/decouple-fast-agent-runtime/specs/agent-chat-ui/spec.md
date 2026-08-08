## MODIFIED Requirements

### Requirement: Chat UI is the primary Agent interface
The system SHALL present both runtime kinds in one chat interface while projecting their actual capabilities: Fast Runs show user messages, compact tool activity, approvals and streamed answers; Trusted Runs additionally show plans, reflections, evidence and verification results.

#### Scenario: User submits a fast message
- **WHEN** the user sends a task with quick mode selected
- **THEN** the UI appends the user message and streams Fast Runtime events into the conversation
- **THEN** the UI does not create empty plan, reflection or verification placeholders

#### Scenario: Trusted Agent returns final answer
- **WHEN** a Trusted Run completes
- **THEN** the UI displays the final answer with its actual verification and evidence state

### Requirement: 对话策略按回答模式渐进呈现
系统 SHALL 仅在可信模式下提供推理强度、工具预算、计划、反思和验证策略控制。快速模式 SHALL 只展示模型、执行审批及其独立 Fast Runtime 设置，并 MUST NOT 将可信策略保存或发送给 Fast Run。

#### Scenario: 快速回答打开模型菜单
- **WHEN** standard 模式用户打开模型菜单
- **THEN** UI 允许选择模型并展示适用的最小 Fast Runtime 设置
- **THEN** UI 不显示或提交可信推理、计划、反思和验证字段

#### Scenario: 可信模式打开模型菜单
- **WHEN** trusted 模式用户打开模型菜单
- **THEN** UI 展示并允许修改持久化可信对话策略

### Requirement: 审计视图只展示真实 DAG
系统 SHALL 仅为实际持久化 Plan DAG 的 Trusted Run 展示图谱工作台。Fast Run SHALL 展示独立的简洁动作时间线，并 MUST NOT 根据工具事件推断或合成 DAG、验证或反思状态。

#### Scenario: 快速运行调用多个工具
- **WHEN** Fast Runtime 连续调用多个工具
- **THEN** UI 按时间顺序展示紧凑工具活动
- **THEN** UI 不创建 Plan node、可信执行图或“已校验”标记

#### Scenario: 可信运行存在 DAG
- **WHEN** Trusted Run 已持久化规范 Plan DAG
- **THEN** UI 展示对应图谱、节点执行与验证关联

