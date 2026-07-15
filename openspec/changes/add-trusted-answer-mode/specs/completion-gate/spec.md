## MODIFIED Requirements

### Requirement: 完成状态由独立的完成门（Completion Gate）决定

系统 SHALL 让 trusted 模式在进入 `completed` 或 `completed_with_warnings` 前执行完整 CompletionGate 评估；standard 模式 SHALL 通过共享 finalization 执行基础保障和轻量完成处理，但 SHALL NOT 声明已经通过完整契约校验。

#### Scenario: 可信模式控制器提出结束运行

* **WHEN** trusted 模式控制器发出终止意图
* **THEN** 完成门评估当前任务契约、证据、验证结果、审批状态、失败情况以及预算
* **THEN** 最终响应根据完整完成门的评估结果生成

#### Scenario: 快速回答完成

* **WHEN** standard 模式生成最终回答且基础保障通过
* **THEN** 共享 finalization 可以结束运行并保存基础 assurance 报告
* **THEN** 结果不得标记为完整校验通过

#### Scenario: 可信模式循环预算耗尽

* **WHEN** trusted 模式在满足所有强制成功条件之前达到轮次、工具、反思或重规划预算上限
* **THEN** 运行不会仅因为执行停止而进入 `completed`
* **THEN** CompletionGate 根据已有部分结果和生效策略选择严格终态

## ADDED Requirements

### Requirement: 基础保障不受回答模式影响
系统 SHALL 在 standard 和 trusted 两种模式中执行权限与工具硬限制、模型输出 Schema 校验、运行错误处理、取消处理、Artifact 引用清洗和敏感信息边界，并 MUST NOT 允许运行 profile 移除这些保障。

#### Scenario: 快速回答产生无效 Artifact 引用
- **WHEN** standard 模式最终回答引用不存在或不属于该 Run 的 Artifact
- **THEN** 系统在交付前清洗该引用并记录基础 assurance warning

#### Scenario: 快速回答请求禁止工具
- **WHEN** standard 模式模型请求被策略禁止的工具或操作
- **THEN** 共享权限门拒绝执行
- **THEN** 快速模式不会降低工具或系统限制
