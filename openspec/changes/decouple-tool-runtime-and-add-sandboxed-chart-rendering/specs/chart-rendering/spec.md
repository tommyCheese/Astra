## ADDED Requirements

### Requirement: Declarative chart rendering tool
系统 SHALL 提供版本化的 `chart.render` 工具，接收声明式数据、图表编码、样式、尺寸、输出格式和可选 backend，不接受任意 Python、JavaScript 或 shell 源码。

#### Scenario: Render an inline dataset
- **WHEN** Agent 提交合法的列、行、x/y 编码和 line chart 请求
- **THEN** 工具生成图表 Artifact，并返回结构化 render metadata 与 ArtifactRef

#### Scenario: Reject source code input
- **WHEN** 请求尝试提交任意 Python 或 JavaScript 源码字段
- **THEN** 工具以 `invalid_input` 拒绝请求且不创建 Sandbox Job

### Requirement: Validated data inputs
`chart.render` MUST 验证数据 schema、类型、行列规模、字符串长度、缺失值、尺寸和允许的输出格式，并支持读取当前 Run 已授权的数据 Artifact。

#### Scenario: Input dataset exceeds limits
- **WHEN** 内联数据或输入 Artifact 超过配置的行数、列数或字节数限制
- **THEN** 工具在渲染前返回 `artifact_limit_exceeded` 或 `invalid_input`

### Requirement: Deterministic backend selection
系统 SHALL 支持 `auto`、`matplotlib`、`seaborn` 和 `echarts` backend；`auto` 必须根据图表类型、统计语义、交互需求和输出格式使用可测试的规则选择 backend。

#### Scenario: Select Seaborn for a statistical chart
- **WHEN** 请求使用 `auto` 并指定受支持的分布或回归图
- **THEN** 系统选择 Seaborn runtime 并在结果中记录选择依据

#### Scenario: Select ECharts for interaction
- **WHEN** 请求交互式 HTML 输出
- **THEN** 系统选择 ECharts runtime，且不得回退为未隔离的进程内浏览器渲染

### Requirement: Static chart outputs
Matplotlib、Seaborn 和 ECharts backend SHALL 根据能力支持 PNG 和 SVG 输出，并记录宽度、高度、MIME、checksum、backend、依赖版本和 runtime image digest。

#### Scenario: Render a PNG with Chinese labels
- **WHEN** 请求包含中文标题和轴标签
- **THEN** PNG 使用 runtime 内置字体正确渲染文字，且不存在缺字警告时标记成功

### Requirement: Safe interactive ECharts output
交互式 ECharts Artifact MUST 由受控模板和验证后的 chart spec 生成，禁止任意脚本、外部网络依赖和 Astra 身份凭据访问。

#### Scenario: Display an interactive chart
- **WHEN** 用户打开 ECharts HTML Artifact
- **THEN** 前端通过独立 origin 或严格 sandboxed iframe 与 CSP 展示，图表不能访问父页面 DOM 或 cookie

### Requirement: Chart render verification
Chart processor SHALL 验证预期输出数量、MIME、文件完整性、非空尺寸和 render warnings，并将结论交给通用完成门控。

#### Scenario: Renderer produces an empty file
- **WHEN** Sandbox Job 退出成功但输出图片为空或无法解析
- **THEN** Artifact 被拒绝，ToolCall 以 `invalid_artifact` 或 `render_failed` 失败

### Requirement: Reproducible chart rendering
图表 runtime SHALL 固定 locale、timezone、随机种子和依赖版本，并将有效渲染配置保存为 chart spec Artifact。

#### Scenario: Re-render the same chart request
- **WHEN** 相同数据、spec、runtime digest 和资源配置被再次执行
- **THEN** 系统产生语义等价的图表并保留可比较的 provenance
