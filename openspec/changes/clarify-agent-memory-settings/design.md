## Context

当前设置导航包含模型、工具、运行时、记忆、界面和隐私。Agent Profile 编辑器被嵌在 Runtime 依赖管理组件中；MemoryWorkbench 同时展示真实记忆、AutoDream 作业和 Agent Evolution，并在页面全局显示“生产晋升关闭”。后端已经具备记忆写入、跨 Session 召回、预算阈值和 AutoDream 开关，但这些值只来自启动环境，普通本机用户无法在 UI 中辨认或调整。

本变更依赖已完成的 `expose-agent-profile-runtime-config`，继续使用同一 Runtime JSON 和本机 API 边界。Astra 当前是单进程本机应用，Settings 实例由运行服务和后台服务共享。

## Goals / Non-Goals

**Goals:**

- 让设置导航按“Agent 指令、基础设施、记忆控制/数据/维护、实验性改进”划分。
- 为普通用户提供简洁的记忆管理视图，并把召回评分、状态版本和原始 JSON 渐进披露到审计视图。
- 让已有记忆运行参数可以受校验地持久化，并立即影响后续 Run。
- 在运行中安全启停 AutoDream 扫描器。
- 保留现有记忆、作业、候选和 API 数据模型，不做破坏性迁移。

**Non-Goals:**

- 不允许生产晋升 Agent Evolution 候选。
- 不新增跨用户共享、组织命名空间或认证模型。
- 不提供直接覆写记忆内容的就地编辑；纠错仍通过撤销或新版本完成。
- 不重新设计召回算法、AutoDream 提案格式或 Profile schema。

## Decisions

1. **设置导航新增 Agent 和实验功能。** Agent Profile 从 Runtime 移到 Agent；Evolution 从 Memory 移到实验功能。Runtime 只呈现基础设施。这是信息架构变化，不改变底层 Profile 或 Evolution API。

2. **Memory 页面使用四个产品任务。** “记忆设置”管理强制运行策略；“已保存的记忆”显示内容、范围、状态、来源和撤销；“整理与合并”显示 AutoDream；“活动与审计”显示完整召回、生命周期、版本和原始元数据。复用同一 MemoryWorkbench 数据客户端，通过视图参数控制可见数据和详情深度。

3. **记忆覆盖存入现有 Runtime JSON。** `memory_settings` 只保存允许用户修改的字段；读取时与应用启动默认值合并。备选数据库设置表更适合多用户部署，但当前本机单配置没有额外价值。

4. **将跨 Session 两个布尔值建模为三态模式。** UI/API 使用 `off`、`shadow`、`on`，服务映射到 `agent_memory_cross_session_enabled` 与 `agent_memory_cross_session_shadow`，避免用户组合出同时启用注入和影子评估的矛盾状态。

5. **动态修改共享 Settings，并协调 AutoDream 生命周期。** RuntimeProfileService 在启动时应用持久化覆盖；更新成功后原子写盘并修改共享 Settings。API 在 AutoDream 开关变化时调用其 `startup`/`shutdown`，保证 UI 状态与实际后台任务一致。

6. **只开放有稳定产品含义的参数。** 首版包括写入开关、跨 Session 模式、最大条数/Token、最低置信度/得分、AutoDream 开关、扫描间隔和最低候选数。模型调用预算、Lease、批大小等保留为运维配置。

## Risks / Trade-offs

- [运行中修改设置导致同一 Run 前后策略变化] → 明确设置作用于后续 Run；已创建 Run 的执行 Profile 不变，但后续新建 AgentLoop 读取更新后的共享 Settings。
- [启停 AutoDream 与正在处理的作业竞争] → `shutdown` 取消扫描任务但不删除持久化作业；重新启用时恢复过期 Lease 并继续安全扫描。
- [基础和高级记忆视图重复列表] → 复用同一组件和 API，区别只在详情密度；后续可按用户反馈合并入口。
- [原始 Markdown 仍可能被误认为硬策略] → Agent 页面显著标注“行为指令，不会开启能力或覆盖运行设置”。

## Migration Plan

1. 缺少 `memory_settings` 的 Runtime 配置继续使用启动环境默认值。
2. 用户首次保存后写入覆盖并立即用于后续运行。
3. 旧书签和 API 不受影响；原 MemoryWorkbench 数据接口保持兼容。
4. 回滚版本会忽略 Runtime JSON 中未知的 `memory_settings` 字段，再次升级后可恢复读取。

## Open Questions

无。多用户设置继承和真正生产晋升需要独立变更。
