## Why

Astra 当前把 Agent Profile 指令、真实记忆数据、记忆召回审计、AutoDream 整理作业和 Agent 自进化候选混在“运行时”和“记忆”两个页面中，用户难以判断哪些是规则、哪些是强制设置、哪些是数据以及哪些会真正影响生产行为。需要按职责和用户任务重新组织设置，并把后端已有的记忆运行参数开放为明确、可持久化的控制项。

## What Changes

- 新增独立“Agent”设置类别，承载身份、表达、记忆原则和后台整理协议；原始 Markdown 作为高级编辑能力，并明确其提示词效力边界。
- “运行时”页面只保留隔离环境、依赖和基础设施状态。
- 将“记忆”重构为“记忆设置”“已保存的记忆”“整理与合并”三个用户任务；审计信息在已保存记忆的详情中渐进披露，避免重复列表和重复请求。
- 将 AutoDream 作为记忆库的后台“整理与合并”能力展示，而不是独立记忆类型。
- 将“自进化候选”和“生产晋升关闭”移到新的“实验功能 → Agent 改进”页面。
- 新增本机 Runtime 记忆设置 API，支持持久化控制记忆写入、跨任务召回模式、召回预算/阈值和 AutoDream 调度，并让修改立即作用于后续运行。

## Capabilities

### New Capabilities

- `agent-memory-settings-information-architecture`: 定义 Agent 指令、记忆控制、记忆数据、整理作业、审计和实验性 Agent 改进在设置界面中的清晰边界与渐进披露。

### Modified Capabilities

- `memory-management`: 允许本机用户通过受校验的持久化 Runtime 设置控制记忆写入、跨 Session 召回、召回预算和 AutoDream 调度。

## Impact

- 后端 Runtime Profile 服务、Runtime API、动态 Settings 和 AutoDream 服务生命周期。
- 前端设置导航、Runtime/Agent 页面、MemoryWorkbench、API 类型、国际化和响应式样式。
- 后端 Runtime/Memory 测试与前端 App/MemoryWorkbench 测试。
- 设置和记忆治理文档；不迁移或改写已有记忆、AutoDream 作业及自进化候选。
