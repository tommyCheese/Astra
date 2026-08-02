## Context

此前没有 session 记忆类型。`cross_session_mode` 只是三态召回开关；`workspace` 记忆依赖未被创建路径设置的 `TaskRecord.workspace_id`，而实际 `TaskWorkspaceRecord` 与 Task 一对一。因此 workspace 既不能跨 Task，也不应继续作为当前产品作用域。

## Goals / Non-Goals

**Goals:**

- 用单一布尔开关替代三态召回。
- 新增可跨 Task、按浏览器会话隔离的 session 记忆。
- 删除生产 workspace 记忆并安全处理已有数据。
- 保留历史审计可读性并修正文档。

**Non-Goals:**

- 不建设长期项目级 Workspace。
- 不把 session 记忆扩大为跨浏览器会话或跨用户记忆。

## Decisions

### 1. 使用 recall_enabled

公开设置改为布尔 `recall_enabled`。旧 `on` 映射为 true，`off` 与 `shadow` 映射为 false，避免升级后 shadow 突然影响回答。

### 2. Session identity 属于 Run

前端在 `sessionStorage` 生成随机稳定 ID，并随每次创建 Run 发送。Run 保存该 identity；同一浏览器会话中不同 Task 可以共享 session 记忆，同一 Task 在新浏览器会话中不会误用旧 session 记忆。

### 3. Workspace 记忆迁回 Task

新写入和召回只支持 run、task、session、user。迁移根据 workspace 记忆的来源 Run 找到 Task，将其改为 task scope；无法确定来源的记录撤销。旧召回审计 payload 保持原样。

### 4. 保留历史 shadow 字段

数据库审计列和读取模型保留，但新召回事件总是 `shadow=false`。运行时只有关闭和启用分支。

## Risks / Trade-offs

- [sessionStorage 被清理] → 自动生成新 identity，旧 session 记忆不会越界召回。
- [旧 workspace 记录无法归属] → fail closed，撤销而不是扩大范围。
- [客户端未提供 session ID] → run/task 仍可工作，但 session 写入被拒绝。

## Migration Plan

1. 增加 `runs.memory_session_id`。
2. 迁移 workspace 记忆到来源 Task 或撤销。
3. 发布兼容旧设置读取的新 API 与前端 session identity。

## Open Questions

- 长期项目级共享应在独立 Workspace 产品模型中设计。

