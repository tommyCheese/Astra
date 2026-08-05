## Context

Astra 后端是约 54,668 行生产 Python、24,714 行测试、820 个测试用例的模块化单体。它已经具备成熟的 Run 状态机、权限治理、并发子 Agent、Memory、Skills、调度与沙箱能力，但新增能力主要沿技术分层持续累积，造成核心概念被超大源码单元和跨层依赖遮蔽。

本次盘点得到的代表性事实包括：

- `runner/agent_loop.py` 为 3,292 行，`AgentLoop.run()` 单方法约 2,372 行，同时负责恢复、上下文、模型交互、权限、执行、持久化、评估、反思、完成和错误收敛。
- `repositories/runs.py` 为 2,685 行，`RunRepository` 约 1,926 行，混合 Run 生命周期、Plan revision、Step、ToolCall、Approval、Artifact、Sandbox、Turn、Memory 与 Event 持久化；同文件还承担约 324 行 API read projection。
- `db/models.py` 集中 40 余个 ORM record，`schemas/agent.py` 集中运行策略、计划、状态、结果和 API view 等多个概念族。
- `scheduling/dispatcher.py` 导入 `api/runs.py` 的私有调度函数；`repositories` 导入 `runner` 类型，同时 `runner` 广泛依赖 `repositories`；`runner` 与 `subagents` 双向依赖。目录层次与实际依赖方向不一致。
- Ruff 当前通过，说明现有问题主要超出语法和格式 lint 的覆盖范围；需要针对架构、复杂度、命名、类型边界和事务所有权建立护栏。

重构必须保持持久化 Run 的可恢复语义、effect-aware 权限安全边界、并发 fencing、历史数据库兼容、HTTP/SSE 契约和本地单体部署形态。当前活跃 OpenSpec change 仍可能修改相同区域，因此实施必须按切片重放最新主线行为，不基于长期分支一次性替换。

## Goals / Non-Goals

**Goals:**

- 让源码结构直接表达 Astra 领域词汇和真实执行流程，使开发者能够从用例入口沿单向依赖定位行为。
- 把超长函数和超大文件拆成职责单一、名称明确、局部可理解和可独立测试的组件。
- 使用 SOLID 作为职责与依赖设计准则，而不是为了模式本身增加抽象；优先高内聚、低耦合、显式数据流和最小公共接口。
- 把 HTTP、应用用例、领域策略、持久化、provider/tool adapter 和应用装配分开，使副作用和事务边界清晰。
- 在不改变产品语义的前提下，以 characterization tests 和契约测试保护安全、并发、恢复和对外协议。
- 用自动化规则阻止新的反向依赖、循环依赖、巨型函数、无边界动态字典和失控公共 API。

**Non-Goals:**

- 在同一 change 中增加新产品能力、重做前端或改变现有 API/SSE 语义。
- 将模块化单体改成微服务、引入消息中间件，或把进程内调度改成分布式任务系统。
- 为追求“纯 DDD”而给每个类增加接口、工厂或抽象基类。
- 一次性重写全部代码并在最后统一验证，或仅通过改名和移动文件制造重构完成的表象。
- 修改数据库逻辑结构；确有必要的数据模型变更必须单独提出并迁移。

## Decisions

### 采用按能力纵向切分的模块化单体

目标结构以业务能力为主、技术角色为辅：

```text
app/
  bootstrap/              # 唯一组合根、应用生命周期与依赖装配
  platform/               # config、database、http、observability 等基础设施
  run_management/         # Run 创建、生命周期、审批、事件与读取投影
  agent_runtime/          # root agent 执行阶段与状态机
    phases/
  planning/               # plan domain、scheduler 与 revision
  subagents/              # 委派、预算、执行、join、恢复
  permissions/            # effect analysis、authorization 与 credentials
  conversations/
  scheduling/
  memory/
  skills/
  tools/
  workspaces/
  artifacts/
```

每个能力包可包含 `domain`、`application`、`ports`、`infrastructure` 和 `api` 中真正需要的部分，但不会机械创建空层。领域规则不得导入 FastAPI、SQLAlchemy 或具体 provider；应用服务依赖窄端口；基础设施实现端口；`bootstrap` 是唯一允许了解全部具体实现的组合根。

备选方案是维持全局 `api/runner/repositories/db/schemas` 技术分层，只拆文件。该方案能缩短 diff，但同一用例仍横跨多个大目录，反向依赖也会继续出现，因此不采用。

### 用显式阶段管线替代巨型 AgentLoop 方法

保留确定性 Runtime 控制权，但将单次迭代表示为类型化 `ExecutionContext` 在固定阶段间流动。阶段至少覆盖：恢复/加载、上下文组装、模型决策、行动解析、权限授权、工具或子 Agent 调用、观察归一化、进度评估、反思/重规划、完成验证和终态收敛。

`AgentRunOrchestrator` 只负责阶段顺序、循环预算、取消检查和 outcome 路由；每个阶段通过窄输入/输出协议表达 `continue`、`wait`、`complete`、`blocked` 或 `failed`，不能隐藏提交、调度或权限绕过。横切的审计、计量和事件由显式协作者处理，而不是散落在条件分支中。

备选方案是把现有方法按行数提取为大量私有函数。它会缩短主方法，却仍共享过多隐式局部状态和 Repository，无法形成可测试的职责边界，因此仅作为短期迁移手段。

### Repository 按聚合与持久化职责拆分，事务归应用用例所有

拆分 `RunRepository` 为命名明确的端口/实现，例如 `RunStore`、`RunLifecycleStore`、`ApprovalStore`、`ToolCallStore`、`RunEventStore` 和专用 `RunQueryService`。最终名称以领域术语表为准，避免 `Manager`、`Helper`、`Utils` 等无职责名称。

Repository 方法执行查询和状态转换，但不自行提交。跨多个 Repository 的原子用例由应用服务通过同一 Unit of Work / session 明确 `commit` 或 `rollback`；外部网络等待与模型等待不得持有无必要的数据库事务。命令模型与读取投影分离，API view 组装不放在写 Repository 中。

备选方案是保留大 Repository 并用 mixin 分类。Mixin 会隐藏能力来源、扩大可见接口并保留单对象多职责，不采用。

### 拆分 ORM 与 schema，但保留数据库和外部契约兼容

ORM record 按能力移动到独立模块，通过一个 metadata 聚合入口确保 Alembic 能加载全部模型。迁移期间 `app.infrastructure.db.models` 可短暂 re-export，但新增代码必须直接导入拥有该模型的能力模块；re-export 在所有调用点迁移后删除。

Pydantic 模型按 `run_policy`、`planning`、`execution_state`、`tool_invocation`、`run_result` 和 API request/view 等概念族拆分。持久化 JSON、provider payload 和 API DTO 在边界显式转换，不共用一个“万能 schema”。

备选方案是立即修改数据库表和生成全新 schema。当前问题不要求改变存储结构，这会扩大风险，因此不采用。

### 通过应用服务消除跨层与跨能力反向导入

`ScheduledRunDispatcher` 调用公开的 `RunApplicationService.start_run()`，不导入 API handler 或 `_schedule_run`。Repository 不导入 runner 的具体类；共享的稳定类型移动到拥有语义的 domain/contract 模块。Root runtime 与 subagent runtime 共享的执行协议位于中立 contract 包，由两者依赖，而不是互相导入实现。

所有依赖规则由机器检查：`api -> application -> domain/ports`，`infrastructure -> ports/domain`，具体实现之间的装配只发生在 `bootstrap`。对确有必要的跨能力访问，使用公开 facade 或显式 domain/application contract，不使用对方私有函数。

### 可读性规则以语义为主、指标为护栏

命名使用统一术语表：一个概念只有一个首选名称；布尔值使用可判定前缀；集合使用复数；ID 带实体限定；时间带单位/时区语义；状态转换使用动词。`data`、`item`、`result`、`state`、`payload` 等名称只允许出现在作用域极小且语义由边界明确限定的位置，跨越多个步骤时必须使用领域名称。注释记录原因、不变量、风险与兼容约束，不复述语句。

指标不会替代设计评审，但作为防回退门槛：新增或实质修改的生产函数默认不超过 60 行且圈复杂度不超过 10；CI 硬限制为 100 行和复杂度 15。生产模块默认不超过 500 行，硬限制 800 行；生成代码和 Alembic migration 豁免。超过默认预算需要在架构例外清单中说明单一职责仍成立的理由、owner 和清除期限，硬限制不得豁免。

### 测试按公共行为和职责边界重组

在移动代码前冻结关键 characterization：HTTP/OpenAPI、SSE 顺序、Run 状态转换、审批 exactly-once、取消、恢复、事务提交/回滚、权限衰减、并发 fencing、工具审计、历史 JSON 和 ORM metadata。测试通过公共用例或协议断言行为，避免绑定私有调用顺序。

每个阶段组件拥有窄单元测试；应用服务使用 fake ports 测试编排；真实 Repository 使用数据库集成测试；端到端测试只覆盖关键跨层链路。现有 820 个测试是迁移基线，但测试数量本身不作为等价证明。

### 应用装配与 HTTP 横切关注点集中管理

将 `create_app()` 分为应用工厂、依赖容器、生命周期协调器、router 注册、middleware 和 exception handler 模块。启动和关闭服务使用显式依赖顺序，并对部分启动失败执行逆序清理。业务模块不通过任意 `app.state` 查找依赖；FastAPI dependency/provider 只读取类型化容器。

### 分阶段替换，不保留双实现

每个迁移切片遵循“characterize → 新端口/实现 → 调用方切换 → 等价验证 → 删除旧路径”。兼容 re-export 和 adapter 必须有删除任务，不允许形成永久双实现或 shadow behavior。大型文件只有在其旧职责已迁空且所有调用点切换后删除。

## Risks / Trade-offs

- [活跃 change 与重构修改同一区域，产生高冲突率] → 以小而完整的能力切片合并；每个切片开始前重放主线测试和依赖图，不长期维护巨型重构分支。
- [拆分过程中无意改变权限、恢复或并发语义] → 先建立安全与状态机 characterization tests；类型化阶段 outcome 必须覆盖所有现有终态；关键路径执行全套并发与故障注入测试。
- [抽象数量增加反而降低可读性] → 只在存在真实替换点、独立策略或副作用边界时引入 Protocol；单实现纯函数保持具体；架构评审检查调用链深度和概念数量。
- [行数/复杂度规则被机械提取函数规避] → 指标只做门槛，评审仍要求高内聚、明确命名、最小参数和无隐藏共享状态。
- [模块移动破坏内部脚本或测试导入] → 搜索仓库内全部 `app.*` 消费者，提供短期 re-export；公开 API 不变，未公开 Python 导入只保证迁移窗口。
- [ORM 拆分导致 metadata 漏载或关系解析失败] → 增加 metadata table-set 快照、Alembic no-diff、全新数据库和历史数据库启动测试。
- [分阶段迁移出现旧新路径并存] → 兼容层登记 owner/截止阶段；架构检查禁止新增对旧路径的依赖，最终阶段检查零兼容层。
- [全量重构耗时并阻塞功能开发] → 每个 phase 产生可独立合并且不降低代码质量的终态；先处理反向依赖和最高复杂度热点，避免横向移动全部文件后才获益。

## Migration Plan

1. 记录基线：生成模块/依赖/复杂度清单，补齐关键外部契约和状态机 characterization tests，建立术语表与目标模块地图。
2. 建立护栏：加入依赖、循环、复杂度和类型边界检查；对现有债务采用冻结基线，只禁止恶化并逐阶段收紧。
3. 提取组合根和应用服务：拆分 `main.py`，建立类型化容器；提取统一 Run 创建/派发服务，切断 `scheduling -> api`。
4. 重构 Agent runtime：先引入类型化 execution context/outcome，再逐阶段迁出 `AgentLoop.run()`，最后删除旧分支和隐藏状态。
5. 重构持久化：按职责拆分 Run Repository 和 read projections，明确 Unit of Work；拆分 ORM 模型并验证 Alembic metadata 无变化。
6. 重构 contracts：按概念拆分 `schemas/agent.py`，收紧动态字典和字符串状态，迁移 root/subagent 共享协议以消除双向依赖。
7. 依次处理 model provider、web tool、skills、memory、API 等剩余热点；每个切片同步拆分对应测试。
8. 删除 re-export、adapter 和旧模块，收紧质量门槛到目标值；更新架构文档并运行完整测试、lint、架构检查和迁移验证。

任一阶段均可通过回退该阶段的调用方切换恢复旧路径；因为不改变数据库结构和外部契约，不需要数据回滚。若某切片发现必须改变公开行为或持久化语义，应停止并创建独立 OpenSpec change。

## Open Questions

- 已解决：依赖与复杂度门禁复用标准库 AST 盘点器，配置、冻结基线和例外均为可审阅 JSON。这样无需引入与 Ruff 重叠的重量依赖，并能以完整模块路径报告 forbidden edge、新循环连接和债务增长；它只承担项目特定规则，不尝试替代 Ruff。
- 已解决：不对 54k 行历史代码一次性启用全局 strict mode。架构检查从 `bootstrap`、`run_management` 和共享 execution contracts 开始强制公共参数与返回类型完整，并随迁移扩大；Ruff 同时禁止无错误码或失效的 suppression，冻结基线禁止增加 `type: ignore`。
