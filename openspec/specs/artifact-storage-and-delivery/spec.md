# artifact-storage-and-delivery Specification

## Purpose
TBD - created by archiving change decouple-tool-runtime-and-add-sandboxed-chart-rendering. Update Purpose after archive.
## Requirements
### Requirement: Artifact metadata and content separation
系统 SHALL 将 Artifact 元数据保存在数据库，将二进制或大型内容保存在 Artifact Store；ToolCall output 只能返回小型结构化数据和 ArtifactRef，不得内嵌图片 base64 或大型 HTML。

#### Scenario: Store a rendered PNG
- **WHEN** Sandbox Job 生成合法 PNG
- **THEN** 文件进入 Artifact Store，数据库记录 identity、storage key、MIME、size、checksum 和 provenance，ToolCall 返回 ArtifactRef

### Requirement: Complete artifact provenance
每个工具生成的 Artifact SHALL 关联 Run、ToolCall、可选 SandboxJob、Provider、OCI image digest、lock digest、生成时间和输入 Artifact。

#### Scenario: Inspect chart provenance
- **WHEN** 用户在审计面板查看图表 Artifact
- **THEN** 系统可追溯生成它的工具调用、runtime、输入数据和校验状态

### Requirement: Validate collected files
Artifact collector MUST 在持久化前执行路径规范化、允许目录检查、文件数量和大小配额、MIME sniffing、checksum 计算及类型特定安全验证。

#### Scenario: Renderer writes outside output directory
- **WHEN** runtime 声明或链接到输出目录以外的文件
- **THEN** collector 拒绝该文件、记录 `sandbox_policy_violation`，且不得读取或持久化目标内容

#### Scenario: File extension mismatches MIME
- **WHEN** `.png` 输出的实际内容不是允许的图像类型
- **THEN** collector 将其标记为 `invalid_artifact` 并阻止交付

### Requirement: Authorized artifact delivery
Artifact API SHALL 在交付前验证用户对所属 Run/Workspace 的访问权，并使用受控响应或短期签名地址提供内容。

#### Scenario: Unauthorized artifact request
- **WHEN** 用户请求其无权访问的 Artifact ID
- **THEN** API 拒绝请求且不泄露 storage key 或文件是否存在

### Requirement: Safe artifact presentation
前端 SHALL 根据受信任的 MIME 与安全状态选择预览器；PNG/SVG、HTML、数据集和日志使用不同的安全展示策略。

#### Scenario: Preview a static chart
- **WHEN** 最终答案包含已验证 PNG 或安全 SVG ArtifactRef
- **THEN** 前端在消息内显示预览，并提供可访问的标题与尺寸信息

#### Scenario: Preview interactive HTML
- **WHEN** Artifact 类型为已验证 ECharts HTML
- **THEN** 前端仅通过配置的隔离 origin 或 sandboxed iframe 预览

### Requirement: Artifact retention and cleanup
系统 SHALL 按部署配置执行 Artifact retention、过期清理和失败 Job 临时文件清理，同时保留必要的审计元数据。

#### Scenario: Artifact retention expires
- **WHEN** Artifact 超过 retention policy 且没有保留标记
- **THEN** 内容从 Artifact Store 删除，数据库记录更新为不可用而不破坏 Run 审计关系

