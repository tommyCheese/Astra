# unified-validation-outcomes Specification

## Purpose
TBD - created by archiving change unify-verification-completion-gate. Update Purpose after archive.
## Requirements
### Requirement: Validator 返回统一结果
系统 SHALL 要求所有参与终态判断的领域与系统 validator 返回统一、强类型的 ValidationOutcome。

#### Scenario: Validator 成功
- **WHEN** validator 完成检查且没有阻塞问题
- **THEN** 输出包含 validator 标识、`passed=true`、blocking 标志、warnings、issues、requirement IDs 和 evidence refs

#### Scenario: Validator 阻塞失败
- **WHEN** validator 发现不允许成功交付的问题
- **THEN** 输出包含 `passed=false`、`blocking=true` 和至少一个可审计 issue

### Requirement: TaskAdapter 只提供领域验证结果
系统 SHALL 让 TaskAdapter 返回领域 ValidationOutcome，并且 TaskAdapter MUST NOT 直接决定整个 Run 的终态。

#### Scenario: Web 证据不足
- **WHEN** Web 任务已尝试外部检索但没有成功抓取来源或答案没有来源引用
- **THEN** WebTaskAdapter 返回阻塞失败 outcome，而不是返回 Run CompletionDecision

#### Scenario: Chart Artifact 完整
- **WHEN** Chart 任务产生通过完整性检查的 Artifact
- **THEN** ChartTaskAdapter 返回通过 outcome，并携带 Artifact 证据引用

### Requirement: VerificationEngine 聚合所有验证事实
系统 SHALL 由 VerificationEngine 聚合领域 outcomes、Artifact 引用校验和验证统计，并将完整 outcomes 持久化到 VerificationReport。

#### Scenario: 聚合 warning
- **WHEN** 任一 outcome 通过但包含非阻塞 warning
- **THEN** VerificationReport.status 为 `completed_with_warnings`，并保留 warning 来源 outcome

#### Scenario: 聚合阻塞失败
- **WHEN** 任一 outcome 为阻塞失败
- **THEN** VerificationReport.status 表示验证失败，并保留 issue、validator 和 evidence refs

#### Scenario: 历史报告兼容
- **WHEN** 系统读取不含 validation outcomes 的历史 VerificationReport
- **THEN** schema 使用空 outcome 列表完成兼容解析

### Requirement: Verification 状态独立于 Run 终态
系统 SHALL 分别持久化 VerificationReport.status 与 CompletionDecision.state，并且 MUST NOT 用 Run 终态覆盖验证状态。

#### Scenario: 验证带警告但任务被其他条件阻塞
- **WHEN** VerificationReport 为 `completed_with_warnings`，但 CompletionGate 因未满足成功准则返回 `blocked`
- **THEN** 最终结果同时保留 VerificationReport.status=`completed_with_warnings` 和 CompletionDecision.state=`blocked`

