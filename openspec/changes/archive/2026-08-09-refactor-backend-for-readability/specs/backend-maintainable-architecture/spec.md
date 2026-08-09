## ADDED Requirements

### Requirement: 领域词汇必须一致且名称表达意图
后端 SHALL 维护一份与代码同步的领域术语表，并在模块、类型、方法、变量和状态名称中为同一概念使用一个首选术语。名称 MUST 表达业务角色、集合性、实体归属、布尔语义以及时间或计量单位；宽泛名称跨越多步逻辑时 MUST 替换为领域名称。

#### Scenario: 新代码引入已有领域概念
- **WHEN** 开发者新增或修改一个表示已有领域概念的公共类型、方法或模块
- **THEN** 名称使用术语表中的首选术语，且不会再引入含义相同的别名

#### Scenario: 局部数据离开窄作用域
- **WHEN** 一个名为 `data`、`item`、`result`、`state` 或 `payload` 的值跨越多个处理步骤或作为公共接口参数
- **THEN** 该值被重命名为能说明其领域含义和生命周期的名称

### Requirement: 模块必须围绕单一可描述职责组织
后端 SHALL 以业务能力和执行阶段组织生产代码，每个生产模块和类 MUST 能用一个不包含“以及”的职责句描述。超出规模预算的源码单元 MUST 被拆分到高内聚模块，不得通过无语义的 `utils`、`helpers`、mixin 或纯转发层规避限制。

#### Scenario: 修改高复杂度热点
- **WHEN** 实施触及 `agent_loop.py`、`repositories/runs.py`、`db/models.py`、`schemas/agent.py` 或其他超过硬限制的生产文件
- **THEN** 相关职责被迁移到命名明确的模块，并且最终生产模块不超过 800 行、实质修改函数不超过 100 行且圈复杂度不超过 15

#### Scenario: 职责需要独立测试
- **WHEN** 一段逻辑拥有独立业务规则、失败方式或副作用边界
- **THEN** 该逻辑位于具有窄输入输出的命名组件中，并可不经无关基础设施进行测试

### Requirement: 依赖方向必须单向且可自动验证
后端 SHALL 执行 `transport/API -> application -> domain/ports` 与 `infrastructure -> domain/ports` 的依赖方向，具体实现的组合 MUST 仅发生在组合根。业务能力不得导入其他能力的私有实现，Repository 不得依赖 runner 具体实现，调度与后台服务不得导入 API handler。

#### Scenario: 调度器启动 Run
- **WHEN** scheduled job 或 heartbeat 需要创建并派发 Run
- **THEN** 调度器调用公开的 Run application service，并且 import graph 中不存在 `scheduling -> api` 依赖

#### Scenario: CI 检查模块依赖
- **WHEN** CI 分析后端 import graph
- **THEN** 不存在违反声明层次的依赖或生产模块循环，并输出足以定位源模块和目标模块的失败信息

### Requirement: Agent 执行必须呈现显式阶段与总结果
Root Agent 执行 SHALL 由可读的 orchestrator 按明确阶段编排；阶段输入输出 MUST 类型化并覆盖继续、等待、完成、阻塞和失败等所有控制结果。权限判断、工具调用、持久化提交、取消检查与终态转换 MUST 在调用链上可见，不得由无关 helper 隐式触发。

#### Scenario: 阅读一次正常执行迭代
- **WHEN** 开发者从 Agent orchestrator 入口检查一次非终态迭代
- **THEN** 能按代码顺序识别上下文、决策、授权、执行、观察、评估、反思和完成验证阶段，而无需阅读单个超长方法

#### Scenario: 阶段请求暂停或终结
- **WHEN** 任一阶段返回 waiting、completed、blocked 或 failed outcome
- **THEN** orchestrator 通过穷尽式路由执行唯一合法状态转换、审计与返回路径

### Requirement: 持久化职责与事务所有权必须明确
Repository SHALL 按聚合或持久化职责提供最小接口，命令写入与 API read projection MUST 分离。Repository 方法不得自行隐藏事务提交；跨 Repository 用例 SHALL 由应用服务或显式 Unit of Work 控制 commit/rollback，并且外部网络或模型等待不得持有无必要的数据库事务。

#### Scenario: 多记录用例成功
- **WHEN** 一个应用用例需要原子修改 Run、ToolCall、Approval 和 Event
- **THEN** 所有写入使用同一显式事务并在用例边界只提交一次

#### Scenario: 多记录用例失败
- **WHEN** 该用例在提交前任一步骤失败
- **THEN** 同一事务中的全部变更回滚，且不会发布代表已提交状态的事件

#### Scenario: API 读取 Run
- **WHEN** API 需要返回完整 Run view
- **THEN** 专用 query/projection 组件组装读取模型，写 Repository 不包含 HTTP view 序列化职责

### Requirement: 跨边界数据必须类型化并保持单一所有者
公共 domain、port、application 和 API 边界 SHALL 使用命名类型、值对象、枚举或判别联合表达稳定结构。`dict[str, Any]` MUST 仅保留在真正开放的 JSON/provider 元数据边界并在进入领域逻辑前转换；字符串状态和无说明的 `type: ignore` MUST 被移除或以可验证的类型收窄替代。

#### Scenario: Provider payload 进入应用逻辑
- **WHEN** 外部 provider 返回开放 JSON payload
- **THEN** adapter 在边界验证并转换为应用拥有的类型，后续阶段不依赖 provider 原始字典形状

#### Scenario: 共享执行协议
- **WHEN** root runtime 与 subagent runtime 需要交换相同的执行概念
- **THEN** 双方依赖中立且单一所有者的 contract，而不是相互导入具体实现或复制相似 schema

### Requirement: 应用装配与生命周期必须集中且故障安全
后端 SHALL 使用唯一组合根装配具体依赖，并将应用工厂、生命周期、router、middleware 和异常映射拆为可独立理解的模块。启动顺序和关闭顺序 MUST 显式；部分启动失败 MUST 清理由当前进程成功启动的资源。

#### Scenario: 应用正常启动和关闭
- **WHEN** FastAPI lifespan 启动所有后台服务后收到关闭信号
- **THEN** 服务按声明依赖的逆序关闭，模型客户端与后台任务均被释放

#### Scenario: 中途启动失败
- **WHEN** 某个后台服务在前序服务成功后启动失败
- **THEN** 已启动服务被逆序清理，原始错误按现有错误契约传播

### Requirement: 重构必须保持外部行为与安全不变量
重构 SHALL 保持现有公开 HTTP/OpenAPI、SSE 事件语义、数据库表与历史数据兼容，以及 Run 恢复、审批 exactly-once、权限衰减、取消、并发 fencing、Workspace、Sandbox 和 Artifact 安全不变量。任何无法保持的外部或持久化语义变更 MUST 从本 change 移出并另立提案。

#### Scenario: 迁移一个执行切片
- **WHEN** 新模块替换现有 Run 执行、审批、恢复或工具调用路径
- **THEN** characterization 与契约测试在旧路径和新路径上产生等价的状态、持久化记录、事件顺序和安全决策

#### Scenario: 加载现有数据库
- **WHEN** 重构后的应用使用迁移前创建的数据库启动
- **THEN** Alembic metadata 不产生意外 schema diff，历史 Run 与相关记录仍可读取和恢复

### Requirement: 架构与可读性护栏必须进入 CI
后端 CI SHALL 检查 lint、import 方向、循环依赖、函数复杂度、函数与模块硬规模限制以及关键公共边界类型。规则 MUST 提供冻结基线和逐步收紧机制；默认预算的例外 MUST 记录理由、owner 和失效期限，硬限制不得豁免。

#### Scenario: 新变更恶化历史债务
- **WHEN** 一个 pull request 新增反向依赖、循环依赖，或使受影响源码单元超过硬限制
- **THEN** CI 失败并指出具体违规位置，即使仓库仍有登记在基线中的其他历史债务

#### Scenario: 默认预算确有例外
- **WHEN** 一个源码单元超过默认 500 行模块、60 行函数或复杂度 10 的预算但未超过硬限制
- **THEN** 合并前存在包含职责理由、owner 和失效期限的显式例外记录

### Requirement: 测试和文档必须映射真实架构
测试 SHALL 按行为与职责边界组织，区分纯领域单元测试、应用编排测试、Repository 集成测试和关键端到端契约测试。架构文档 SHALL 包含当前模块地图、依赖方向、组合根、事务边界、Agent 阶段图、领域术语表和例外清单，并与实现同步更新。

#### Scenario: 模块边界发生变化
- **WHEN** 一个迁移切片新增、移动或删除公共模块和职责
- **THEN** 同一切片更新对应测试位置、模块地图与依赖规则，文档中的路径可在仓库中解析

#### Scenario: 删除旧实现
- **WHEN** 某职责的所有调用方均已迁移且完整验证通过
- **THEN** 旧实现、兼容 adapter、re-export 和绑定其私有细节的测试被删除，不保留双实现
