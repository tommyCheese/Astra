## Why

当前 `VerificationEngine` 生成的 `VerificationReport` 主要用于展示，而 `CompletionGate` 只消费 `TaskAdapter` 压缩后的通过布尔值与 warnings；两条重复校验路径可能漂移，使验证已经发现的问题无法约束终态。随着 Astra 增加 Artifact、安全、计算和高风险验证器，需要建立唯一、强类型、可审计的验证结果链路。

## What Changes

- 新增统一 `ValidationOutcome` / `ValidationIssue` 合约，表达 validator、是否通过、是否阻塞、关联验证要求、问题、warnings 和证据引用。
- TaskAdapter 从返回终态 `CompletionDecision` 改为返回领域 `ValidationOutcome`，不再越权决定整个 Run 的状态。
- `VerificationEngine` 聚合领域验证、Artifact 引用验证和证据统计，生成包含完整 outcomes 的 `VerificationReport`。
- `CompletionGate` 直接消费全部 `ValidationOutcome`，核对 `TaskContract.verification_requirements` 和成功准则，只由它决定 Run 终态。
- Verification 状态与 Run 终态分离；系统不得再用 `CompletionDecision.state` 覆盖 `VerificationReport.status`。
- 为缺失强制 validator、阻塞问题、非阻塞 warning、无效 Artifact 引用和 Web/Chart 回归路径补充测试。

## Capabilities

### New Capabilities

- `unified-validation-outcomes`：定义统一验证输出、验证聚合、验证要求匹配与独立 VerificationReport 状态。

### Modified Capabilities

- `completion-gate`：完成门必须消费强类型验证结果并核对所有强制验证要求，而不是接收单一 `validator_passed` 布尔值。

## Impact

- `backend/app/schemas/agent.py` 的验证与结果合约。
- `backend/app/runner/adapters.py` 的 TaskAdapter 返回类型和领域校验职责。
- `backend/app/runner/agent_loop.py` 的验证聚合、状态更新与终态生成顺序。
- `backend/app/runner/reasoning.py` 的成功准则更新和 CompletionGate 输入。
- 相关 reasoning、Agent Loop、RunResult 与 repository 回归测试。
- 不改变对外 Run 终态枚举；`verification_report` 增加结构化 outcomes，并保持旧字段可读。
