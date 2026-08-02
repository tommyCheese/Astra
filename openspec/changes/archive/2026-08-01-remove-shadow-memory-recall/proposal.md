## Why

Memory 的 `shadow` 召回模式没有可感知价值，却增加了配置和运行分支。现有 `workspace` 记忆又依赖未开放的共享身份，而产品中的 Task Workspace 实际与 Task 一对一；Astra 也从未实现真正的 session 记忆类型。

## What Changes

- **BREAKING**：移除 `cross_session_mode=off|shadow|on`，改为布尔设置 `recall_enabled`。
- 旧 `on` 配置迁移为启用，旧 `off` 或 `shadow` 安全迁移为关闭。
- 移除 shadow 运行分支；历史 shadow 审计仍可读取。
- 移除生产使用的 `workspace` 记忆作用域；现有 workspace 记忆迁回来源 Task，无法归属的记录撤销。
- 新增真正的 `session` 记忆作用域。每次浏览器会话持有稳定 session identity，同一 session 中的多个 Task 可以共享记忆。
- 文档明确 Task Workspace 只是与 Task 一对一的文件运行空间，不是跨 Task 记忆边界。

## Capabilities

### New Capabilities

- `memory-recall-control`: 定义持久记忆召回开关、session 作用域以及旧 shadow/workspace 数据的安全迁移行为。

### Modified Capabilities

- None.

## Impact

- Runtime settings API, Run session identity, database migration, context assembly, Memory namespaces, frontend settings and documentation, and test suites.

