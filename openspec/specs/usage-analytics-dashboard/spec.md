# usage-analytics-dashboard Specification

## Purpose
TBD - created by archiving change persisted-usage-analytics-dashboard. Update Purpose after archive.
## Requirements
### Requirement: 用量查询支持明确范围
系统 SHALL 提供后端用量查询，支持全部历史、起止时间、当前对话及单次 Run 范围，并以数据库为事实来源。

#### Scenario: 查询全部历史
- **WHEN** 客户端请求全部历史范围
- **THEN** 系统聚合数据库中符合条件的所有事实记录

#### Scenario: 查询当前对话
- **WHEN** 客户端使用 task id 查询当前对话
- **THEN** 系统聚合该 task 下全部 Runs 的数据

#### Scenario: 查询单次 Run
- **WHEN** 客户端使用 run id 查询
- **THEN** 系统仅返回该 Run 关联的数据

### Requirement: 查询响应提供可解释明细
系统 MUST 返回总览、Token 分类、按日趋势、模型明细、工具明细与 Token 报告覆盖率，并使用一致的统计口径。

#### Scenario: 部分调用缺少 Token
- **WHEN** 范围内只有部分模型调用报告 Token usage
- **THEN** 响应分别返回已知 Token 合计、已报告调用数和总调用数

#### Scenario: 无持久化价格
- **WHEN** 系统没有与调用时间匹配的模型价格快照
- **THEN** 响应不生成估算费用

### Requirement: 看板展示持久化用量
前端用量看板 SHALL 在打开及切换范围时调用用量 API，不得根据文本长度、Turn 或 ToolCall 推算 Token 或模型调用数。

#### Scenario: 打开看板
- **WHEN** 用户打开用量统计看板
- **THEN** 界面加载后端持久化数据并展示所选范围的指标

#### Scenario: 切换最近时间范围
- **WHEN** 用户选择最近 7 天或最近 30 天
- **THEN** 界面请求对应时间边界并更新总览、趋势与明细

### Requirement: 看板具有完整反馈状态
用量看板 MUST 区分加载、查询失败、空数据、完整覆盖和部分覆盖状态。

#### Scenario: 查询失败
- **WHEN** 用量 API 返回错误或网络请求失败
- **THEN** 界面展示可理解的错误信息和重试操作

#### Scenario: 范围内没有记录
- **WHEN** 查询成功但所选范围没有事实记录
- **THEN** 界面展示空状态而非伪造零用量活动

#### Scenario: Token 覆盖不完整
- **WHEN** 报告 usage 的模型调用少于调用总数
- **THEN** 界面明确展示覆盖比例和未知数据提示

