## MODIFIED Requirements

### Requirement: Web 搜索发现候选来源
系统 SHALL 提供一个 `web_search` 工具，接收搜索查询并返回包含 provider 执行信息的候选来源记录。

#### Scenario: 搜索返回候选项
- **WHEN** runner 使用有效查询调用 `web_search`
- **THEN** 工具返回候选来源，包含 URL、标题、摘要或描述、提供方元数据和检索时间戳

#### Scenario: 自动搜索返回审计元数据
- **WHEN** runner 在自动 provider 模式下调用 `web_search`
- **THEN** 工具输出包含实际 provider、provider 模式、按顺序排列的尝试记录、候选数量、degraded 状态和 warnings

#### Scenario: 搜索失败
- **WHEN** 搜索提供方返回错误或超时且没有可用回退结果
- **THEN** 系统记录 failed ToolCall，并允许 runner 根据 run 计划选择失败、重试或带警告完成
