# Astra 后端领域术语表

本表是后端命名的规范来源。代码中的历史表名或兼容字段可以暂时保留，但新增名称必须使用“首选术语”。

| 首选术语 | 精确定义 | 避免使用 |
| --- | --- | --- |
| Conversation | 用户可见的长期会话容器；数据库当前由 `TaskRecord`/`tasks` 承载，可包含多个 Run 和一个持久 Workspace | 把一次执行称为 Task；在新应用代码中继续扩散 `task` 同义词 |
| Run | 为一个用户目标创建的、可持久化和恢复的一次执行实例 | Job、Task execution（scheduled job 除外） |
| Run goal | 创建 Run 时需要完成的用户目标 | query、prompt、description（除非确指协议字段） |
| Agent execution | Run 内一个 root 或 child Agent 的持久执行身份、谱系与生命周期 | worker、agent run |
| Turn | Agent execution 中一次模型决策及其 observation/evaluation/reflection 检查点 | iteration、round |
| Plan | 某个版本的有向执行图及其生命周期 | workflow、task list |
| Plan node | Plan 中描述意图、能力需求、依赖和成功条件的逻辑工作单元 | step（新代码） |
| Node execution | Plan node 的一次实际执行尝试，包含 claim、fencing、phase、checkpoint 和结果 | node run、worker task |
| Execution step | standard 模式中的线性执行记录；仅用于当前 `StepRecord` 契约，禁止表示 Plan node | plan step |
| Tool invocation | Agent 请求调用一个冻结工具输入的应用层意图 | action、call（缺少 tool 限定时） |
| Tool call | Tool invocation 的持久化执行与审计记录 | invocation record（无必要时） |
| Effect plan | 对具体工具输入可能产生的副作用和资源影响的冻结分析 | permission list、risk data |
| Authorization decision | Permission Engine 对本次 effect plan 返回的 allow/ask/deny 决策 | approval（ask 之前） |
| Approval request | ask 决策后持久化、等待用户处理且绑定冻结输入的请求 | permission request |
| Approval grant | 对 once/similar/conversation scope 的可消费授权记录 | approval token |
| Execution context | 一个执行阶段所需的显式、类型化上下文 | ctx、state、data |
| Stage outcome | 阶段返回的 continue/wait/complete/blocked/failed 判别结果 | result、status dict |
| Agent state | Run 的版本化推理状态，包括事实、标准进度和失败指纹 | context、runtime state |
| Run result | Run 终态对外返回的规范化交付结果 | final payload、answer dict |
| Final answer | Run result 中面向用户的摘要、发现、引用和 caveats | response |
| Execution profile | 创建 Run 时冻结的执行模式和行为配置 | mode config |
| Reasoning policy | 决策、反思、预算、plan 和 subagent 行为的冻结策略 | profile（单独使用） |
| Model policy | 模型 provider、模型、thinking 和 context window 的冻结配置 | model config（进入 Run 后） |
| Runtime profile | 可构建、激活和回滚的沙箱依赖与镜像配置 | execution profile |
| Application service | 一个公开用例的事务与协作者编排入口 | manager、helper |
| Port | 应用/领域层依赖的窄能力协议 | interface（无语义限定时） |
| Adapter | 对数据库、HTTP、模型 provider 或工具 runtime 等外部机制的 port 实现 | wrapper |
| Repository / Store | 对一个聚合或明确持久化职责的查询和变更接口；不拥有跨用例 commit | DAO、manager |
| Query service | 只读取并构建 typed projection 的组件 | read repository（混合写入时） |
| Unit of Work | 应用用例拥有的明确事务边界 | session helper |
| Workspace | Conversation 级、跨多个 Run 保留的受控文件空间 | working dir |
| Sandbox job | 隔离工具执行的单个 runtime 实例 | container（除非确指容器实现） |
| Artifact | 经验证、存储并可交付的不可变输出引用 | file、attachment（泛称） |
| Evidence | 支撑结论、可追溯到工具调用或 Agent execution 的规范化事实片段 | source data |
| Scheduled job | 持久化的自动触发配置 | task、schedule task |
| Scheduled run | Scheduled job 的一次触发与投递记录 | run（缺少 scheduled 限定时） |

## 命名规则

- ID 必须携带实体名称，例如 `run_id`、`plan_node_id`，不得在跨步骤逻辑中只写 `id`。
- 集合使用复数；映射名称说明 key/value，例如 `runs_by_id`，不写 `data`。
- 布尔值使用 `is_`、`has_`、`can_`、`should_` 等可判定前缀。
- 时间注明语义和必要单位，例如 `started_at`、`timeout_seconds`、`duration_ms`。
- `payload` 仅用于 HTTP/provider 边界的短作用域原始载荷；验证后立即转换成领域名称。
- `result` 仅用于紧邻调用且类型已完整表达语义的短作用域；跨步骤使用 `run_result`、`tool_output`、`authorization_decision` 等名称。
- 注释解释设计原因、安全不变量、兼容约束和反直觉行为，不复述下一行代码。
