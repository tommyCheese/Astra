# completion-gate Specification

## Purpose
TBD - created by archiving change add-general-reasoning-reflection-core. Update Purpose after archive.
## Requirements
### Requirement: 完成状态由独立的完成门（Completion Gate）决定
系统 SHALL 只让 Trusted Agent Runtime 在进入 `completed` 或 `completed_with_warnings` 前执行完整 CompletionGate。Fast Agent Runtime MUST NOT 装配 CompletionGate 或 VerificationEngine，并 SHALL 在模型给出最终答案后直接持久化快速结果。

#### Scenario: 可信模式控制器提出结束运行
- **WHEN** Trusted Runtime 控制器发出终止意图
- **THEN** 完成门评估当前任务契约、证据、验证结果、审批状态、失败情况以及预算
- **THEN** 最终响应根据完整完成门的评估结果生成

#### Scenario: 快速回答完成
- **WHEN** Fast Runtime 模型生成最终回答
- **THEN** 系统直接清洗引用、持久化回答和终态
- **THEN** 系统不创建 VerificationReport、CompletionDecision 或可信完成事件

#### Scenario: 可信模式循环预算耗尽
- **WHEN** Trusted Runtime 在满足所有强制成功条件之前达到轮次、工具、反思或重规划预算上限
- **THEN** 运行不会仅因为执行停止而进入 `completed`
- **THEN** CompletionGate 根据已有部分结果和生效策略选择严格终态

### Requirement: 强制成功条件与验证共同决定成功
系统 SHALL 仅在所有强制成功准则均由匹配其 verification method 的统一 ValidationOutcome 满足、所有 mandatory verification requirements 均存在通过结果、且所有必需审批均已完成时，允许进入 `completed`。

#### Scenario: 交付物存在且验证通过
* **WHEN** 所有强制交付物均已存在，其 mandatory validator outcomes 全部存在且通过，并且不存在任何未解决的阻塞失败
* **THEN** 完成门可以返回 `completed`

#### Scenario: 结果声明缺乏证据
* **WHEN** 某项最终声明未引用任务契约要求的任何已接受观察、工件、验证结果或具有来源信息（provenance）的记忆
* **THEN** 相应的成功准则仍视为未满足
* **THEN** 完成门不得返回 `completed`

#### Scenario: 强制 validator 缺失
* **WHEN** TaskContract 声明了 mandatory verification requirement，但没有相同 validator 标识的 ValidationOutcome
* **THEN** 完成门返回 `blocked`，并在 unmet criteria 中标识缺失的 verification requirement

#### Scenario: 非阻塞验证警告
* **WHEN** 所有 mandatory validator 均通过，但至少一个 outcome 包含 warning
* **THEN** 完成门返回 `completed_with_warnings`，并汇总所有验证 warnings

### Requirement: 终止状态具有明确且互不混淆的语义

系统 SHALL 区分 `completed`、`completed_with_warnings`、`waiting_user`、`blocked`、`failed` 和 `cancelled`，并 SHALL 为所有非 `completed` 的终止结果持久化结构化的终止原因及未满足的成功准则。

#### Scenario: 需要用户输入

* **WHEN** 后续进展依赖用户作出关键选择或审批
* **THEN** 运行进入 `waiting_user`，并附带明确的问题或审批请求
* **THEN** 后续可继续恢复执行，而不会被表示为 `completed` 或 `blocked`

#### Scenario: 已无允许的策略

* **WHEN** 任务已被正确理解，但所有安全、允许且预算范围内的策略均已耗尽
* **THEN** 运行进入 `blocked`，并记录失败策略引用及未满足的成功准则

#### Scenario: 运行时发生不可恢复错误

* **WHEN** 不可恢复的内部错误或基础设施故障导致无法受控地继续执行
* **THEN** 运行进入 `failed`，并记录符合审计要求的错误类别及恢复信息

#### Scenario: 用户主动取消

* **WHEN** 用户在 Run 自然结束前请求终止当前执行
* **THEN** 运行进入 `cancelled` 并记录用户取消原因
* **THEN** 取消不得被表示为完成、阻塞或运行失败

#### Scenario: 部分结果可用但存在非关键缺口

* **WHEN** 强制部分结果策略允许交付，且仅剩明确标记为非关键的成功准则未满足
* **THEN** 运行可以进入 `completed_with_warnings`
* **THEN** 响应中列出所有警告及所有未满足的非关键成功准则

### Requirement: 终结器生成与状态相匹配的响应

系统 SHALL 基于已接受状态生成最终输出，并 SHALL 根据完成门返回的结果采用相应的响应格式。

#### Scenario: 阻塞状态完成终结

* **WHEN** 完成门返回 `blocked`
* **THEN** 终结器解释证据缺口或能力缺口，并说明可能的下一步操作
* **THEN** 不得将缺乏支持的任务结果表述为成功完成

#### Scenario: 等待状态请求澄清

* **WHEN** 完成门返回 `waiting_user`
* **THEN** 终结器输出聚焦的问题或审批请求，并保留可恢复执行的运行状态

### Requirement: 任务适配器提供领域特定验证
系统 SHALL 通过授权的 TaskAdapter 获取领域专属的观察归一化、默认成功准则和 ValidationOutcome，同时由统一 VerificationEngine 聚合结果并由 CompletionGate 决定终态。

#### Scenario: Web 任务使用 Web 适配器
* **WHEN** Web 检索任务进入验证阶段
* **THEN** Web 适配器评估来源可信性、证据覆盖率、证据冲突以及检索失败情况，并返回领域 ValidationOutcome
* **THEN** VerificationEngine 将该结果纳入 VerificationReport，通用完成门消费完整 outcomes，无需 Web 专属终态分支

#### Scenario: 适配器不可用
* **WHEN** 某次运行请求使用尚不存在授权 TaskAdapter 的任务能力
* **THEN** mandatory validator outcome 缺失，系统在完成门进入 `blocked`
* **THEN** 终止原因明确标识缺失的能力或验证要求

### Requirement: 活动计划状态参与完成判定

系统 SHALL 在允许 `completed` 或 `completed_with_warnings` 前检查活动计划的节点和依赖状态，并 MUST NOT 在必需节点仍为 pending、running、failed 或 blocked 时将运行表示为成功完成。

#### Scenario: 成功准则满足但计划仍在执行

- **WHEN** TaskContract 的强制准则已满足，但活动计划仍存在 running 或必需 pending 节点
- **THEN** CompletionGate 返回 continue 而不是 completed
- **THEN** 运行继续从 PlanScheduler 选择允许的节点

#### Scenario: 必需计划节点失败

- **WHEN** 一个必需节点进入 failed 或 blocked 且没有可用的合法替代计划
- **THEN** CompletionGate 返回 blocked 或 failed
- **THEN** 终止原因包含节点和未满足成功准则引用

#### Scenario: 计划、契约和验证全部完成

- **WHEN** 活动计划全部必需节点完成、强制成功准则满足、必需验证通过且不存在等待状态
- **THEN** CompletionGate 可以返回 completed 或符合策略的 completed_with_warnings

### Requirement: 基础保障不受回答模式影响
系统 SHALL 在 Fast 与 Trusted Agent Runtime 中通过共享基础设施执行权限与工具硬限制、工具输入 Schema、运行错误处理、取消处理、Artifact 引用清洗和敏感信息边界，并 MUST NOT 通过 runtime profile 关闭这些保障。

#### Scenario: 快速回答产生无效 Artifact 引用
- **WHEN** Fast Runtime 最终回答引用不存在或不属于该 Run 的 Artifact
- **THEN** 系统在持久化前静默清洗该引用
- **THEN** 系统不为此创建 VerificationReport 或启动完成门

#### Scenario: 快速回答请求禁止工具
- **WHEN** Fast Runtime 模型请求被平台策略禁止的工具或操作
- **THEN** 共享权限门拒绝执行并把拒绝返回模型
- **THEN** 快速运行时不会降低工具或系统限制

### Requirement: Grounding outcomes participate through verification requirements
The Completion Gate SHALL enforce mandatory grounding ValidationOutcome failures when selected by the TaskContract and MUST NOT globally require research-specific validators merely because the capability is installed.

#### Scenario: Ordinary non-Web trusted task completes
- **WHEN** a trusted task has no grounding requirement and uses no canonical Web evidence
- **THEN** the absence of Web sources does not block completion

#### Scenario: Required grounding validator fails
- **WHEN** the TaskContract requires a grounding validator and its blocking outcome fails
- **THEN** the Completion Gate reports the corresponding unmet verification requirement

### Requirement: 根完成门验证并发子 Agent 汇合已消费
系统 SHALL 在 trusted 根 Run 成功完成前确认所有强制 descendant 已进入允许终态、required 和 first-success Join 已成功消费、child 预算已结算、必要审批已解决且不存在未处理的阻塞合并冲突。

#### Scenario: Child 已完成但 Join 尚未消费
- **WHEN** 所有 required child 已完成但其 Join 仍处于 ready 或 merging
- **THEN** CompletionGate 返回 continue_run
- **THEN** 根 Agent 不得发布最终成功答案

#### Scenario: Optional child 失败
- **WHEN** optional child 失败且不影响任何强制成功准则或 required Join
- **THEN** CompletionGate 可在记录 warning 后继续评估成功

#### Scenario: 合并结果存在阻塞冲突
- **WHEN** required child 的已验证结果产生尚未处理且会影响强制成功准则的 conflict
- **THEN** CompletionGate 不返回 completed
- **THEN** 根 Agent 必须解决冲突、补充验证、重新委派或进入 blocked

### Requirement: Root Completion Gate 等待所有必需子 Agent
系统 SHALL 在 Run 进入成功终态前确认所有 required child joins 已达到允许终态，且不存在仍可能改变强制成功准则的活动 descendant。

#### Scenario: required child 仍在运行
- **WHEN** 父 Agent 提出终止意图但任一 required child 仍为 queued、running 或 waiting
- **THEN** Root Completion Gate 拒绝 completed 或 completed_with_warnings，并返回等待的 execution 引用

#### Scenario: 只有 optional child 未完成
- **WHEN** 所有强制准则和 required joins 已满足且仅有允许忽略的 optional child 未完成
- **THEN** Completion Gate 可按 policy 取消或分离该 child，并在结果中记录处理方式

### Requirement: Root Completion Gate 验证子结果谱系和汇合
系统 SHALL 验证被最终结果使用的 SubagentResult 已通过 child Completion Gate、output schema、Artifact/Evidence lineage、权限合规及父级冲突合并。

#### Scenario: 父级引用未验证 child 输出
- **WHEN** 顶层声明引用 failed、blocked 或 schema 无效的 child result
- **THEN** 该声明不满足对应成功准则，且 Root Completion Gate 不把自然语言摘要视为替代证据

#### Scenario: child 结果含未解决关键冲突
- **WHEN** 多个 child 对强制声明存在未解决冲突
- **THEN** Root Completion Gate 要求追加验证，或根据任务策略返回 blocked/completed_with_warnings 并明确披露冲突

