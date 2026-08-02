# run-cancellation Specification

## Purpose
TBD - created by archiving change add-user-run-cancellation. Update Purpose after archive.
## Requirements
### Requirement: 用户可以幂等取消活动 Run
系统 SHALL 提供按 Run 标识取消当前执行的 API，并 SHALL 将重复取消收敛到同一个持久化结果。

#### Scenario: 用户取消正在执行的 Run
- **WHEN** 用户对 created、planning、executing、synthesizing 或 verifying 状态的 Run 发起取消
- **THEN** 系统停止对应执行任务并将 Run 标记为 `cancelled`
- **THEN** 系统持久化 `user_cancelled` 终止原因和 `run.cancelled` 事件

#### Scenario: 用户重复取消同一个 Run
- **WHEN** Run 已经由用户取消且客户端再次发送取消请求
- **THEN** 系统返回同一个 `cancelled` Run 状态且不重复产生副作用

#### Scenario: 取消与自然完成同时发生
- **WHEN** Run 在取消请求到达前已经进入自然终态
- **THEN** 系统保留原终态且不得将其覆盖为 `cancelled`

### Requirement: 取消传播到活动执行资源
系统 MUST 中断当前模型流和可取消工具，并 SHALL 将活动执行记录收敛为非运行状态。

#### Scenario: 模型输出期间取消
- **WHEN** 用户在模型流式请求尚未完成时取消 Run
- **THEN** 系统关闭本地模型流并将对应模型调用标记为 `interrupted`
- **THEN** 不再启动新的 Agent turn、工具或验证动作

#### Scenario: 工具执行期间取消
- **WHEN** 用户在 ToolCall 或 Sandbox Job 执行期间取消 Run
- **THEN** 系统尽力终止工具执行并清理其运行资源
- **THEN** 活动 Step、ToolCall、AgentTurn 和 Sandbox Job 不得永久停留在 running 状态

### Requirement: 取消保留可见部分结果与审计信息
系统 SHALL 保留取消前已经传输给用户的回答文本和已完成审计记录，但 MUST NOT 将取消表示为成功完成。

#### Scenario: 已产生部分回答时取消
- **WHEN** 回答流已经产生可见 answer delta 后用户取消
- **THEN** RunView 保留该部分回答并明确标记运行已取消
- **THEN** 系统不生成完成验证通过的结果

#### Scenario: 尚无回答时取消
- **WHEN** Run 在规划或工具阶段被取消且尚无可见回答
- **THEN** UI 显示简洁的“已终止本次运行”状态

### Requirement: 取消不关闭所属对话
系统 SHALL 仅终止当前 Run，并 SHALL 允许用户在原 Task 中继续提交新消息。

#### Scenario: 取消后继续追问
- **WHEN** 当前 Run 进入 `cancelled` 后用户再次发送消息
- **THEN** 系统在同一 Task 下创建新的 Run
- **THEN** 已取消 Run 的历史与审计信息保持可访问

