## Context

前两轮重构已经把 Agent 执行收敛为一个 233 行的 canonical Loop，并将后端生产代码降至约 60,619 行、302 个模块。当前局部函数规模受控，但一次 Standard 执行仍经过 `standard.py -> standard_state.py -> standard_recovery.py`，Trusted 组装经过 `trusted.py -> trusted_factory.py -> trusted_capabilities.py -> trusted_state.py`；Run 查询也存在 `query_service.py -> run_view.py` 的薄转发。它们增加了阅读跳转，却没有形成独立可替换边界。

本变更只处理能够通过调用关系证明为共同变化、单一所有者的切片。数据库事务、授权、审批、防重放、恢复、插件信任和 canonical Loop 端口保持原样。

## Goals / Non-Goals

**Goals:**

- 缩短 Standard、Trusted 和 Run read-model 三条高频调用链。
- 用 `checkpoint`、`capabilities`、`run view` 等运行语义替代宽泛的 `state` 或纯层次名称。
- 删除薄转发模块和字段复制对象，同时保持强类型。
- 使能力入口文件能够独立回答“从哪里开始读”和“下一步调用什么”。
- 净减少生产模块、符号和代码量，并保持所有外部行为。

**Non-Goals:**

- 不把 Runtime、FastAPI composition root 或 Repository 合并成大型文件。
- 不改变 Standard/Trusted 行为、Plan 模型、数据库 schema 或公开 API。
- 不在本变更中引入通用 facade、兼容 re-export 或新的插件机制。
- 不实施尚未进入主链路的 AutoDream、Evolution、Credential administration 或 governed hooks。

## Decisions

### 1. 以实际阅读链为删除单位

先记录入口、直接依赖和职责，再只合并满足以下条件的模块：单一能力所有者、没有替换实现、没有独立生命周期、调用方总是同时需要。相比按文件行数机械合并，这能减少跳转而不制造新的大文件。

### 2. Standard checkpoint 统一拥有序列化与恢复

将 `standard_state.py` 与仅由它调用的 `standard_recovery.py` 合并为 `standard_checkpoint.py`。Checkpoint 的加载、保存、pending action、幂等恢复和 result-unknown 决策属于同一个持久化协议；拆成两个文件没有形成可替换端口。合并后仍低于 500 行默认预算。

替代方案是保留两个文件并增加 package facade；这会新增转发层，与删除优先原则冲突。

### 3. Trusted 运行数据由 capability composition 所有

将 58 行的 `trusted_state.py` 中 `TrustedRuntime` 和 `TrustedRuntimeState` 就近放入 `trusted_capabilities.py`。这些类型只描述该 factory 组装出的 capability graph，不是独立领域状态或持久化模型。`trusted.py` 和 finalization 直接依赖其真实所有者。

### 4. Run read operations 与 projection 同址

将只包装 Repository 读取并立即调用 `run_view` 的三个函数移入 `projections/run_view.py`，删除 `query_service.py`。API 直接导入 read-model 所有者，避免“query service 只是调用 view”这一跳。

### 5. 不通过 `__init__.py` 隐藏路径

公开入口使用职责明确的真实实现模块；package `__init__.py` 不承担大规模 re-export。这样 IDE、traceback 和静态 import graph 都指向唯一所有者。

### 6. 用自动化指标验证导航收益

架构文档记录变更前后生产模块、行数、类、函数/方法和公共符号。关键调用链用模块序列记录，至少上述三条链各减少一个无策略跳转。完整架构检查和测试验证行为保持不变。

## Risks / Trade-offs

- [合并后模块接近默认 500 行预算] → 只合并同一协议，并保持函数硬限制；若超过预算则按序列化、决策等真实职责重新评估，而不是恢复转发文件。
- [内部 import path 变化影响测试或工具] → 一次性迁移全部生产和测试消费者，不保留兼容 re-export，并用全仓库搜索确认旧路径消失。
- [类型移动引发循环依赖] → `TYPE_CHECKING` 依赖继续指向 application stage，factory 和 adapter 只依赖 capability owner；实施后运行 import-cycle 检查。
- [代码量下降但阅读体验无改善] → 除结构指标外显式记录三条入口到实现的模块跳转变化。

## Migration Plan

1. 冻结当前架构指标和三条调用链。
2. 合并并重命名 Standard checkpoint 模块，迁移所有消费者。
3. 将 Trusted runtime composition values 移入 capability owner，删除旧模块。
4. 将 Run query functions 合并到 projection owner，删除薄 query service。
5. 更新架构地图、指标和测试 import，确认无旧路径或 compatibility export。
6. 运行架构检查、目标测试和完整后端测试；失败时按单个切片回退，不改变数据库或外部契约。

## Open Questions

无。更大范围的 Repository 聚合和 application package 扁平化需要独立使用关系审计，不纳入本次首批实施。
