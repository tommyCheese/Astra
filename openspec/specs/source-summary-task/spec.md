# source-summary-task Specification

## Purpose
TBD - created by archiving change add-google-search-adaptive-crawler. Update Purpose after archive.
## Requirements
### Requirement: 用户可以运行真实 Web 总结任务
系统 SHALL 支持用户输入一个主题，并完成搜索、抓取、综合和验证的真实 Web 总结任务。

#### Scenario: 总结任务成功
- **WHEN** 用户提交一个需要 Web 信息的总结目标
- **THEN** 系统执行 Google 搜索、抓取多个来源、生成证据包，并返回有来源支撑的总结

#### Scenario: 总结任务证据不足
- **WHEN** 搜索或抓取无法获得足够高质量来源
- **THEN** 最终结果说明证据不足，并列出失败来源、低质量来源和限制

### Requirement: 来源筛选和去重
系统 SHALL 在抓取前对搜索候选来源进行筛选和去重。

#### Scenario: 去除重复来源
- **WHEN** 搜索结果包含重复 URL、相同 canonical URL 或明显相同页面
- **THEN** 系统只抓取去重后的来源，并在 evidence metadata 中记录去重数量

#### Scenario: 跳过不适合抓取的来源
- **WHEN** 搜索结果指向明显不可抓取或非正文型资源
- **THEN** 系统跳过该来源或降低优先级，并记录原因

### Requirement: 总结基于证据包
系统 SHALL 使用已记录的搜索和抓取结果构造 Evidence Pack，并基于该 Evidence Pack 生成总结。

#### Scenario: Evidence Pack 构造完成
- **WHEN** 搜索和抓取步骤完成
- **THEN** 系统构造包含查询、候选来源、已抓取来源、正文片段、失败原因和质量评分的 Evidence Pack

#### Scenario: Evidence Pack 来源于工具调用
- **WHEN** 系统构造 Evidence Pack
- **THEN** Evidence Pack 只使用已记录的 ToolCall、Artifact 和验证结果，不读取未审计的临时状态

#### Scenario: 总结引用来源
- **WHEN** 系统输出总结要点
- **THEN** 每个关键要点包含来源引用，或明确说明该要点无法可靠归因

### Requirement: 冲突和限制可见
系统 SHALL 在总结结果中报告来源冲突、抓取失败和证据限制。

#### Scenario: 来源存在冲突
- **WHEN** 不同来源对同一事实给出冲突信息
- **THEN** 最终结果列出冲突内容、相关来源和验证备注

#### Scenario: 部分来源抓取失败
- **WHEN** 部分候选来源抓取失败但剩余来源足以总结
- **THEN** 最终结果带警告完成，并展示失败来源和失败原因

### Requirement: 总结任务时间线完整
系统 SHALL 在 timeline 中展示搜索、筛选、抓取、证据包构造、总结和验证步骤。

#### Scenario: Timeline 展示总结流程
- **WHEN** 总结任务运行中或完成后被查看
- **THEN** UI 展示每个阶段的状态、工具调用、抓取质量和最终结果可用事件

