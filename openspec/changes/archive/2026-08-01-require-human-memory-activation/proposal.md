## Why

Astra 当前会在模型产生记忆候选并通过结构与来源校验后立即将其自动激活，用户没有机会在它进入后续召回前检查语义正确性、范围和敏感性。需要把最终生产激活权交给人工操作员，并提供集中、可审计的待确认列表。

## What Changes

- **BREAKING**：普通记忆提取器产生的新记录只保存为 `candidate`，不再自动转换为 `active`，因此未经人工确认的候选不会参与后续召回。
- 在记忆管理 API 中增加带状态版本检查、操作人和原因的人工激活能力，并保留现有撤销能力作为拒绝候选的终态操作。
- 在“已保存的记忆”中增加待确认列表和候选详情，让本机操作员检查内容、范围、种类、置信度和来源后逐条激活或拒绝。
- 记忆新版本也进入待确认状态；旧 active 版本在替代版本获人工确认前继续生效，避免未审核内容自动接管稳定键。
- 保留 AutoDream 的独立“验证后人工发布”流程；普通记忆确认不绕过 AutoDream 的发布和回滚边界。
- 更新帮助文档与审计文案，明确“保存候选”“人工激活”和“实际召回”是三个不同阶段。

## Capabilities

### New Capabilities

- `human-memory-activation`: 定义人工待确认队列、激活/拒绝操作、并发保护和审计行为。

### Modified Capabilities

- `memory-management`: 将普通持久记忆从自动激活改为人工确认后激活，并要求候选与已生效记忆在 UI 和召回路径中清晰隔离。

## Impact

- 后端 MemoryManager、MemoryRepository、Memory API schema/router、记忆生命周期审计与版本创建逻辑。
- 前端 MemoryWorkbench、API 类型和客户端、记忆设置/帮助文档、国际化与交互测试。
- 后端 repository、runtime、API 和 recall 回归测试。
- 不新增数据库状态或迁移，复用已有 `candidate`、`active`、`revoked` 与 `state_version` 字段。
