## ADDED Requirements

### Requirement: Chat UI 展示子 Agent 协作而不分裂主会话
系统 SHALL 保持 root Agent 为主对话发言者，并以紧凑、可展开的过程组件展示 children 的目标、状态、等待、预算、Artifacts 和结果。

#### Scenario: child 开始执行
- **WHEN** Run 创建一个或多个 child executions
- **THEN** Chat UI 在当前 Run 内显示子 Agent 汇总，而不创建伪造的独立用户会话或让 child 直接发布最终答案

#### Scenario: child 请求父级输入
- **WHEN** child 进入 waiting_parent
- **THEN** UI 默认显示父级正在处理该请求，只有父级将 Run 转为 waiting_user 时才向用户呈现澄清卡片

### Requirement: 用户可下钻子 Agent 审计和控制
系统 SHALL 允许用户从过程流或执行图查看 child lineage、委派契约摘要、能力/权限摘要、usage、工具和交付物，并在授权范围内取消目标 child。

#### Scenario: 查看 child 详情
- **WHEN** 用户展开一个 child execution
- **THEN** UI 显示经过清洗的结构化详情、父级关系、join policy 和取消影响，且不暴露隐藏 reasoning 或 secret

#### Scenario: 历史 Run 重放
- **WHEN** 用户打开已完成或中断的多 Agent Run
- **THEN** UI 从持久化快照重建相同的 Agent 树、关键时间线和终态，不依赖原 SSE 连接

