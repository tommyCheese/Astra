## 1. 基线与重构地图

- [x] 1.1 生成并提交后端生产模块的文件行数、函数长度、圈复杂度、公共符号和 import graph 基线清单
- [x] 1.2 建立领域术语表，统一 Task/Conversation、Run、Execution、Turn、Step、Node、Result、Outcome、Profile 与 Policy 等易混概念
- [x] 1.3 编写当前到目标模块的职责迁移地图，为每个旧热点指定目标能力包、owner 和删除条件
- [x] 1.4 搜索并登记仓库内所有直接导入后端内部 `app.*` 路径的生产、测试、脚本和 Alembic 消费者
- [x] 1.5 记录当前 820 个测试、Ruff、OpenAPI、Alembic metadata 和典型数据库的可复现绿色基线

## 2. 行为与安全 Characterization

- [x] 2.1 补齐 Run 创建、standard/trusted 执行、等待、完成、阻塞、失败和取消的状态转换 characterization tests
- [x] 2.2 补齐审批创建、冻结、一次消费、拒绝、恢复和重放拒绝的 exactly-once 契约测试
- [x] 2.3 补齐 SSE 事件提交后发布、顺序、恢复 cursor、终态和敏感信息过滤契约测试
- [x] 2.4 补齐 root/subagent 权限衰减、catalog 冻结、credential scope、并发 fencing 与取消传播测试
- [x] 2.5 补齐模型调用或外部网络等待期间不持有写事务的事务边界测试
- [x] 2.6 补齐历史 Run JSON、现有 SQLite/PostgreSQL schema、ORM metadata table set 与 Alembic no-diff 兼容测试
- [x] 2.7 为 application service、阶段组件和 Repository 测试建立可复用的 typed fakes/builders，减少对私有实现的 mock

## 3. 自动化架构与可读性护栏

- [x] 3.1 选择并接入可输出违规依赖路径的 import/layer 检查工具，声明组合根与允许依赖方向
- [x] 3.2 在 CI 中禁止新增生产模块循环、`scheduling -> api`、`repositories -> runner` 及 root/subagent 具体实现双向依赖
- [x] 3.3 配置函数 60 行/复杂度 10 与模块 500 行的默认预算，以及函数 100 行/复杂度 15 与模块 800 行的不可豁免硬限制
- [x] 3.4 为历史债务建立只减不增的冻结基线，并添加包含理由、owner 和失效期限的默认预算例外清单
- [x] 3.5 扩展 Ruff/类型检查以覆盖复杂度、无效 suppression、公共 contract 和不安全动态类型边界
- [x] 3.6 将 lint、架构、复杂度、类型、OpenSpec 和快速契约测试组合成一个本地与 CI 共用的后端质量命令

## 4. 组合根与 HTTP 应用边界

- [x] 4.1 创建类型化 application container，集中声明 session factory、runtime services、registries 和后台服务依赖
- [x] 4.2 从 `main.py` 提取 FastAPI application factory 与 router 注册模块，并保持现有路由/OpenAPI 不变
- [x] 4.3 提取本机访问边界、请求日志和 trace context middleware，使用具名组件替代内嵌闭包
- [x] 4.4 提取 Astra、validation、database 和 unknown exception mapper，验证现有 error envelope 与状态码
- [x] 4.5 提取 lifecycle coordinator，显式实现启动顺序、逆序关闭与部分启动失败清理
- [x] 4.6 将业务模块对任意 `app.state` 的访问迁移为 FastAPI dependency 提供的类型化容器接口
- [x] 4.7 删除 `main.py` 中已迁移实现并验证应用工厂在测试、开发和生产入口下行为一致

## 5. Run 应用服务与调度解耦

- [x] 5.1 定义 Run 创建、派发、恢复与取消的 application contracts 和窄端口
- [x] 5.2 将 `run_creation.py` 与 `api/runs.py` 中的用例编排合并到命名明确的 Run application services
- [x] 5.3 提取进程内 Run dispatcher，拥有后台 task 引用、调度、取消、清理和 shutdown 语义
- [x] 5.4 让 API handler 仅负责 request validation、application service 调用和 response mapping
- [x] 5.5 让 scheduled dispatcher 和 heartbeat 通过 Run application service 创建/派发执行，移除对 API 私有函数的导入
- [x] 5.6 为 API 请求、手动调度、scheduled job 和 heartbeat 添加共享用例契约测试，验证事务和工作区复用

## 6. Agent Runtime 类型化阶段

- [x] 6.1 定义 `ExecutionContext`、阶段输入、阶段输出和穷尽的 continue/wait/complete/blocked/failed outcome contract
- [x] 6.2 从 `AgentLoop.run()` 提取恢复、Run/Plan/Turn 加载和取消检查阶段
- [x] 6.3 将 `ContextAssembler` 拆为 conversation、memory、skill、plan 与 tool catalog 投影协作者，并为每个输入预算增加测试
- [x] 6.4 提取模型决策阶段，隔离 prompt composition、model invocation、usage metering、响应验证和 retry policy
- [x] 6.5 提取 action resolution 与 capability/tool selection 阶段，统一 root/subagent 可共享的 invocation intent
- [x] 6.6 提取 effect analysis 与 permission authorization 阶段，保持 deny/ask/allow、grant 和 frozen input 语义
- [x] 6.7 提取 tool/subagent invocation 阶段，将审计上下文、workspace、sandbox、artifact 和 tool-call 生命周期显式化
- [x] 6.8 提取 observation normalization 与 evidence/grounding 更新阶段，移除工具专用分支对主循环的渗透
- [x] 6.9 提取 progress evaluation、no-progress detection、reflection 与 plan revision 阶段
- [x] 6.10 提取 completion verification、memory candidate、final answer normalization 和终态收敛阶段
- [x] 6.11 实现只描述阶段顺序、预算与 outcome 路由的 `AgentRunOrchestrator`，并对所有 outcome 做穷尽测试
- [x] 6.12 将 standard、trusted、approval-resume 和 recovery 调用方切换到新 orchestrator，运行行为等价测试
- [x] 6.13 删除旧 `AgentLoop.run()` 路径及临时 adapter，确保没有单函数或模块超过硬限制

## 7. Root 与 Subagent 执行契约解耦

- [x] 7.1 提取 root/subagent 共用的 execution、invocation、completion、budget 和 lineage contracts 到中立所有者模块
- [x] 7.2 将 subagent executor 的决策、调用、观察、反思和完成逻辑对齐共享 contract，但保留权限衰减与独立生命周期
- [x] 7.3 用公开 facade 替代 `runner -> subagents` 和 `subagents -> runner` 的具体类导入
- [x] 7.4 拆分超长 `LocalAstraAgentExecutor.execute()` 与 `_call_tool()`，为每个阶段添加独立测试
- [x] 7.5 运行委派、join、预算、恢复、取消与并发测试并确认 import graph 无双向实现依赖

## 8. Run 持久化与读取投影拆分

- [x] 8.1 按 Run core/lifecycle、reasoning/waiting、turn/step、tool-call/approval、event 和 artifact/sandbox 职责定义 Repository ports
- [x] 8.2 迁移 Run 创建、profile freeze、reasoning state、waiting/resume 与 lifecycle transition 到专用 stores
- [x] 8.3 迁移 Step、AgentTurn 和 NodeExecution 持久化并统一状态转换名称和并发版本检查
- [x] 8.4 迁移 ToolCall、ApprovalRequest 与 ApprovalGrant 持久化，保持冻结、消费、撤销和 identity invalidation 原子性
- [x] 8.5 迁移 RunEvent 查询/写入与 commit-aware publish，保持 cursor 和批量读取性能
- [x] 8.6 将 artifact/sandbox 记录能力移动到其所属能力 Repository，Run store 只保留聚合引用
- [x] 8.7 创建专用 `RunQueryService` 和 typed projections，迁移 `run_to_view`、initial view、chat messages、agent tree 与 parallelism summary
- [x] 8.8 为跨 store 用例引入显式 Unit of Work/session 边界并移除 Repository 内部 commit
- [x] 8.9 迁移所有调用方到窄接口，删除巨型 `RunRepository` 与旧 projection helpers
- [x] 8.10 使用查询计数、并发和回滚测试验证拆分没有 N+1、部分提交或事件早发

## 9. ORM 模型与数据边界拆分

- [x] 9.1 按 conversations/runs/plans/executions/permissions/workspaces/memory/skills/evolution/scheduling 拆分 ORM model 模块
- [x] 9.2 建立显式 ORM metadata 聚合入口并更新 Alembic env、relationship resolution 与测试 fixtures
- [x] 9.3 提供临时 `app.infrastructure.db.models` re-export 并禁止新增消费者依赖该兼容入口
- [x] 9.4 迁移生产和测试导入到模型所有者模块，保持表名、列名、约束、索引和级联语义不变
- [x] 9.5 运行全新数据库、历史数据库、Alembic upgrade/no-diff 和 metadata table-set 验证
- [x] 9.6 删除 `app.infrastructure.db.models` 兼容 re-export 并确认 import graph 中不存在旧路径

## 10. Schema、状态与公共 Contract 拆分

- [x] 10.1 将 `schemas/agent.py` 按 run policy、planning、execution state、tool invocation、result 和 API views 拆分
- [x] 10.2 为状态、终态、阶段 outcome、ID、时间和预算引入拥有明确语义的枚举或值对象
- [x] 10.3 在 API、persistence JSON、model provider 和 tool provider 边界增加显式 mapper/validator
- [x] 10.4 收紧公共 domain/port/application 接口中的 `dict[str, Any]`、宽泛 `Any` 和魔法字符串
- [x] 10.5 移除现有无说明 `type: ignore`，使用类型收窄、overload、Protocol 或校验后 cast 表达真实不变量
- [x] 10.6 保留短期 schema re-export 并迁移全部消费者，验证 OpenAPI 与历史 JSON 序列化兼容后删除旧入口

## 11. 其余高复杂度能力切片

- [x] 11.1 按 provider transport、request mapping、response parsing、reasoning adaptation 和 retry/error policy 拆分 `model_client.py`
- [x] 11.2 按 search provider、fetch security、content extraction 与 result normalization 拆分 `tools/web.py`
- [x] 11.3 按 package validation、draft/revision storage、catalog activation 和 API projection 拆分 skills 实现与 `api/skills.py`
- [x] 11.4 按 candidate generation、validation、publication/rollback 与 job orchestration 拆分 memory consolidation 实现和 Repository
- [x] 11.5 按 domain policy、application use case、persistence 与 API projection 拆分 evolution、runtime profiles 和 schedules 剩余热点
- [x] 11.6 逐模块审查 permissions、workspaces、artifacts、conversation context 和 deliverables 的命名、职责与依赖方向并清除违规
- [x] 11.7 将对应大测试文件按公共行为和职责边界拆分，移除仅验证旧私有调用结构的测试

## 12. 清理、文档与最终验收

- [x] 12.1 删除所有过期 compatibility facade、re-export、adapter、重复实现、未使用类型和例外记录
- [x] 12.2 更新系统详细设计、后端模块地图、Agent 阶段图、事务边界、组合根说明和领域术语表
- [x] 12.3 更新贡献指南，说明命名、注释、模块职责、SOLID 使用边界、测试层次和架构例外流程
- [x] 12.4 将历史冻结阈值收紧到所有生产文件不超过 800 行、函数不超过 100 行且复杂度不超过 15
- [x] 12.5 运行完整 820+ 后端测试、Ruff、类型、架构、复杂度、OpenAPI、Alembic、SQLite 与 PostgreSQL 验证
- [x] 12.6 对关键 Run、审批、取消、恢复、scheduled job、subagent、sandbox 和 artifact 链路执行故障注入与性能回归测试
- [x] 12.7 复核公开 HTTP/SSE 与持久化语义零意外变更，并为任何必要外部变更创建独立 OpenSpec proposal
- [x] 12.8 完成最终可读性评审：从每个主要用例入口验证调用链、命名、事务、副作用和错误路径无需依赖旧架构知识即可理解

## 13. 去碎片化与真实 Agent 能力归属

- [x] 13.1 删除 `agent_runtime` 的兼容 contract re-export，让 root/subagent 只依赖 `execution` 的单一契约所有者
- [x] 13.2 删除无消费者的临时 Repository ports、重复 invocation pipeline 及绑定旧实现的测试
- [x] 13.3 将仅用于多重继承组合的 Run store 薄壳并入 Unit of Work，减少无业务语义的模块和类
- [x] 13.4 统一 Runtime Profile 的持久化实现，删除未接入生产路径的重复 Repository
- [x] 13.5 将离线 Memory 评估从生产 `memory` 能力移到 benchmark/test 支持边界
- [x] 13.6 收敛 `runner`、`agent_runtime`、planning 与 model provider 的概念所有权，迁移调用方并删除旧模块路径
- [x] 13.7 重新生成只减不增基线，更新模块地图并运行完整 lint、架构、OpenSpec、契约、数据库和 820+ 测试验收
