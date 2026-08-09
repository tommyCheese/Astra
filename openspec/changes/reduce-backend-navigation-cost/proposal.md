## Why

Astra 的 canonical Loop 已经足够轻量，但外围应用能力仍存在入口分散、同一用例需要跨越过多模块、部分名称只描述技术角色而不表达运行语义的问题。现在需要在不削弱持久化、恢复、权限和审计边界的前提下，学习 Lyra 的直接命名和短阅读路径，让维护者能够从一个能力入口连续追踪到实际执行。

## What Changes

- 为 Runtime、Planning、Subagent、Memory 等高频能力建立见名知意的单一导航入口，并明确公开 surface 与内部实现。
- 统计并约束主要用例的模块跳转数；合并只做转发、总是共同变化且没有独立策略或替换价值的模块。
- 将宽泛或历史性命名替换为领域动作名称，删除旧路径、兼容 re-export 和重复别名。
- 继续消除同一信任边界内字段对字段复制的 schema、dataclass、domain object 与 projection，不以非类型化字典换取行数下降。
- 保留 canonical Loop、固定 capability slots、事务所有权、effect-aware authorization、approval integrity、审计、恢复和插件隔离边界。
- 记录优化前后的模块、代码量、符号数量及关键调用链跳转数，并运行完整架构与回归验证。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `backend-code-organization`: 增加直接命名、能力公开入口和关键用例低导航成本的可验证要求。
- `backend-accidental-complexity-control`: 将跨模块转发链、重复边界对象和调用链跳转数纳入删除优先与净简化验收。

## Impact

- 主要影响 `backend/app/application`、`backend/app/infrastructure/runtime`、相关 repositories，以及其测试和后端架构文档。
- 不改变公开 HTTP/OpenAPI、SSE、数据库 schema、持久化语义、插件协议或 Agent 结果契约。
- 生产内部 import path 可能直接迁移；不保留兼容转发模块。
