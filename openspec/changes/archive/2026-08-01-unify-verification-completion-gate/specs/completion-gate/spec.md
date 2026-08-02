## MODIFIED Requirements

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
