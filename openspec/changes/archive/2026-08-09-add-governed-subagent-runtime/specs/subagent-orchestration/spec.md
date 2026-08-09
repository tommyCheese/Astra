## ADDED Requirements

### Requirement: 父 Agent 保持用户会话和最终结果所有权
系统 SHALL 在 supervisor/worker 模式中让 root Agent 负责用户交互、顶层任务契约、最终合成和 Run 终态，子 Agent SHALL 只处理委派工作并返回父级。

#### Scenario: 子 Agent 完成工作
- **WHEN** child 返回 completed SubagentResult
- **THEN** 结果发送给父级合并器而不是直接作为用户最终答案发布

#### Scenario: 子 Agent 需要用户澄清
- **WHEN** child 返回 waiting_parent 问题且父级上下文无法回答
- **THEN** 父 Agent 决定是否把 Run 转为 waiting_user，并由父级向用户提出问题

### Requirement: Runtime 根据适用性门控委派
系统 SHALL 在创建 child 前评估任务复杂度、独立性、上下文压力、资源冲突、估计收益、预算和风险，并 SHALL 对简单、强顺序、共享写热点或收益不足的任务保持单 Agent/DAG 执行。

#### Scenario: 宽度优先任务允许委派
- **WHEN** 一个 trusted 任务包含多个可独立验证的研究或分析方向且预算充足
- **THEN** Runtime 可允许父 Agent 创建有界并行 children

#### Scenario: 简单任务拒绝委派
- **WHEN** 单次模型或原子工具调用足以完成目标
- **THEN** Runtime 拒绝 child 创建并记录 `delegation_not_beneficial` 原因

#### Scenario: 高风险委派未启用
- **WHEN** 委派要求高风险外部写而当前 profile 或 feature policy 只允许只读子 Agent
- **THEN** Runtime 拒绝该委派但可让父级在现有受控路径继续处理

### Requirement: 并行 fan-out 具有硬上限和背压
系统 SHALL 同时限制每 Run children 总数、每 parent children 数、活动 children 数、深度以及 Run/部署级模型和工具并发，并 MUST NOT 将 child 数和内部 node 数无界相乘。

#### Scenario: 达到并行 child 上限
- **WHEN** 新 child 已经通过契约校验但没有可用 Agent execution slot
- **THEN** 系统将其保持 queued 或返回背压原因，且不突破 provider 或 Run 并发上限

#### Scenario: child 内部并行节点
- **WHEN** 多个 children 同时具有 ready Plan nodes
- **THEN** AgentCoordinator 为它们分配动态 node allowance，使总模型/工具活动数仍在全局配额内

### Requirement: 委派范围在 sibling 之间去重
系统 SHALL 使用父 execution、稳定请求标识、scope、inputs 和 dedupe key 检测重复 child，并 SHALL 在高度重叠且无明确独立成功准则时拒绝或合并委派。

#### Scenario: 重复研究方向
- **WHEN** 父 Agent 创建一个与运行中 sibling 目标、范围和输入实质相同的 child
- **THEN** 系统返回现有 handle 或拒绝新委派，并记录 overlap 诊断

#### Scenario: 有意独立复核
- **WHEN** 两个 children 的目标相似但契约明确声明独立验证或对抗性复核
- **THEN** 系统可允许两者存在，并在 lineage 中记录其复核关系

### Requirement: fan-in 只等待声明的 join set
系统 SHALL 让父计划节点显式声明 required、optional 或 first_success join policy，并 SHALL 只阻塞消费对应 child 结果的节点，而不阻塞无依赖的父级或 sibling 工作。

#### Scenario: required children 尚未完成
- **WHEN** fan-in 节点依赖的任一 required child 仍为非终态
- **THEN** 该节点保持 waiting_child，且父 Run 不得越过该汇合点完成

#### Scenario: optional child 失败
- **WHEN** optional child 失败但所有 required children 和顶层强制准则均满足
- **THEN** 父级可继续，并把失败作为 warning 和限制纳入结果

#### Scenario: first-success 完成
- **WHEN** first_success join set 中一个 child 返回已验证 completed 结果
- **THEN** 系统可解除 fan-in，并仅在其他 children 无不可逆进行中效果时按策略取消 losers

### Requirement: 子 Agent 失败只传播到依赖分支
系统 SHALL 根据 join policy、依赖关系、可重试性和剩余预算决定 child 失败的影响，并 SHALL 允许无依赖 sibling 安全继续。

#### Scenario: required child 可重试失败
- **WHEN** required child 以可重试错误失败且父级仍有预算
- **THEN** 父级可以相同幂等契约重试或以新契约改派，并保留所有 attempts

#### Scenario: required child 不可恢复失败
- **WHEN** required child 失败且没有安全可行的替代策略
- **THEN** 依赖 fan-in 被阻塞，父级 Completion Gate 根据顶层准则返回 blocked、failed 或 completed_with_warnings

### Requirement: 父子往返是有界的结构化协议
系统 SHALL 限制每个 child 的 waiting_parent 往返次数，并要求问题、所需字段和 continuation token 结构化；系统 MUST NOT 通过无限父子消息循环形成隐式群聊。

#### Scenario: 父级回答 child 问题
- **WHEN** 父级在允许往返次数内提供匹配 continuation 的结构化答案
- **THEN** child 从 checkpoint 恢复且不会重新执行已提交步骤

#### Scenario: 超过往返上限
- **WHEN** child 再次请求父级输入且已达到 policy 上限
- **THEN** 系统要求父级改派、缩小目标或将 child 终结为 blocked

### Requirement: 递归委派默认关闭并显式受控
系统 SHALL 默认将 `max_depth` 设为 1，且只有 Run policy、父级 delegated scope 和服务端上限同时允许时，child 才能创建后代。

#### Scenario: 默认 child 尝试递归
- **WHEN** depth=1 的 child 在默认 policy 下请求创建后代
- **THEN** 系统拒绝请求且 child 可继续当前委派

#### Scenario: 实验性 depth=2
- **WHEN** 管理策略明确允许 depth=2 且所有权限和预算衰减校验通过
- **THEN** 系统创建 grandchild，并在完整 lineage、预算树和 UI 中展示

