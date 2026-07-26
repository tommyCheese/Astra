## MODIFIED Requirements

### Requirement: 完成状态由独立的完成门（Completion Gate）决定

系统 SHALL 在运行进入 `completed` 或 `completed_with_warnings` 之前执行 CompletionGate 评估，并且 Coordinator、NodeWorker 或终结器 MUST NOT 直接将一次运行标记为成功；并行 trusted Run 只有在全部活动 execution 和必要 fan-in 屏障达到可判定状态后才能进入评估。

#### Scenario: 控制器提出结束运行

* **WHEN** 控制器发出终止意图以结束运行
* **THEN** 完成门评估当前任务契约、全部必要 Plan 分支、活动 execution、证据、验证结果、审批状态、失败情况以及预算
* **THEN** 最终响应根据完成门的评估结果生成

#### Scenario: 仍有并行节点运行

* **WHEN** 一个分支已提出最终答案但其他必要 NodeExecution 仍为 active、waiting_approval 或 committing
* **THEN** 完成门拒绝进入成功终态
* **THEN** 该候选答案不会作为 Run 的最终答案流式输出

#### Scenario: 循环预算耗尽

* **WHEN** 在满足所有强制成功条件之前，运行达到轮次、工具、反思或重规划预算上限
* **THEN** 运行不会仅因为执行停止而进入 `completed`
* **THEN** 完成门根据已有的部分结果和生效策略，选择 `blocked`、`failed`、`waiting_user` 或 `completed_with_warnings`

### Requirement: 强制成功条件与验证共同决定成功

系统 SHALL 仅在所有强制成功准则均由已接受证据或成功通过的任务专属验证器满足、全部必要 fan-in 节点完成、所有必需审批完成且不存在结果未知的 execution 时允许进入 `completed`。

#### Scenario: 交付物存在且验证通过

* **WHEN** 所有强制交付物均已存在，其验证器全部通过，不存在任何未解决的关键失败或非终态必要 execution
* **THEN** 完成门可以返回 `completed`

#### Scenario: fan-in 分支尚未汇合

* **WHEN** 某个必要汇合节点仍在等待一个并行前置分支
* **THEN** 对应成功准则保持未满足
* **THEN** 完成门不得返回 `completed`

#### Scenario: 结果声明缺乏证据

* **WHEN** 某项最终声明未引用任务契约要求的任何已接受观察、工件、验证结果或具有来源信息的记忆
* **THEN** 相应的成功准则仍视为未满足
* **THEN** 完成门不得返回 `completed`
