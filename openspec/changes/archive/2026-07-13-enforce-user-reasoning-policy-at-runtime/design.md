## Context

Run 创建接口已经把用户选择编译为 `ReasoningPolicySnapshot` 并持久化，但 Agent Loop 的轮次和工具上限仍读取全局 Settings；`ReflectionGate` 已存在却没有参与错误路径。规划策略中 `direct` 与 `adaptive` 也共享同一快速启动分支，实际差异不足。

## Goals / Non-Goals

**Goals:**

- 每个 Run 从自身持久化策略快照读取有效预算和反思规则。
- 用户选择优先；只有策略编译器声明的安全调整可以覆盖用户请求。
- 三种规划策略产生明确、可测试的路径差异。
- 所有自动反思由统一门控决定，并受最大反思次数约束。

**Non-Goals:**

- 不改变 Run 创建 API。
- 不实现新的风险分类模型或新工具。
- 不在本变更中实现模型 token 级“思考深度”参数映射。

## Decisions

1. **Agent Loop 在启动时加载 Run 的 effective policy。** 运行预算来自不可变快照，而不是重新根据前端字段推断，保证恢复运行与首次运行一致。
2. **预算采用用户值与系统硬上限的较小值。** 用户的 fast/balanced/deep 决定期望预算，服务端 Settings 继续作为部署级安全上限，防止单个 Run 超出系统容量。
3. **规划路径分为三类。** direct 使用本地单步计划；adaptive 使用轻量任务契约，并由 Agent 决策是否调用工具或重规划；plan_first 在进入循环前调用模型生成完整契约和计划。
4. **反思统一通过 ReflectionGate。** 模型输出失败、工具失败和每轮完成等信号先进入门控；关闭反思时不得调用 reflector；超过预算时不得继续反思。
5. **不执行反思时保留原始错误语义。** 工具失败仍会形成 observation 并由 Agent 下一轮决策或终态处理，而不是伪造反思记录。

## Risks / Trade-offs

- [深度模式预算大于部署上限] → 使用 `min(policy_budget, system_limit)` 保留部署保护。
- [每轮反思显著增加延迟和成本] → 严格使用 max_reflections，并在测试中验证上限。
- [自适应与直接模式仍可能对简单任务表现相近] → 保证自适应允许模型返回 replan/reflect，direct 将非工具、非终态的重规划意图收敛为继续决策，不进行前置完整规划。
- [恢复旧 Run 时策略字段不完整] → 使用 Pydantic 默认值兼容旧快照。

## Migration Plan

无需数据库迁移。部署后新旧 Run 都从已有 `reasoning_policy` JSON 加载；字段缺失时使用当前默认策略。回滚只需恢复 Agent Loop 和 RunEngine 的策略读取逻辑。

## Open Questions

- 后续是否把推理强度映射到供应商原生的 reasoning effort 参数，需要按不同模型能力单独设计。
