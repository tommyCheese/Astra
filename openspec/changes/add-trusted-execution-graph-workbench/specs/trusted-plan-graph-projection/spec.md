## ADDED Requirements

### Requirement: 服务端提供规范且完整的计划图谱快照
系统 SHALL 为 trusted Run 提供带 Schema 版本的 Plan 图谱快照，其中包含 Plan 标识、Run 标识、版本、状态、前一版本、节点、依赖边和节点执行元数据。

#### Scenario: 客户端加载当前计划
- **WHEN** 客户端请求一个存在当前 Plan 的 trusted Run
- **THEN** 响应包含稳定节点标识、node key、index、标题、意图、状态、依赖、预期结果、成功准则、能力、风险、可选性、证据、失败和时间信息
- **THEN** 依赖边引用同一快照内的稳定节点标识

#### Scenario: 快速响应加载 Run
- **WHEN** 客户端请求 standard Run
- **THEN** 响应不包含合成 Plan 节点、边或版本

### Requirement: 计划历史按需提供且保持版本不可变
系统 SHALL 提供当前 Run 的 Plan 版本摘要，并 SHALL 允许客户端按需读取任一持久化版本；已创建的版本内容 MUST NOT 被原地改写。

#### Scenario: Run 经历重规划
- **WHEN** Run 已创建 v1、v2 和 v3
- **THEN** 版本摘要按稳定顺序标识 active、superseded、planned 或 completed 状态
- **THEN** 客户端可以读取每个版本的规范节点和依赖

#### Scenario: 客户端比较版本
- **WHEN** 客户端请求相邻 Plan 版本
- **THEN** 投影提供 supersedes 和节点 lineage 信息
- **THEN** 客户端无需根据标题猜测节点是否沿袭

### Requirement: 图谱变化通过有序增量事件传输
系统 SHALL 通过现有 Run SSE 流发送计划创建、激活、节点状态、版本替换和修订结果事件，并 SHALL 使用稳定事件 ID、Plan ID 和 Plan 版本约束事件适用范围。

#### Scenario: 节点状态发生变化
- **WHEN** PlanNode 从 pending 转为 running 或从 running 转为终态
- **THEN** 客户端收到包含 Plan 版本、节点 ID、旧状态、新状态和安全执行引用的事件
- **THEN** 客户端无需等待下一个轮询周期即可更新图谱

#### Scenario: 新版本替代当前版本
- **WHEN** 重规划激活新的 Plan 版本
- **THEN** 事件明确标识旧 Plan、新 Plan、版本和沿袭摘要
- **THEN** 旧版本的迟到节点事件不能覆盖当前版本

### Requirement: 快照和增量可以确定性恢复
系统 SHALL 使客户端能够从图谱快照加有序增量恢复当前状态，并 SHALL 在事件缺口、未知版本或 reducer 冲突时重新获取权威快照。

#### Scenario: SSE 连接中断后恢复
- **WHEN** 客户端带最后事件 ID 重连
- **THEN** 服务端重放缺失的持久化图谱事件
- **THEN** 客户端恢复后的节点和版本状态与当前 RunView 一致

#### Scenario: 客户端检测到版本缺口
- **WHEN** 客户端收到无法应用于当前 Plan 版本的增量
- **THEN** 客户端停止猜测合并并请求新的完整快照
- **THEN** 图谱不会显示由不同版本节点拼接出的状态

### Requirement: 计划节点关联实际执行和证据
系统 SHALL 通过稳定 PlanNode ID 将 AgentTurn、ToolCall、Artifact、Evaluation、Verification 和审批记录关联到规范节点。

#### Scenario: 节点产生工具调用和产物
- **WHEN** 某节点执行工具并生成 Artifact
- **THEN** 图谱投影允许客户端从节点解析到对应 ToolCall 和 Artifact
- **THEN** 关联不依赖标题、数组位置或显示文案匹配

#### Scenario: 运行级事件没有节点归属
- **WHEN** 某个验证或终态事件属于整个 Run 而非单一节点
- **THEN** 事件保持 Run 级关联
- **THEN** 系统不把它任意附着到最后一个节点

### Requirement: 图谱投影保持公开摘要与安全边界
系统 MUST 只向图谱投影公开计划字段、清洗后的运行摘要和授权可见的证据引用，并 MUST NOT 包含隐藏思维链、凭据、未经清洗的工具输入或宿主内部路径。

#### Scenario: 节点详情包含模型推理
- **WHEN** 客户端打开节点运行 Trace
- **THEN** 响应只包含简洁可审计 reasoning summary 和结构化决策
- **THEN** 供应商隐藏 reasoning 内容不会进入图谱事件或快照
