# 后端职责迁移地图

“能力所有者”表示最终负责该概念的包，不代表个人。所有兼容入口必须满足删除条件，不保留永久双实现。

| 当前热点 | 当前混合职责 | 目标能力所有者 | 目标模块 | 删除条件 |
| --- | --- | --- | --- | --- |
| `app/main.py` | app factory、service construction、lifespan、middleware、errors、routes | bootstrap/platform-http | `app/bootstrap/*`、`app/platform/http/*` | `main.py` 只保留稳定 app 导出，所有 app-state lookup 已迁移 |
| `app/api/runs.py` | HTTP、Run 用例、后台 task registry、SSE、审批恢复 | run-management | `app/api/runs.py`、`app/run_management/application.py`、`dispatcher.py` | 已完成：handler 只做协议映射，调度器不再导入 API |
| `app/run_creation.py` | validation、profile compilation、conversation、skills、persistence | run-management | `app/run_management/creation.py`、`continuation.py`、`settings.py` | 已完成：旧模块已删除，HTTP/schedule/skills 共用同一服务契约 |
| `app/runner/agent_loop.py` | 整个 root Agent 状态机及大量横切关注点 | agent-runtime | `app/agent_runtime/service.py`、`orchestrator.py` 及命名阶段模块 | 已完成：旧 runner 模块删除，Root Agent 服务与阶段由 `agent_runtime` 单一拥有 |
| `app/runner/engine.py` | profile routing、planning、recovery、coordinator、terminal errors | agent-runtime | `app/agent_runtime/application.py`、`routing.py` | standard/trusted/resume 契约等价且旧 engine 职责迁空 |
| `app/runner/node_worker.py` | node claim、上下文、模型、tool selection、result | planning/agent-runtime | `app/planning/node_worker.py` + shared execution ports | node worker 使用共享阶段 contract 且函数低于硬限制 |
| `app/runner/planning.py` | plan validation、patch、ready-node scheduling | planning | `app/planning/service.py`、`scheduler.py`、`revision.py` | 已完成：计划能力迁出 runner；简单错误类型并入 service，消费者使用真实所有者 |
| `app/runner/reasoning.py` 等策略模块 | Agent 推理、完成、审批、循环与结果适配规则 | agent-runtime | `app/agent_runtime/reasoning.py`、`completion_policy.py`、`approval.py`、`loop_control.py`、`result_adapters.py` | 已完成：策略由 Agent 能力拥有，runner 只保留执行协调 |
| `app/runner/model_reasoning.py` | provider thinking 能力与请求配置 | model-clients | `app/model_clients/reasoning.py` | 已完成：模型 provider 能力不再由 runner 拥有 |
| `app/subagents/executor.py` | child loop、permission、tool runtime、completion | subagents + shared execution contracts | `app/subagents/execution/*`、`app/agent_runtime/contracts.py` | root/subagent 无具体实现双向 import |
| `app/repositories/runs.py` | Run、waiting、revision、step、tool、approval、artifact、event、projection | run-management + owning capabilities | `run_*_store.py`、`run_unit_of_work.py`、`run_view_projection.py` | 已完成：旧 Repository 与 projection facade 已删除，store 无内部 commit |
| `app/db/models.py` | 全部 ORM records | 各能力 + platform-database | 各能力 `infrastructure/models.py`、metadata registry | 54 表 metadata/hash 等价，旧 re-export 零消费者 |
| `app/schemas/agent.py` | policy、plan、execution state、requests、results、views | 对应能力 owner | `contracts/*` 与能力 API schemas | OpenAPI/hash 经审核等价，旧 re-export 零消费者 |
| `app/runner/model_client.py` | transports、provider mapping、reasoning、retry、normalization | model-runtime adapter | `app/model_runtime/*` | provider matrix 与 error/usage tests 等价 |
| `app/tools/web.py` | search providers、fetch security、extract、normalize | tools/web | `app/tools/web/*` | SSRF、redirect、size、provider fallback 测试等价 |
| `app/memory/consolidation.py` | input freeze、candidate generation、normalization、validation | memory | `app/memory/consolidation/*` | publish/rollback 与 validation 状态机测试等价 |
| `app/repositories/memory_consolidation.py` | job、lease、publish、rollback、audit persistence | memory | `app/memory/consolidation/store.py` | 单一事务所有者明确且旧 Repository 删除 |
| `app/skills/storage.py` | package files、draft、revision、audit、builtin bootstrap | skills | `app/skills/packages/*`、`authoring/*`、`store.py` | import/publish/restore/revoke tests 等价 |
| `app/api/skills.py` | HTTP、diff/export、test run、metrics projection | skills | `app/skills/api/*`、`application.py`、`queries.py` | API handlers 无业务事务和跨层组装 |
| `app/scheduling/dispatcher.py` | scheduled delivery + API private dispatch | scheduling/run-management | scheduling application + Run application port | 已完成：`scheduling -> api` 边完全消失 |
| `app/deliverables.py` | 多种记录扫描、分类、projection | artifacts/deliverables | typed source loaders 与 projectors | 已完成：查询、scheduled projection、library projection 与单项 view 构造已分离 |

## 迁移顺序

1. 冻结外部契约和状态机行为。
2. 建立组合根、Run application service 和 in-process dispatcher。
3. 切断 `scheduling -> api` 等反向边。
4. 阶段化 root Agent，再对齐 subagent contract。
5. 拆分 Run persistence/query、ORM 和 schemas。
6. 按能力处理 provider、web、skills、memory 与其他热点。
7. 删除所有兼容入口，收紧门禁并更新系统设计。
