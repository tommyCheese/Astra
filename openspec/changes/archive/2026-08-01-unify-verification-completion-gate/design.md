## Context

当前最终化路径先调用 `VerificationEngine.verify()` 生成报告，再调用 `TaskAdapter.validate()` 得到 `CompletionDecision`，最后把适配器状态压缩为 `validator_passed: bool` 交给 `CompletionGate`。这导致 VerificationReport 中的无效 Artifact 引用、未来安全验证结果或独立 validator 问题无法必然约束终态；同时 Web 证据规则分别存在于 VerificationEngine 和 WebTaskAdapter，容易发生漂移。最终代码还会用 Run 终态覆盖 `VerificationReport.status`，混淆“验证结果”和“任务终态”。

现有 `TaskContract` 已包含 `verification_requirements`，SuccessCriterion 也包含 `verification_method`，但完成门目前没有按 validator 标识核对这些声明。本次改动利用现有契约建立强连接，不引入数据库迁移或新的外部依赖。

## Goals / Non-Goals

**Goals:**

- 为所有 validator 提供统一、强类型、可持久化的输出合约。
- 让 VerificationEngine 聚合领域验证与系统验证，并生成唯一验证事实集合。
- 让 CompletionGate 核对所有 mandatory verification requirements、阻塞问题和成功准则。
- 让验证 warning 稳定传播为 `completed_with_warnings`，阻塞失败稳定传播为 `blocked`。
- 保持 VerificationReport 的验证状态独立于 Run 终态。
- 保持现有 RunResult、VerificationReport 统计字段和终态枚举兼容。

**Non-Goals:**

- 不在本次 change 中实现外部验证服务、人工审批或多模型交叉验证。
- 不改变单轮 ObservationEvaluator 的行为。
- 不把 `runtime.py` 状态机骨架切换为生产主执行路径。
- 不改变现有 Web、Chart 工具输入输出协议。

## Decisions

### 1. 使用 ValidationOutcome 作为唯一 validator 输出

新增：

- `ValidationIssue`：`code`、`message`、`severity`、`evidence_refs`、`details`；
- `ValidationOutcome`：`validator`、`passed`、`blocking`、`requirement_ids`、`issues`、`warnings`、`evidence_refs`。

`passed=false, blocking=true` 表示必须阻止成功；`passed=true` 仍可携带非阻塞 warnings。validator 名称与 `TaskContract.verification_requirements[].validator`、`SuccessCriterion.verification_method` 对齐。相比继续传递 `validator_passed`，强类型结果保留了失败原因、来源和责任 validator，完成门无需猜测布尔值来自何处。

### 2. TaskAdapter 不再返回 CompletionDecision

WebTaskAdapter 和 ChartTaskAdapter 只负责领域规则并返回 `ValidationOutcome(validator="task_adapter")`。它们可以判断 Web 来源是否足够、Chart Artifact 是否完整，但不得直接决定 Run 是 `completed` 还是 `blocked`。

这保持现有默认 `verification_method="task_adapter"` 的契约兼容，同时为未来使用 `web_evidence`、`artifact_security` 等更细粒度 validator 留出空间。

### 3. VerificationEngine 成为验证聚合器

VerificationEngine 接收 FinalAnswer、Evidence Pack、领域 outcomes 和 Artifact 引用清洗结果，负责：

- 生成独立的 Artifact 引用 outcome；
- 汇总所有 outcomes、warnings、issues 和证据统计；
- 根据验证事实计算 VerificationReport.status：`completed`、`completed_with_warnings` 或 `failed`；
- 将 outcomes 写入 VerificationReport。

VerificationEngine 不再复制 WebTaskAdapter 的终态规则。无效 Artifact 引用当前作为非阻塞 warning：引用已被清洗，不允许静默宣称完全无警告；如果未来任务契约要求 Artifact validator 为 mandatory，可将其配置为阻塞验证。

### 4. 完成门按契约匹配 validator

CompletionGate 输入改为 `validation_outcomes`，并依次判断：

1. runtime error；
2. 用户输入或歧义；
3. mandatory verification requirement 是否存在匹配 outcome 且通过；
4. 是否存在 blocking failed outcome；
5. mandatory success criteria 是否满足；
6. 汇总 warnings 后决定 `completed` 或 `completed_with_warnings`。

缺失 mandatory validator 本身就是阻塞错误，写入 `unmet_criteria`，格式为 `verification:<requirement-id>`。

### 5. ValidationOutcome 驱动 SuccessCriterion 状态

在进入 CompletionGate 前，系统按 `SuccessCriterion.verification_method` 查找匹配 outcome：通过则标记 `satisfied`，阻塞失败则标记 `failed`，没有匹配结果则保持 `pending`。更新后的 AgentState 必须先通过 state version 乐观锁持久化，再执行完成门。

这替代“TaskAdapter 通过后一次性把所有 mandatory criterion 标为 satisfied”的粗粒度逻辑。

### 6. Verification 状态与 Run 状态分离

删除 `report.status = final_status`。VerificationReport.status 只描述验证聚合结果，`CompletionDecision.state` 和顶层 Run.status 描述任务终态。最终 AgentTurn observation 使用 Run 终态，不再借用 report.status。

历史 VerificationReport 没有 outcomes 时仍可由 Pydantic 默认空列表读取；不需要数据库迁移。

## Risks / Trade-offs

- [现有测试和调用方依赖 TaskAdapter 返回 CompletionDecision] → 同步迁移所有内部调用和测试，不保留新的布尔旁路。
- [默认 `task_adapter` 粒度仍较粗] → 本次先建立统一通道；未来可以逐项细分 validator，而无需再次修改 CompletionGate。
- [无效 Artifact 引用究竟阻塞还是 warning] → 当前引用先被安全删除并作为 warning；任务契约可通过 mandatory Artifact validator 提升为阻塞。
- [VerificationReport.status 语义改变后前端误当 Run 状态] → 保留顶层 Run.status 作为唯一终态；增加回归测试确认两种状态可以不同。
- [缺失 validator 导致旧契约阻塞] → 默认契约和适配器都使用 `task_adapter`，并测试 direct/Web/Chart 路径均产生 outcome。

## Migration Plan

1. 新增 schema，并让 VerificationReport 兼容缺少 outcomes 的历史数据。
2. 迁移 TaskAdapter 返回值和单元测试。
3. 迁移 VerificationEngine 聚合逻辑。
4. 迁移 AgentLoop 状态更新与 CompletionGate 调用，删除 `validator_passed` 和 report status 覆盖。
5. 运行 reasoning、Agent Loop、RunResult、repository 和全后端回归测试。
6. 如需回滚，恢复旧调用链；无数据库结构需要回滚。

## Open Questions

- 后续是否把默认 `task_adapter` 拆分为 `web_evidence`、`chart_integrity`、`artifact_reference` 和 `artifact_security` 等独立 mandatory requirements。
- `VerificationReport.status` 未来是否改用更明确的 `passed`、`warned`、`failed` 枚举；本次为兼容保留现有字符串。
