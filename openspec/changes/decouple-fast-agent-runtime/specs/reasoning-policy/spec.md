## MODIFIED Requirements

### Requirement: 运行保存请求策略与生效推理策略
系统 SHALL 为 Trusted Run 接受并持久化可信计划执行选择、推理资源、反思、验证和审批策略。系统 SHALL 为 Fast Run 持久化独立且最小的 Fast Runtime Policy，并 MUST NOT 编译、复制或提交可信推理策略字段。

#### Scenario: 编译快速响应 Profile
- **WHEN** 用户以 `standard` 模式启动 Run
- **THEN** 系统记录 Fast Runtime 版本、模型配置、轻量恢复和部署保护参数
- **THEN** 系统不记录 TaskContract、DAG、Reflection、Verification 或 CompletionGate 策略

#### Scenario: 编译可信执行 Profile
- **WHEN** 用户以 `trusted` 模式启动 Run
- **THEN** 系统记录完整规划、规范 DAG 调度、有界反思与重规划以及完整验证策略
- **THEN** 系统记录计划生成后自动执行或等待版本绑定确认的选择

#### Scenario: 运行期间设置发生变化
- **WHEN** 用户在 Run 开始后修改任一模式设置
- **THEN** 当前 Run 继续使用其冻结 runtime 与对应策略
- **THEN** 后续新建 Run 使用更新后的模式设置

### Requirement: 推理强度控制有界思考资源
系统 SHALL 仅在 Trusted Runtime 中将快速、平衡和深度推理映射为模型思考、工具、反思、重规划和验证预算。Fast Runtime SHALL 直接使用所选模型能力和独立部署保护，不把可信推理强度映射为 Fast Agent 行为。

#### Scenario: 快速响应执行任务
- **WHEN** 一个任务采用 `standard` 模式
- **THEN** Fast Runtime 不读取可信推理强度、反思或验证预算
- **THEN** 平台权限与执行硬边界保持不变

#### Scenario: 可信深度推理执行复杂任务
- **WHEN** 一个 Trusted Run 采用深度推理
- **THEN** 生效 Profile 允许更高但仍受限的工具、反思和 DAG 修订预算
- **THEN** 完整初始 DAG 仍在首次外部行动前持久化
