# completion-gate Specification

## Purpose
TBD - created by archiving change add-general-reasoning-reflection-core. Update Purpose after archive.
## Requirements
### Requirement: 完成状态由独立的完成门（Completion Gate）决定

系统 SHALL 在运行进入 `completed` 或 `completed_with_warnings` 之前执行 CompletionGate 评估，并且控制器（controller）或终结器（finalizer）**MUST NOT** 直接将一次运行标记为成功。

#### Scenario: 控制器提出结束运行

* **WHEN** 控制器发出终止意图（terminal intent）以结束运行
* **THEN** 完成门评估当前任务契约、证据、验证结果、审批状态、失败情况以及预算
* **THEN** 最终响应根据完成门的评估结果生成

#### Scenario: 循环预算耗尽

* **WHEN** 在满足所有强制成功条件之前，运行达到轮次、工具、反思或重规划（replan）的预算上限
* **THEN** 运行不会仅因为执行停止而进入 `completed`
* **THEN** 完成门根据已有的部分结果和生效策略，选择 `blocked`、`failed`、`waiting_user` 或 `completed_with_warnings`

### Requirement: 强制成功条件与验证共同决定成功

系统 SHALL 仅在所有强制成功准则均由已接受证据或成功通过的任务专属验证器满足，且所有必需审批均已完成时，允许进入 `completed`。

#### Scenario: 交付物存在且验证通过

* **WHEN** 所有强制交付物均已存在，其验证器全部通过，且不存在任何未解决的关键失败
* **THEN** 完成门可以返回 `completed`

#### Scenario: 结果声明缺乏证据

* **WHEN** 某项最终声明未引用任务契约要求的任何已接受观察、工件、验证结果或具有来源信息（provenance）的记忆
* **THEN** 相应的成功准则仍视为未满足
* **THEN** 完成门不得返回 `completed`

### Requirement: 终止状态具有明确且互不混淆的语义

系统 SHALL 区分 `completed`、`completed_with_warnings`、`waiting_user`、`blocked` 和 `failed`，并 SHALL 为所有非 `completed` 的终止结果持久化结构化的终止原因及未满足的成功准则。

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

系统 SHALL 通过授权的 TaskAdapter 获取领域专属的观察归一化、默认成功准则、验证器以及最终响应 Schema，同时保持统一的完成语义。

#### Scenario: Web 任务使用 Web 适配器

* **WHEN** Web 检索任务进入验证阶段
* **THEN** Web 适配器评估来源可信性（source provenance）、证据覆盖率、证据冲突以及检索失败情况
* **THEN** 通用完成门消费适配器报告，而无需在核心运行时中加入任何 Web 专属分支逻辑

#### Scenario: 适配器不可用

* **WHEN** 某次运行请求使用尚不存在授权 TaskAdapter 的任务能力
* **THEN** 系统在执行任何未注册工具之前进入 `blocked`
* **THEN** 终止原因明确标识缺失的能力

