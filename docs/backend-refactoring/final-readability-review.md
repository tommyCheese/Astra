# 后端最终可读性评审

评审日期：2026-08-04。评审范围为 `backend/app` 的生产代码，以及 HTTP、持久化、调度、
Agent runtime、Subagent、权限、Workspace/Artifact 和外部 provider 边界。

## 主要用例入口

| 用例 | 可连续阅读的调用链 | 事务与副作用所有者 |
| --- | --- | --- |
| 创建与派发 Run | API/command/schedule → `RunApplicationService` → `RunUnitOfWork` → `RunDispatcher` | application service 先提交，dispatcher 后启动 |
| 执行 Agent | `RunExecution` → standard/trusted composition → canonical `run_loop` → mandatory action port | 阶段返回穷尽 outcome；外部等待不持有写事务 |
| 计划执行 | `PlanService` → `PlanScheduler` → node worker/coordinator | validation、调度和错误分别由 `planning.py`、`plan_scheduler.py`、`plan_errors.py` 拥有 |
| 审批恢复 | Run application service → approval store/grant store → dispatcher | 冻结输入、授权消费和恢复状态在显式 UoW 中完成 |
| Subagent 委派 | `SubagentSupervisor` → runtime operations → contract/budget/context → executor/join | catalog、scope、预算和 lineage 在创建 child 前冻结 |
| 取消与恢复 | Run/Subagent cancellation service → tool/sandbox fencing → execution transition | 不可逆 effect 和 result-unknown 明确进入取消报告 |
| Scheduled job | scheduling service/dispatcher → Run application port → deliverable catalog | scheduling 不依赖 HTTP；交付查询与 view projection 分离 |
| Workspace/Artifact | workspace runtime → change/checkpoint store → artifact collector | Workspace 是可变工作区；Artifact 是验证后不可变成果 |

## 命名与职责复核

- Task/Conversation、Run、Execution、Turn、Step、Node、Result、Outcome、Profile 和 Policy
  均按领域术语表使用；跨边界 payload 由 schema 或显式 mapper 校验。
- 旧巨型 `RunRepository`、单文件 ORM/schema、旧 Run 创建模块和模型客户端聚合模块已删除。
- 计划调度与错误不再从 `planning.py` 兼容导出；消费者直接导入概念所有者。
- `DeliverableCatalog.list` 仅编排 source loading 和 projection；scheduled/library 投影及单项 view
  构造具有独立名称。
- Repository/store 不再隐式提交；提交、回滚和 commit-aware event publish 由用例/UoW 所有。
- 架构例外列表为空；当前冻结基线只减不增，且不可豁免硬阈值为模块 800 行、函数 100 行、
  圈复杂度 15。

## 验收结论

主要入口可以在不理解旧架构的前提下沿命名后的 application、runtime、port/store 和 projection
边界导航。HTTP/OpenAPI、SSE、历史 JSON、ORM metadata 与 Alembic schema 没有发现意外外部语义
变化；本次无需创建额外外部变更提案。

2026-08-09 的 single-Loop 收敛进一步删除 `application.runner`、`fast_agent_runtime`、Fast/Trusted
镜像结果类型和 Run projector/query facade。standard、trusted root 与 trusted node 均调用同一个
`run_loop`；差异只存在于冻结策略与 capability registration。清理前结构为 61,167 行、302 个模块、
764 个类、2,461 个函数/方法和 1,190 个公共 symbol，全部低于重构前基线。

## 第二轮去碎片化

后续 import-graph 审计将 `app` 从 327 个模块收敛到 313 个，并删除约 1,800 行未接入真实调用链、
兼容转发或重复持久化代码。具体包括：兼容 execution contract、临时 Repository ports、第二套
invocation pipeline、两个纯多重继承 Store 壳、重复 Runtime Profile Repository，以及生产包中的
离线 Memory 评估。性能 benchmark 也移到生产包之外。Plan 统一归 `app.application.planning`，Agent 策略与
执行阶段归 `app.application.agent_runtime`，provider thinking 归 `app.infrastructure.model_clients`；当前 import graph 没有
生产模块循环或登记的 forbidden edge。

2026-08-10 的导航成本复核进一步把 Standard checkpoint 序列化与恢复合并到
`infrastructure/runtime/standard_checkpoint.py`，将 Trusted 运行组合值归还
`trusted_capabilities.py`，并让 Run API 直接使用 `projections/run_view.py`。三条高频路径各删除一个
无策略跳转，生产规模由 60,619 行、302 模块和 1,189 个公共符号降至 60,580 行、299 模块和
1,187 个公共符号。详见 `docs/backend-refactoring/navigation-cost-review.md`。
