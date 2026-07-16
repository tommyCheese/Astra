## MODIFIED Requirements

### Requirement: Policy-driven tool resolution
Tool Router SHALL 根据 manifest capability、权限、风险、execution backend、当前 Run policy、执行模式、已有批准授权和预算解析工具。`auto_approval` 仅可跳过交互批准，不得绕过注册、权限、风险、backend 或 Sandbox 安全检查。

#### Scenario: Resolve an allowed sandboxed chart tool
- **WHEN** `chart.render` 已注册、Run policy 允许 `sandboxed_compute` 与 `artifact_write`，且当前行动已经获得执行模式要求的批准
- **THEN** Router 校验输入和预算后返回该工具并允许进入执行

#### Scenario: Reject a disallowed capability
- **WHEN** 工具已注册但其 capability 未被当前 Run policy 允许
- **THEN** Router 返回可审计的 `tool_not_allowed` 或 `permission_denied`，且即使处于 `auto_approval` 模式也不执行工具

#### Scenario: Pause an unapproved eligible tool
- **WHEN** 工具通过平台策略解析但 `request_approval` 模式下没有匹配的批准授权
- **THEN** Runtime 在调用工具前创建批准请求并暂停 Run

