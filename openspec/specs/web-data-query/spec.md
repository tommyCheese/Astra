# web-data-query Specification

## Purpose
TBD - created by archiving change implement-web-data-query-task-runner. Update Purpose after archive.
## Requirements
### Requirement: 用户可以运行 Web 数据查询
系统 SHALL 支持一种通用数据查询任务：收集 Web 来源材料，综合答案，并报告证据。

#### Scenario: 成功的数据查询
- **WHEN** 用户提交一个需要当前或外部 Web 信息的数据查询目标
- **THEN** 系统规划查询，通过 Web 工具收集来源材料，综合答案，并返回有来源支撑的发现

#### Scenario: 查询无法回答
- **WHEN** 系统无法收集足够相关的来源材料来回答查询
- **THEN** 最终结果解释该查询无法回答，并包含失败或不足的证据尝试

### Requirement: Web 搜索发现候选来源
系统 SHALL 提供一个 `web_search` 工具，接收搜索查询并返回候选来源记录。

#### Scenario: 搜索返回候选项
- **WHEN** runner 使用有效查询调用 `web_search`
- **THEN** 工具返回候选来源，包含 URL、标题、摘要或描述、提供方元数据和检索时间戳

#### Scenario: 搜索失败
- **WHEN** 搜索提供方返回错误或超时
- **THEN** 系统记录 failed ToolCall，并允许 runner 根据 run 计划选择失败、重试或带警告完成

### Requirement: Web 抓取获取来源内容
系统 SHALL 提供一个 `web_fetch` 工具，抓取指定 URL，并返回规范化来源内容和元数据。

#### Scenario: 抓取返回内容
- **WHEN** runner 使用有效 URL 调用 `web_fetch`
- **THEN** 工具返回状态信息、规范化文本内容或提取数据、来源元数据和检索时间戳

#### Scenario: 抓取失败
- **WHEN** URL 无法获取、被阻止或超时
- **THEN** 系统记录 failed ToolCall，包含 URL、错误类别和错误详情

### Requirement: Web 工具调用被作为网络读取审计
系统 SHALL 在工具审计记录中将 Web 搜索和 Web 抓取调用分类为网络读取操作。

#### Scenario: 搜索审计元数据
- **WHEN** `web_search` 被执行
- **THEN** ToolCall 记录包含网络访问权限分类和只读副作用等级

#### Scenario: 抓取审计元数据
- **WHEN** `web_fetch` 被执行
- **THEN** ToolCall 记录包含网络访问权限分类和只读副作用等级

### Requirement: 答案综合使用抓取证据
系统 SHALL 基于已记录的工具输出综合最终答案，而不是依赖没有支撑的模型断言。

#### Scenario: 发现引用来源
- **WHEN** 系统从抓取到的来源材料中生成发现
- **THEN** 每条发现至少包含一个来源引用，或解释为什么无法提供来源归因

#### Scenario: 来源材料存在冲突
- **WHEN** 抓取到的来源包含互相冲突的信息
- **THEN** 最终结果标识该冲突，并包含限制说明或验证备注

### Requirement: Web 查询验证报告限制
系统 SHALL 验证收集到的证据是否足以回答请求的查询，并报告限制。

#### Scenario: 证据足够
- **WHEN** 抓取到的来源与查询相关且足够
- **THEN** 最终结果将验证标记为通过，并包含证据摘要

#### Scenario: 证据不完整
- **WHEN** 来源缺失、过时、不可访问，或不足以回答部分查询
- **THEN** 最终结果包含描述限制的验证警告

