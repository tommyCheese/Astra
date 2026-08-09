## ADDED Requirements

### Requirement: 运行从创建开始产生实时过程事件
系统 SHALL 在可能发生明显等待的规划、行动选择、工具执行、反思、验证和回答组织阶段产生有序过程事件，并 SHALL 通过现有 Run SSE 流发送这些事件。

#### Scenario: 首轮模型决策尚未完成
- **WHEN** Run 已创建且 Agent 正在等待首轮模型决策
- **THEN** 客户端在决策完成前收到受控的阶段开始事件
- **THEN** 用户无需等待最终回答即可确认 Astra 当前所处阶段

#### Scenario: 工具与反思连续发生
- **WHEN** Agent 选择工具、获得结果并触发反思
- **THEN** 对应 ToolCall、观察和反思事件按照持久化执行顺序进入同一 SSE 流

### Requirement: 可审计推理摘要支持增量传输
系统 SHALL 允许模型显式生成的简洁 `reasoning_summary` 以合并后的增量事件传输，并 SHALL 在字段完成时发送包含稳定完整摘要的完成事件。

#### Scenario: 决策摘要逐步生成
- **WHEN** 模型流中 `reasoning_summary` 获得新的可安全解码文本
- **THEN** 后端在有界短时间窗内发出 `reasoning.summary.delta`
- **THEN** 字段完成后发出 `reasoning.summary.completed` 用于客户端校正

#### Scenario: 决策同时包含最终回答
- **WHEN** 同一个模型响应先生成 `reasoning_summary` 再生成 `final_answer.summary`
- **THEN** 推理摘要进入过程事件
- **THEN** 最终回答继续通过独立的 `answer.delta` 事件传输且两者不会混合

### Requirement: 实时过程保持可恢复和有界
系统 SHALL 持久化足以重放的过程事件，SHALL 使用稳定事件 ID 去重，并 MUST 对增量频率、单条摘要长度和事件 payload 进行限制。

#### Scenario: SSE 在过程生成期间重连
- **WHEN** 客户端携带最后事件位置重新连接或随后加载 RunView
- **THEN** 系统可恢复已完成过程条目且不会永久丢失终态摘要

#### Scenario: 模型产生大量细粒度 chunks
- **WHEN** 一个摘要在短时间内产生多个模型 chunks
- **THEN** 后端合并 chunks 后持久化，避免为每个供应商 token 单独提交事件

### Requirement: 过程协议不得暴露隐藏思维链和敏感信息
系统 MUST 只传输运行时阶段、简洁可审计摘要和已清洗的执行元数据，并 MUST NOT 请求、依赖或转发模型隐藏 Chain-of-Thought、供应商 reasoning 内容、凭据、内部路径或完整未清洗工具输入。

#### Scenario: 模型供应商提供隐藏推理字段
- **WHEN** 模型响应包含供应商专用 reasoning token 或隐藏推理内容
- **THEN** 该内容不进入 RunEvent、RunView 或聊天过程面板

#### Scenario: 过程事件包含工具活动
- **WHEN** 工具开始或完成
- **THEN** 过程事件只包含公开工具名、稳定标识和安全状态摘要
