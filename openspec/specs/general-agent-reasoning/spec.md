# general-agent-reasoning Specification

## Purpose
TBD - created by archiving change add-general-reasoning-reflection-core. Update Purpose after archive.
## Requirements
### Requirement: Agent 创建可验证的任务契约

系统 SHALL 仅为 trusted Run 创建可持久化的完整 TaskContract；standard Run SHALL 不创建 TaskContract、规范计划或 AgentState，并 SHALL 直接进入共享 Agent Loop 的快速分支。

#### Scenario: 可信执行生成完整任务契约

* **WHEN** trusted 模式提交的目标已具备足够信息，可安全开始执行
* **THEN** 任务契约识别所有强制交付物及可验证的成功准则
* **THEN** 完整 DAG 和最终验证均通过稳定标识符引用这些成功准则

#### Scenario: 快速响应跳过契约和计划

* **WHEN** standard 模式创建新 Run
* **THEN** 系统不生成 TaskContract、Plan 或 AgentState
* **THEN** 系统立即开始回答或工具选择

#### Scenario: 可信执行关键歧义需要澄清

* **WHEN** trusted 模式缺失的信息将实质性影响交付结果、安全性或外部影响
* **THEN** 完整任务契约记录该歧义
* **THEN** 运行进入 `waiting_user`，而不是自行假设缺失的信息

### Requirement: Agent 状态具有统一规范且支持版本管理

系统 SHALL 为 trusted Run 维护统一且版本化的 AgentState；standard Run SHALL 不创建可信 AgentState。trusted AgentState 包含任务契约、Profile 引用、计划版本、成功准则状态、观察结果、失败指纹、预算和终止意图。

#### Scenario: 可信执行下一轮接收更新状态

* **WHEN** trusted 行动产生新的观察与评估
* **THEN** 系统在下一次决策前持久化状态更新
* **THEN** 下一次决策接收新的状态版本

#### Scenario: 快速响应工具调用完成

* **WHEN** standard 工具调用返回规范化结果
* **THEN** 系统进入下一次快速回答或工具选择
* **THEN** 系统不创建用于模拟可信生命周期的 AgentState

### Requirement: 计划采用可执行且可修订的图结构
系统 SHALL 将计划表示为带版本的有向无环图（Directed Acyclic Graph，DAG），其中每个步骤包含依赖关系、预期结果、关联成功准则、语义任务能力、风险以及执行状态；新生成或修订的计划 MUST NOT 指定具体工具或提供者身份。

#### Scenario: 依赖阻塞步骤执行

* **WHEN** 某个计划步骤存在尚未满足的必要依赖
* **THEN** 控制器不会执行该步骤
* **THEN** 控制器根据策略选择其他可执行步骤、重新规划（replan）、请求用户协助，或进入阻塞状态

#### Scenario: 重新规划保留有效成果

* **WHEN** 计划级反思替换了某条失效分支
* **THEN** 系统创建新的计划版本
* **THEN** 不受影响的已完成步骤及其证据继续保持关联并仍然有效

#### Scenario: 计划描述需求而不是工具

* **WHEN** 规划器认为某个步骤需要检索外部信息
* **THEN** 节点声明稳定的语义任务能力和预期结果
* **THEN** 节点不包含 `web_search` 或任何其他具体工具、提供者或插件身份

### Requirement: 每一次行动决策均以成功准则为导向
系统 SHALL 要求所有可执行决策包含目标步骤、简洁且适合审计的推理摘要、预期观察结果、引用的成功准则、风险等级、置信度，以及适用时的回退行为（fallback）；具体工具身份仅可在执行期行动决策中从当前候选集合选择。

#### Scenario: 工具决策信息完整

* **WHEN** 控制器在执行期选择某项工具操作
* **THEN** 决策明确指定候选集合中的具体工具、工具输入、目标步骤、预期结果以及该结果推进的成功准则
* **THEN** 执行前由候选约束和策略门（policy gate）验证该决策

#### Scenario: 决策缺少预期结果

* **WHEN** 某项拟议操作无法说明其期望获得何种有价值的观察结果
* **THEN** 该决策被判定为无效并拒绝执行
* **THEN** 控制器在预算范围内重新规划、请求澄清或进入阻塞状态

#### Scenario: 执行期替代工具选择

* **WHEN** 首选工具不可用、被策略拒绝或产生不满足预期的结果
* **THEN** 控制器可在剩余预算内从同一语义需求的其他当前候选中选择替代工具
* **THEN** 无需仅因具体工具变化而重写逻辑计划节点

### Requirement: 观察结果统一归一化并依据预期进行评估
系统 SHALL 通过共享工具边界归一化所有模式的工具结果与失败。Trusted Runtime SHALL 针对活动节点预期生成 Evaluation；Fast Runtime SHALL 将规范化结果直接提供给下一次模型决策，不创建可信 Evaluation。

#### Scenario: 可信工具成功但未满足节点意图
- **WHEN** trusted 工具调用成功但观察未满足活动节点预期
- **THEN** Evaluation 为 mismatch、partial 或 inconclusive
- **THEN** 活动节点不被标记为完成

#### Scenario: 快速工具结果返回循环
- **WHEN** Fast Runtime 的工具调用产生规范化结果
- **THEN** 结果返回独立 Fast Agent loop
- **THEN** 系统不运行节点完成评估或可信进度更新

### Requirement: 推理记录保持可审计且安全

系统 SHALL 持久化简洁、可供用户审计的摘要、结构化决策、状态差异以及证据引用，并且 **MUST NOT** 要求、暴露或依赖隐藏的思维链（chain-of-thought）。

#### Scenario: 用户查看某一轮执行记录

* **WHEN** 用户打开运行审计视图
* **THEN** 当前轮次说明所选择的操作、预期结果、实际结果以及状态变化
* **THEN** 不显示隐藏的思维链，也无需依赖隐藏思维链即可复现该状态转换

### Requirement: 运行时控制 Agent Loop 的节点顺序
系统 SHALL 仅通过 Trusted Agent Runtime 执行 TaskContract、DAG 调度、AgentState、节点评估、Reflection 和 CompletionGate。Fast Agent Runtime SHALL 使用独立的模型驱动动作循环，并 MUST NOT 调用或模拟可信节点生命周期；两者只共享工具与平台边界。

#### Scenario: 可信模型尝试跳过完成处理
- **WHEN** trusted 模型决策尝试从行动选择直接进入 completed
- **THEN** Trusted Runtime 拒绝该转换
- **THEN** 最终状态仍由节点状态和 CompletionGate 判定

#### Scenario: 快速行动轮次完成
- **WHEN** Fast Runtime 的已授权行动返回结果
- **THEN** 运行时将规范化观察交回 Fast Agent 模型
- **THEN** 系统不执行 DAG 节点评估、Reflection 或可信完成验证

### Requirement: 每个节点返回类型化转换结果

系统 SHALL 要求每个循环节点返回类型化 `NodeResult`，其中包含拟议状态补丁、产生的事件、下一节点和可选的分类错误。

#### Scenario: 节点结果不合法

* **WHEN** 节点缺少必要的转换信息，或状态补丁超出该节点权限
* **THEN** 运行时在不修改规范状态的情况下拒绝结果
* **THEN** 运行时进入该错误类别对应的有界处理路径

### Requirement: 行动轮次使用可恢复检查点

系统 SHALL 在启动外部行动前持久化已验证决策、策略结果、行动阶段和稳定幂等键，并 SHALL 在行动返回后持久化结果及规范状态更新。

#### Scenario: 行动执行前运行时停止

* **WHEN** 进程在准备轮次后、行动启动前停止
* **THEN** 恢复过程可使用相同幂等键继续已准备的行动

#### Scenario: 幂等行动返回后运行时停止

* **WHEN** 行动结果已经记录，但状态应用被中断
* **THEN** 恢复过程复用已记录结果并完成状态应用，不再次执行行动

#### Scenario: 非幂等行动结果未知

* **WHEN** 恢复过程无法判断某项非幂等行动是否已经生效
* **THEN** 运行时不自动重试该行动
* **THEN** 运行进入 `waiting_user` 或 `blocked`，并记录结果不确定的行动引用

### Requirement: 等待中的运行从持久化续点恢复

系统 SHALL 在进入 `waiting_user` 时持久化暂停节点、状态版本、计划版本、未决请求和 continuation token。

#### Scenario: 用户回答澄清问题

* **WHEN** 用户为等待中的运行提供所需信息
* **THEN** 用户响应被转换为类型化 Observation
* **THEN** 运行从持久化续点恢复，而不是重新开始整个任务

#### Scenario: 用户拒绝行动批准

* **WHEN** 用户拒绝待审批行动
* **THEN** 审批结果作为 Observation 被记录
* **THEN** 控制器选择允许的替代方案、重新规划或阻塞，且不执行被拒绝的行动

### Requirement: 节点错误使用分类且确定性的出口

系统 SHALL 对模型、策略、工具、状态、验证器、预算和运行时内部错误进行分类，并 SHALL 将恢复路径限制在该类别允许的出口集合内。

#### Scenario: 工具发生永久失败

* **WHEN** 工具报告不可重试的永久失败
* **THEN** 运行时不采用临时错误重试路径
* **THEN** 根据策略和剩余能力选择替代方案、重新规划或进入阻塞状态

#### Scenario: 运行时发生内部错误

* **WHEN** 未预期的内部错误使受控 checkpoint 无法完成
* **THEN** 运行以适合审计的错误类别进入 `failed`
* **THEN** Finalizer 不得将任务呈现为成功完成

### Requirement: Quick reasoning activates Skills inside the lightweight loop
The system SHALL expose bounded frozen Skill discovery metadata to the quick controller, SHALL accept structured Skill activation without creating trusted planning records, and SHALL continue the quick loop with the activated instructions and applicable resources.

#### Scenario: Quick controller automatically selects a Skill
- **WHEN** the quick controller determines that an eligible Skill description matches the request
- **THEN** it emits a structured activation for a frozen Skill identity
- **THEN** the next quick decision can use the validated Skill instructions without a TaskContract or DAG

### Requirement: Trusted reasoning resolves Skills before contract and planning
The system SHALL complete an explicit Skill-resolution phase for trusted Runs before generating the TaskContract and initial canonical Plan, and SHALL include each selected Skill identity and revision in trusted state.

#### Scenario: Trusted request matches a Skill
- **WHEN** Skill resolution selects one or more Skills for a trusted request
- **THEN** the TaskContract and complete initial DAG are generated with access to their frozen instructions
- **THEN** the Plan records applicable Skill identities for relevant nodes and success criteria

### Requirement: Trusted nodes receive attenuated Skill subsets
The system SHALL bind each trusted Plan node to the subset of active Skills required for that node and SHALL reconstruct only those Skill instruction and resource contexts in its NodeExecution.

#### Scenario: Parallel nodes use different Skills
- **WHEN** two ready nodes require different Skills
- **THEN** each NodeExecution receives only its declared Skill subset
- **THEN** neither node receives unrelated Skill resources solely because they are active elsewhere in the Run

### Requirement: Late trusted Skill activation requires a Plan revision
The system SHALL treat activation of a previously inactive frozen-Catalog Skill after trusted Plan persistence as a semantic Plan change and SHALL require a valid PlanPatch or replan before using it for executable nodes.

#### Scenario: Observation reveals a needed Skill
- **WHEN** a trusted observation shows that an inactive frozen-Catalog Skill is needed
- **THEN** the runtime activates it only together with a validated revision of the unfinished DAG
- **THEN** completed nodes and accepted evidence remain immutable

### Requirement: Trusted completion verifies Skill-derived criteria
The system SHALL map mandatory Skill workflow checks that are accepted into the TaskContract or Plan to stable success criteria and SHALL evaluate them through the trusted Completion Gate; Skill text alone MUST NOT mark a Run complete.

#### Scenario: Skill prescribes final validation
- **WHEN** trusted planning incorporates a mandatory validation step from an active Skill
- **THEN** the Plan links that step to a success criterion and evidence requirement
- **THEN** completion remains blocked until the normal trusted verification outcome satisfies it

### Requirement: Persisted reasoning structures use only current schemas
The system SHALL deserialize Agent state, plan graphs, decisions, and final results only from their current schemas and SHALL NOT synthesize current fields from obsolete persisted shapes.

#### Scenario: Load a legacy Agent state or plan graph
- **WHEN** a persisted reasoning structure uses an earlier schema version or removed field
- **THEN** validation fails explicitly and no compatibility transformation is applied

### Requirement: Trusted 根 Agent 使用 Swarm 内建能力委派
系统 SHALL 向 eligible trusted 根控制器提供 Astra `swarm` runtime built-in，其中包含有界 DelegationRequest 集合和一个 Join 规范；系统 MUST NOT 将该能力交给第三方插件或 sandbox 执行，也 MUST NOT 允许其绕过 SubagentSupervisor。

#### Scenario: 控制器识别独立并行工作
- **WHEN** trusted 根控制器识别出两个相互独立、预期收益为正且符合策略的子任务
- **THEN** 控制器可在一次 `swarm` 调用中提交两个完整 DelegationRequest 和 Join policy
- **THEN** 运行时在执行前验证目标、成功标准、范围、输入、输出 schema、能力、预算和去重信息

#### Scenario: Standard 控制器尝试调用 Swarm
- **WHEN** standard Run 的控制器构造或请求 `swarm` 调用
- **THEN** 运行时拒绝该决策且不创建 child

### Requirement: 根 Agent 仅消费验证后的合并观察
系统 SHALL 将已消费 Join 的合并结果作为类型化 parent Observation 提供给后续根决策，并 MUST NOT 注入 child 隐藏推理、完整对话或私有 scratchpad。

#### Scenario: 多个 child 返回相互冲突的结论
- **WHEN** Join Merger 检测到两个已验证 child 对同一事实或声明给出不同值
- **THEN** parent Observation 保留各来源、Evidence 和结构化 conflict
- **THEN** 根控制器处理冲突而不是任意覆盖一个结果

### Requirement: 快速控制器直接决定轻量委派
系统 SHALL 允许 eligible standard 根控制器依据任务独立性、预期并发收益、上下文压力、共享资源冲突、风险和剩余预算直接选择 `swarm`，并 SHALL 在 `subagent_mode = auto` 且收益不足时继续单 Agent 回答。

#### Scenario: 快速任务适合并发
- **WHEN**standard 请求包含多个独立、只读且可分别验证的子问题并且预算充足
- **THEN**根控制器可以在当前 AgentTurn 选择一个有界 `swarm` group
- **THEN**每个 child 收到结果导向的目标、输出合同和衰减后的能力范围

#### Scenario: 快速任务不适合并发
- **WHEN**standard 请求简单、强顺序、存在共享写热点或估计收益不足
- **THEN**根控制器继续当前快速循环而不创建 child
- **THEN**系统不为了展示 Subagent 而生成虚假 fan-out

### Requirement: Agent 只在委派产生可验证收益时创建子 Agent
系统 SHALL 要求父 Agent 将委派决策绑定到顶层成功准则，并说明独立工作范围、预期收益、成功标准和停止条件；Runtime SHALL 在创建前执行确定性适用性门控。

#### Scenario: 父 Agent 提出并行委派
- **WHEN** 多个子问题可以独立完成和验证且结果将在明确 fan-in 汇合
- **THEN** 父 Agent 可提出多个 DelegationContracts，并保持最终合成责任

#### Scenario: 委派不能帮助成功准则
- **WHEN** child 目标无法映射到任何未满足的顶层成功准则
- **THEN** Runtime 拒绝委派并要求父 Agent 继续现有计划或重规划

### Requirement: 父 Agent 验证并合并子 Agent 结果
系统 SHALL 将 SubagentResult 视为带 provenance 的观察而非可信最终事实，并 SHALL 在合并前验证 schema、完成决定、证据引用、冲突和 join 完整性。

#### Scenario: siblings 结果一致
- **WHEN** required children 返回 schema 有效、证据充分且互不冲突的结果
- **THEN** 父级可把验证后的结果提升为共享事实并用于顶层完成评估

#### Scenario: siblings 结果冲突
- **WHEN** children 对同一关键声明给出不兼容结果
- **THEN** 父级保留结构化 conflict set，并在预算内验证、改派或向最终结果披露不确定性

### Requirement: 子 Agent 的推理状态彼此隔离
系统 SHALL 阻止父子或 sibling 直接修改对方的 AgentState、plan revision、scratchpad 和局部事实，并 SHALL 仅通过版本化委派输入、问题回答和 SubagentResult 交换状态。

#### Scenario: child 发现新线索
- **WHEN** child 在执行中发现超出自身范围但可能有价值的信息
- **THEN** 它将线索作为 open issue 或 evidence ref 返回父级，而不是直接修改 sibling 计划

