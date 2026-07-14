## 1. 验证合约

- [x] 1.1 新增 ValidationIssue、ValidationOutcome，并在 VerificationReport 中持久化 outcomes
- [x] 1.2 保持历史 VerificationReport 和 RunResult 对缺失 outcomes 的兼容解析

## 2. Validator 与聚合

- [x] 2.1 将 WebTaskAdapter 和 ChartTaskAdapter 的 validate 返回值迁移为 ValidationOutcome
- [x] 2.2 重构 VerificationEngine 聚合领域 outcomes、Artifact 引用问题和验证统计
- [x] 2.3 根据 outcomes 独立计算 VerificationReport.status，不再复用 Run 终态

## 3. 完成门与状态推进

- [x] 3.1 实现 ValidationOutcome 到 SuccessCriterion 状态的精确映射
- [x] 3.2 将 CompletionGate 改为核对 mandatory verification requirements、阻塞 outcomes 和汇总 warnings
- [x] 3.3 改造 AgentLoop 使用统一 outcomes，删除 validator_passed 布尔旁路和 report status 覆盖

## 4. 验证

- [x] 4.1 更新 TaskAdapter、CompletionGate 和 VerificationEngine 单元测试
- [x] 4.2 增加缺失强制 validator、Artifact warning、验证状态与 Run 终态分离的 AgentLoop 回归测试
- [x] 4.3 运行格式检查、相关测试、完整后端测试和 OpenSpec validate
