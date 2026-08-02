## Why

Astra 的 Agent Profile 当前只能随代码发布，用户无法在运行时调整 Agent 的身份、表达方式和记忆治理协议。将受校验的 Profile 文档开放到本机 Runtime 设置，可以让用户定制后续任务使用的 Agent 行为，同时继续保留每个 Run 的不可变快照和权限边界。

## What Changes

- 在 Runtime API 中公开当前生效的 Agent Profile 文档、来源和版本信息。
- 支持用户校验并保存四类 Profile Markdown 文档；保存后仅影响新建 Run，运行中和历史 Run 继续使用原快照。
- 支持一键恢复随应用发布的默认 Profile。
- 在 Runtime 设置页提供分文档编辑、未保存状态、校验错误和恢复默认交互。
- 将用户修改持久化到现有本地 Runtime 配置文件；Git 中的内置 Profile 仍作为首次启动和恢复默认的权威基线。

## Capabilities

### New Capabilities

- `agent-profile-runtime-editing`: 定义本机用户读取、校验、更新和恢复 Agent Profile 的 Runtime API 与设置页行为。

### Modified Capabilities

- `agent-profile-management`: 新 Run 应从当前激活的内置或用户 Profile 创建不可变快照，并保持历史 Run 可精确恢复。

## Impact

- 后端 Agent Profile 加载器、Runtime Profile 服务和 `/api/runtime` 路由。
- Run 创建及 AutoDream 等调用 `load_agent_profile()` 的运行时绑定路径。
- 前端 Runtime 设置页、API 类型、国际化文本和相关样式。
- 后端 Agent Profile/Runtime API 测试与前端设置页测试。
