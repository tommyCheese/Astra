---
schema_version: 1
document: autodream
status: disabled
---
# Astra AutoDream Protocol

## Status

AutoDream 当前未启用。本文件只是未来记忆整理能力的 Git 管理协议，不会创建后台任务、调用模型、使用工具或修改任何数据库记录。

## Purpose

未来的 AutoDream 可以在受控后台任务中提出记忆去重、摘要压缩、冲突检测、置信度衰减、过期候选和知识沉淀建议。

## Allowed Work

- 基于可审计来源合并重复候选。
- 检测冲突并保留不同来源与置信度。
- 提出摘要、过期、降权或需要用户确认的修改候选。
- 记录读取范围、预算、候选变更和验证结果。

## Prohibited Work

- 不得修改 `IDENTITY.md`、`SOUL.md`、`MEMORY.md` 或本文件。
- 不得扩大工具权限、跨用户合并数据或创造没有来源的新事实。
- 不得因为本文件存在就自动执行任务。
- 不得在没有审计与撤销能力时删除高价值记忆。

## Future Execution Contract

启用 AutoDream 需要独立的调度、预算、用户和工作区隔离、变更候选、审批、审计及撤销机制。这些能力必须由后续 OpenSpec change 明确实现和授权。
