# Astra 后端目标架构

本文描述重构后的代码导航入口与不可破坏的边界。领域词义以
[`domain-glossary.md`](domain-glossary.md) 为准；历史迁移关系以
[`migration-map.md`](migration-map.md) 为准。

## DDD 分层

| 层级 | 路径 | 职责 |
| --- | --- | --- |
| Common | `app.common` | 配置、错误、共享契约与跨边界 Schema |
| Domain | `app.domain` | Agent Profile、Memory/Evolution 领域模型、执行端口与 Grounding 规则 |
| Application | `app.application` | 用例编排、Agent runtime、会话、权限、规划、调度、技能与工作区 |
| Infrastructure | `app.infrastructure` | ORM、Repository、模型客户端、工具、沙箱、插件和应用装配 |
| Interfaces | `app.interfaces` | HTTP API、middleware、依赖注入和协议映射 |

`common` 与 `domain` 不得依赖 `application`、`infrastructure` 或 `interfaces`；
`application` 不得依赖 `interfaces`。这些方向由架构检查自动约束。

## 主要模块地图

| 能力 | 公开入口 | 内部职责 | 不应承担 |
| --- | --- | --- | --- |
| 应用启动 | `app.infrastructure.bootstrap.application:create_application` | 组合依赖、路由、middleware、生命周期 | 业务规则、持久化查询 |
| HTTP 平台 | `app.interfaces.platform.http` | trace、本机访问、错误映射、请求日志 | 用例编排 |
| Run 管理 | `app.application.run_management.application:RunApplicationService` | 创建、派发、恢复、审批决定、取消 | Agent 阶段实现、HTTP 映射 |
| Agent runtime | `app.application.agent_runtime.services.loop:AgentLoop` | `models` 共享对象、`policies` 纯决策、`services` 阶段与用例编排 | provider 细节、跨用例提交 |
| Planning | `app.application.planning.service:PlanService` | Plan 校验、变更、revision 与 ready-node 调度 | Agent iteration、HTTP 映射 |
| Model clients | `app.infrastructure.model_clients` | provider transport、thinking 能力、请求映射与响应归一化 | Run 生命周期、权限决策 |
| Run 持久化 | `app.infrastructure.repositories.run_unit_of_work:RunUnitOfWork` | 组合窄 store、显式 commit/rollback | 自动提交、公共 read-model 拼装 |
| Run 查询 | `app.infrastructure.repositories.run_view_projection:RunViewProjector` | ORM 到 typed public projection | 修改 ORM、触发事务提交 |
| 权限 | `app.application.permissions` | effect analysis、allow/ask/deny、grant 与 credential scope | 工具执行 |
| Subagent | `app.application.subagents.supervisor:SubagentSupervisor` | 委派、谱系、预算、权限衰减、join/cancel | facade 转发层、反向依赖 root runner 实现 |
| Workspace / Artifact | `app.application.workspaces` / `app.application.workspaces.artifacts` | 受控可变工作区 / 不可变交付物 | 互相替代概念 |
| Scheduling | `app.application.scheduling` | 定时配置、claim、投递、心跳 | 导入 HTTP handler |

## Run 调用链

```mermaid
flowchart LR
    HTTP["HTTP / command / schedule"] --> Service["RunApplicationService"]
    Service --> UoW["RunUnitOfWork + narrow stores"]
    Service --> Dispatcher["RunDispatcher"]
    Dispatcher --> Engine["RunEngine"]
    Engine --> Runtime["AgentRunOrchestrator"]
    Runtime --> Stages["typed stages"]
    Stages --> Ports["model / tool / permission / workspace ports"]
    UoW --> DB[(Database)]
```

入口只做协议映射；application service 决定事务与派发顺序；runtime 只决定执行
阶段和 outcome。任何新能力若需要越过两个以上边界，应先定义位于概念所有者包的
窄 `Protocol`，再由组合根注入 adapter。

## Agent 阶段与 outcome

一次迭代依次经过：恢复与加载、上下文组装、模型决策、action resolution、effect
analysis、authorization、invocation、observation/evidence、progress/reflection、完成
校验与终态收敛。阶段只返回 `continue`、`wait`、`complete`、`blocked` 或 `failed`
之一；orchestrator 是 outcome 路由的唯一所有者。阶段不得自行启动后台 Run，也不得
将 HTTP response、provider payload 或裸 ORM 字典作为相邻阶段契约。

## 事务与副作用边界

- Repository/store 方法只修改并 `flush`；Run 用例由 `RunUnitOfWork` 显式提交或回滚。
- 创建或恢复 Run 必须先提交，再交给 dispatcher，避免后台 session 看不到状态。
- SSE 事件只在数据库提交成功后发布；回滚不得产生可见事件。
- 模型、网络、工具、sandbox 和文件系统等待前，先持久化 ownership/checkpoint 并结束
  写事务。外部调用结束后开启新事务记录结果。
- approval、continuation token、claim 和 fencing token 必须在同一原子状态转换中消费；
  重放返回冲突，不得部分提交。
- 失败恢复只回滚当前尝试；若恢复动作本身是公开状态（例如恢复原计划并刷新 token），
  application service 必须在返回错误 envelope 前明确提交恢复状态。

## 组合根

`ApplicationContainer` 是运行期依赖的类型化来源。application factory 构造 container、
注册 router/middleware，lifecycle coordinator 按依赖顺序启动并逆序关闭资源。业务模块
不得读取任意 `app.state` 字段；HTTP dependency 只暴露所需的类型化 service。测试通过
同一 factory 覆盖 container 端口，不复制生产组装逻辑。

## 可读性约束

模块名表达概念所有权，类名表达稳定角色，方法名表达用例或状态转换。公共调用链应能从
入口连续读到副作用边界；禁止永久 `compat`/`legacy` facade、跨包 re-export、巨型
`Manager`/`Repository` 和依赖私有函数的测试。默认预算为模块 500 行、函数 60 行、复杂度
10；任何代码不得超过模块 800 行、函数 100 行或复杂度 15。

顶层只允许 `common`、`domain`、`application`、`infrastructure`、`interfaces` 五个 DDD
分层包；各层内先表达业务/Agent 能力，再在大型能力内部使用 `models`、`validation`、
`policies`、`services`、`normalization` 或 `transports` 表达代码角色。只有形成多个同类模块时
才建立 role 子包；禁止 `utils.py`、`helpers.py`、`common.py`。单一消费者的 dataclass 与实现
保持同文件，无状态包装类、单实现抽象和旧路径 re-export 应直接删除。

复杂度预算是只减不增的架构契约。DDD 路径加深带来的纯导入换行、5 个分层包入口以及
保持旧 OpenAPI Schema ID 的惰性边界适配计入结构迁移基线；当前上限为 61,609 行、302 个
模块、764 个类、2,473 个函数/方法和 1,205 个公共 symbol。后续重构或新增能力不得通过
修改预算掩盖净增长。
