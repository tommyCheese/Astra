## ADDED Requirements

### Requirement: 模型调用逐次持久化
系统 SHALL 为每一次实际发送到模型供应商的请求尝试持久化独立调用记录，包括重试、终态、耗时、模型标识与所属 Run。

#### Scenario: 成功调用落库
- **WHEN** 模型请求成功返回
- **THEN** 系统持久化一条 succeeded 调用记录并关联对应 Run

#### Scenario: 重试逐次记录
- **WHEN** 同一逻辑操作因错误产生多次 HTTP 尝试
- **THEN** 系统按 attempt 分别保存每次尝试及其终态

#### Scenario: 调用失败
- **WHEN** 模型请求在获得有效响应前失败
- **THEN** 系统保存 failed 调用记录、耗时和可用的错误分类

### Requirement: 精确采集供应商 Token
系统 MUST 从流式或非流式供应商响应中采集其明确返回的 Token usage，并规范化 input、cached input、output、reasoning 与 total Token。

#### Scenario: 流式响应含 usage
- **WHEN** 流式响应的终止数据块包含 usage
- **THEN** 系统保存供应商返回的 Token 分类和原始 usage

#### Scenario: 非流式响应含 usage
- **WHEN** 非流式响应包含顶层 usage
- **THEN** 系统保存供应商返回的 Token 分类和原始 usage

#### Scenario: 供应商未返回字段
- **WHEN** 供应商未返回某个 Token 字段
- **THEN** 系统将该字段保留为未知而非估算为零

### Requirement: 计量数据可跨重启恢复
系统 SHALL 将已完成的调用事实提交到数据库，并在服务启动时识别遗留的运行中记录。

#### Scenario: 服务重启回显
- **WHEN** 服务在模型调用完成后重启
- **THEN** 重启后的查询返回与重启前一致的已持久化调用数据

#### Scenario: 启动时存在遗留调用
- **WHEN** 服务启动时发现超时仍为 running 的调用记录
- **THEN** 系统将其标记为 interrupted 且不计入成功或失败

### Requirement: 已有事实保持单一来源
系统 MUST 从 Run、AgentTurn、ToolCall、Memory、SandboxJob 与 Artifact 的现有持久化表读取对应指标，不得通过前端状态或重复事实表替代。

#### Scenario: 工具成功率计算
- **WHEN** 范围内同时存在 succeeded、failed 和 running 工具调用
- **THEN** 成功率仅以 succeeded 与 failed 作为分母
