## ADDED Requirements

### Requirement: 最终答案可以引用当前 Run 的工具输出
系统 SHALL 允许最终答案中的每个 finding 通过稳定 Artifact ID 引用零个、一个或多个工具输出，并 SHALL 保持未引用 Artifact 的结果兼容性。

#### Scenario: Finding 引用一个图表
- **WHEN** 最终答案的 finding 包含当前 Run 中一个已验证图表 Artifact 的 ID
- **THEN** RunView 保留该 Artifact 引用，前端可将图表与该 finding 关联展示

#### Scenario: Finding 引用多个不同类型输出
- **WHEN** 一个 finding 引用当前 Run 中的图片、HTML 或文件 Artifact
- **THEN** 系统按引用顺序保留所有有效 Artifact ID，并允许前端使用对应类型的安全渲染器

#### Scenario: 纯文本结果没有工具输出
- **WHEN** Run 没有生成 Artifact 或 findings 没有 Artifact 引用
- **THEN** 最终答案仍按现有 summary、findings、sources 和 caveats 协议正常返回

### Requirement: Artifact 引用必须经过后端完整性验证
系统 MUST 在最终结果持久化前验证 finding 的 Artifact 引用，并 MUST 只接受属于当前 Run、状态为 verified 且具有可访问内容的 Artifact。

#### Scenario: 引用当前 Run 的有效 Artifact
- **WHEN** finding 引用的 Artifact 属于当前 Run、已验证且具有 storage key
- **THEN** 系统保留引用并在审计结果中记录引用有效

#### Scenario: 引用其他 Run 或不存在的 Artifact
- **WHEN** finding 引用不存在或属于其他 Run 的 Artifact ID
- **THEN** 系统从结果中移除该引用，且不泄露目标 Artifact 的元数据或存在性

#### Scenario: 引用未验证或不可访问的 Artifact
- **WHEN** finding 引用 security status 不是 verified 或没有可访问内容的 Artifact
- **THEN** 系统从结果中移除该引用，并向 verification notes 添加不包含敏感路径的 warning

#### Scenario: Finding 内重复引用
- **WHEN** 同一个 finding 多次引用同一个 Artifact ID
- **THEN** 系统按首次出现位置去重引用

### Requirement: 工具输出按答案上下文就近展示
前端 SHALL 按最终答案的 finding 顺序，将首次被有效引用的 Artifact 紧邻对应 finding 展示，而不是将所有输出统一集中在答案底部。

#### Scenario: 多个 Finding 分别引用不同图表
- **WHEN** 第一个 finding 引用图表 A，第二个 finding 引用图表 B
- **THEN** 前端在第一个 finding 后展示图表 A，并在第二个 finding 后展示图表 B

#### Scenario: 一个 Finding 引用多个 Artifact
- **WHEN** 一个 finding 引用多个有效 Artifact
- **THEN** 前端在该 finding 后按引用顺序展示一个局部 Artifact Gallery

#### Scenario: 多个 Finding 引用同一个 Artifact
- **WHEN** 多个 finding 引用同一个 Artifact
- **THEN** 前端只在首次引用处渲染 Artifact，并在后续引用处提供不重复内容的关联提示或定位能力

### Requirement: 未关联输出必须有确定性降级展示
前端 SHALL 展示当前 Run 中所有尚未在 finding 位置渲染的已验证 Artifact，并 SHALL 将它们放入明确标注的“其他输出”区域。

#### Scenario: 模型没有返回 Artifact 引用
- **WHEN** Run 生成了已验证 Artifact，但所有 findings 的 artifact_ids 均为空
- **THEN** 前端在答案正文之后的“其他输出”区域展示这些 Artifact

#### Scenario: 旧 Run 不包含新字段
- **WHEN** 前端加载没有 artifact_ids 字段的历史 Run
- **THEN** 前端将该字段视为空列表并继续展示所有可访问 Artifact

#### Scenario: 部分 Artifact 已经就近展示
- **WHEN** 一部分 Artifact 被 findings 引用，另一部分没有被引用
- **THEN** “其他输出”只展示尚未渲染的 Artifact，不重复已就近展示的内容

### Requirement: 思考过程可以定位工具输出
前端 SHALL 使用 Artifact 的 tool_call_id 将工具调用与输出关联，并 SHALL 在不暴露内部存储路径的前提下提供查看或定位能力。

#### Scenario: Chart ToolCall 产生多个 Artifact
- **WHEN** 一个 chart.render ToolCall 生成多个已验证 Artifact
- **THEN** 对应 ProcessPanel 步骤显示输出数量，并允许用户定位到这些 Artifact 的答案展示位置或其他输出区域

#### Scenario: ToolCall 没有产生 Artifact
- **WHEN** 工具调用成功但没有 Artifact，或工具调用失败
- **THEN** ProcessPanel 仍显示原有调用状态且不展示空输出入口

### Requirement: 流式答案与结构化展示平稳切换
前端 SHALL 在 answer.delta 阶段保持连续 summary 流式展示，并 SHALL 在完整 RunView 到达后一次性切换为基于 Artifact 引用的结构化答案。

#### Scenario: 流式生成期间 Artifact 关系未完成
- **WHEN** 前端仍在接收 answer.delta 且尚未取得终态 RunView
- **THEN** 前端只渲染流式 summary，不提前移动或重复渲染 Artifact

#### Scenario: 最终 RunView 到达
- **WHEN** answer.completed 后前端加载到包含有效 Artifact 引用的最终 RunView
- **THEN** 前端移除临时流式气泡并渲染稳定的 finding 与 Artifact 布局

### Requirement: 上下文展示必须保持可访问与响应式
前端 MUST 为关联 Artifact 提供与现有 ArtifactGallery 等价的安全、可访问和响应式行为。

#### Scenario: 图片、HTML 和文件输出
- **WHEN** 关联输出分别为图片、HTML 或其他文件
- **THEN** 图片具有替代文本，HTML 使用隔离 iframe，文件使用受控内容链接，且所有输出保留可识别标题

#### Scenario: 移动端多输出
- **WHEN** 窄屏设备展示一个 finding 的多个输出
- **THEN** 局部 Gallery 自动调整为不溢出、不重叠的单列或适配布局

