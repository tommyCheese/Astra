## MODIFIED Requirements

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
