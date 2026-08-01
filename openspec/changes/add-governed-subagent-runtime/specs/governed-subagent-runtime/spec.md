## ADDED Requirements

### Requirement: 子 Agent 只能通过冻结的委派契约创建
系统 SHALL 要求每个子 Agent 由结构化且冻结的 DelegationContract 创建，契约至少包含稳定请求标识、目标、成功准则、范围、输入引用、输出 schema、所需能力、资源范围、预算、截止时间和 join policy，且 MUST NOT 将任意自然语言 prompt 直接视为可执行委派。

#### Scenario: 创建有效子 Agent
- **WHEN** 父 Agent 提交满足 schema、权限、预算和深度约束的委派请求
- **THEN** 系统持久化不可变 DelegationContract，并返回关联的 child execution handle

#### Scenario: 拒绝不完整委派
- **WHEN** 委派请求缺少可验证成功准则、输出 schema 或明确范围
- **THEN** 系统拒绝创建子 Agent，并向父 Agent 返回机器可读的校验原因

#### Scenario: 幂等重试创建
- **WHEN** 同一父 execution 使用相同稳定请求标识重复提交委派
- **THEN** 系统返回已有 child execution，且不重复预留预算或创建 identity

### Requirement: 每个子 Agent 具有独立且可审计的执行身份
系统 SHALL 为每个子 Agent 创建独立 Agent identity 和 AgentExecution，并持久化 parent、Run、Task、Plan node、委派、深度和因果谱系。

#### Scenario: 工具调用归属子 Agent
- **WHEN** 子 Agent 发起模型或工具调用
- **THEN** 调用记录包含 child identity、agent execution、完整 delegation chain 和父 Run 关联

#### Scenario: 禁止跨 Task 形成父子关系
- **WHEN** 创建请求试图把不同 Task 的 identity 连接为父子 Agent
- **THEN** 系统拒绝该委派并记录安全审计事件

### Requirement: 子 Agent 的执行权限逐维衰减
系统 SHALL 将子 Agent 的 action、resource、Tool、Skill、Credential、network、data、Workspace、model 和 budget 范围限制为父级生效范围、Task/Run policy、显式 delegated scope 与服务端子 Agent policy 的交集。

#### Scenario: 请求未委派工具
- **WHEN** 子 Agent 选择父级可见但 DelegationContract 未委派的工具或 Skill
- **THEN** 候选解析或 Permission Engine 拒绝该选择，且不得通过模型重试扩大 Catalog

#### Scenario: 请求未委派凭据
- **WHEN** 子 Agent 需要访问外部服务但没有绑定 child identity 的有效 Credential Grant
- **THEN** 系统不向其上下文或工具运行时注入父级凭据

#### Scenario: 子 Agent 尝试自我提权审批
- **WHEN** 子 Agent 对自己的 ask 决策提出批准、充当 reviewer 或创建更大范围后代
- **THEN** 系统拒绝该操作且 deny 不能由用户以外的该执行链主体覆盖

### Requirement: 子 Agent 使用隔离且最小化的上下文
系统 SHALL 为每个子 Agent 构建带来源、摘要、hash、data label、用途和 token estimate 的 ContextManifest，并 SHALL 只装配完成委派所需的显式事实、Artifact/Evidence 引用及衰减 Catalog。

#### Scenario: 不复制完整父会话
- **WHEN** 系统启动子 Agent
- **THEN** 子 Agent 不接收父级完整消息历史、隐藏 reasoning、兄弟 scratchpad 或未选择的 Memory

#### Scenario: 通过引用传递大对象
- **WHEN** 委派输入或结果超过内联上下文阈值
- **THEN** 系统传递受权限控制的 Artifact 或 Evidence 引用，而不是在父子消息中重复完整内容

#### Scenario: 上下文项超出数据用途
- **WHEN** 某输入的数据标签或允许用途不包含该子 Agent 的委派目的
- **THEN** Context Composer 排除该输入并记录上下文缺口

### Requirement: 子 Agent 拥有独立可恢复的 Agent loop 和 checkpoint
系统 SHALL 使每个 AgentExecution 拥有独立状态机、模型上下文、plan/node namespace、checkpoint、heartbeat 和版本，且其恢复 MUST NOT 依赖原进程中的协程对象。

#### Scenario: 子 Agent 独立规划和执行
- **WHEN** child execution 从 queued 被认领
- **THEN** 它可在自己的预算和 Catalog 内规划、多轮调用工具、反思并验证委派目标，而不修改父 Agent 的私有状态

#### Scenario: 进程重启后恢复
- **WHEN** 运行中的 child heartbeat 过期且存在兼容 checkpoint
- **THEN** 恢复器从已提交 checkpoint 和工具结果继续，并使用 fencing token 拒绝旧 Worker 提交

#### Scenario: 非幂等结果未知
- **WHEN** 重启发生在不可证明幂等的外部调用已发送但结果未确认之后
- **THEN** child execution 进入 waiting 或 blocked，且系统不得盲目重放调用

### Requirement: 子 Agent 使用层级预算并原子结算
系统 SHALL 为每个 child 分配父预算的有界 envelope，并原子预留和结算 token、模型调用、工具调用、墙钟时间、成本和并发槽，同时保留父级完成与合成所需的最低预算。

#### Scenario: 成功预留预算
- **WHEN** 委派请求在父级剩余预算和所有服务端上限内
- **THEN** 系统一次性预留 child envelope，并在完成后按真实 usage 结算和释放未用余额

#### Scenario: 并发创建竞争预算
- **WHEN** 多个 sibling 同时请求的预算总和超过父级可委派余额
- **THEN** 只有原子预留成功的 children 被创建，其余请求返回预算不足且不超额消费

#### Scenario: child 创建后代
- **WHEN** 允许递归的 child 请求创建后代
- **THEN** 后代预算只能从该 child envelope 预留且不得突破 Run 总预算或最大深度

### Requirement: 子 Agent 返回类型化且可验证的结果
系统 SHALL 要求 child 以 SubagentResult 返回状态、摘要、schema outputs、Artifact/Evidence refs、claims、open issues、completion、usage 和 provenance，且父级 MUST NOT 仅凭自然语言完成声明接受结果。

#### Scenario: 子任务成功完成
- **WHEN** child Completion Gate 验证成功准则、output schema、引用存在性和证据要求
- **THEN** 系统持久化 completed SubagentResult，并使其可被父级结果合并器消费

#### Scenario: 子结果缺少必需证据
- **WHEN** child 声称完成但强制声明没有已接受 Evidence 或 Artifact 支持
- **THEN** child Completion Gate 不返回 completed，并记录未满足准则

#### Scenario: 大型交付物回传
- **WHEN** child 生成报告、代码、数据集或图像等大型输出
- **THEN** 输出进入受管 Workspace/Artifact 管线，SubagentResult 只携带稳定引用、摘要和 provenance

### Requirement: 子 Agent 具有明确且可恢复的终态
系统 SHALL 区分 `completed`、`completed_with_warnings`、`waiting_parent`、`waiting_approval`、`waiting_resource`、`blocked`、`failed` 和 `cancelled`，并持久化每次合法状态转换及结构化原因。

#### Scenario: 子 Agent 请求父级输入
- **WHEN** child 在契约范围内无法继续且需要父级提供信息
- **THEN** 它进入 waiting_parent、释放活动计算槽并持久化一个结构化问题和 continuation token

#### Scenario: 子 Agent 内部错误
- **WHEN** 不可恢复的内部或基础设施错误终止 child
- **THEN** 它进入 failed 并记录安全的错误分类和可否重试信息，而不得将其表述为业务 blocked

### Requirement: 取消和撤销沿委派树传播
系统 SHALL 使用版本化 cancellation epoch 阻止被取消 execution 的新认领，并将 Run 或父级取消传播到所有 descendants，同时保存已发生的不可逆效果。

#### Scenario: 用户取消整个 Run
- **WHEN** 用户取消含活动 children 的 Run
- **THEN** 系统停止创建和认领新工作，协作取消所有 descendants，并在超时后终止仍可中断的 sandbox/tool job

#### Scenario: 只取消可选 child
- **WHEN** 父级取消一个 optional child
- **THEN** 系统只取消该 child 及 descendants，且不取消无依赖的 siblings

#### Scenario: 取消前已提交外部效果
- **WHEN** child 在收到取消前已经完成不可逆外部写入
- **THEN** 系统保留 ToolCall、effect 和结果记录，并在父级结果中披露而不声称已回滚

