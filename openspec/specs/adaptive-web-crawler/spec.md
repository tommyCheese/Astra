# adaptive-web-crawler Specification

## Purpose
TBD - created by archiving change add-google-search-adaptive-crawler. Update Purpose after archive.
## Requirements
### Requirement: 自适应爬虫作为工具注册
系统 SHALL 将自适应网页抓取能力作为 `web_fetch` 工具注册到统一 tool registry。

#### Scenario: 工具 manifest 可用
- **WHEN** 系统启动并加载 `web_fetch` 工具
- **THEN** tool registry 中存在 `web_fetch` 工具 manifest，包含名称、版本、描述、输入 schema、输出 schema、权限、副作用等级、超时和错误类别

#### Scenario: 通过 registry 执行
- **WHEN** runner 需要抓取网页内容
- **THEN** runner 通过 tool registry 调用 `web_fetch`，而不是直接调用爬虫实现

### Requirement: 自适应网页抓取
系统 SHALL 提供自适应 `web_fetch`，用于抓取 URL 并提取页面主要内容。

#### Scenario: 抓取 HTML 页面
- **WHEN** runner 使用有效 URL 调用 `web_fetch`
- **THEN** 系统抓取页面，提取标题、metadata、主要正文、content type、检索时间和来源 URL

#### Scenario: 页面无法访问
- **WHEN** URL 超时、返回错误状态码、被阻止或无法解析
- **THEN** 系统记录 failed ToolCall，包含 URL、错误类别、错误详情和检索时间

### Requirement: 受控抓取策略
系统 SHALL 只允许爬虫使用已定义的抓取策略，不得执行模型生成的任意代码。

#### Scenario: 模型生成有效抓取策略
- **WHEN** 模型返回符合 schema 的 CrawlerPlan
- **THEN** 系统根据策略枚举和允许的 selector 执行抓取

#### Scenario: 模型生成无效抓取策略
- **WHEN** 模型返回未知策略、非法 selector 或非结构化内容
- **THEN** 系统拒绝该策略，并回退到默认抓取策略或记录失败

### Requirement: 内容提取质量评分
系统 SHALL 为每次成功抓取生成内容质量评分和 warning。

#### Scenario: 高质量正文
- **WHEN** 抽取正文达到最小长度且包含与搜索目标相关的内容
- **THEN** 工具输出包含较高质量评分，且不产生低质量 warning

#### Scenario: 低质量正文
- **WHEN** 抽取正文过短、重复、导航噪音过多或缺少相关性
- **THEN** 工具输出包含较低质量评分，并记录低质量 warning

### Requirement: 元数据优先提取
系统 SHALL 支持从 OpenGraph、Twitter Card、schema.org、title 和 meta description 中提取来源元数据。

#### Scenario: 页面包含结构化 metadata
- **WHEN** 页面包含可解析 metadata
- **THEN** 工具输出包含标题、描述、发布时间、站点名或作者等可用字段

### Requirement: 抓取输出可审计
系统 SHALL 在 `web_fetch` 输出中记录抓取策略、内容长度、质量评分、warnings 和来源类型。

#### Scenario: 记录抓取审计字段
- **WHEN** `web_fetch` 成功完成
- **THEN** ToolCall output 包含 extraction_strategy、content_length、quality_score、warnings、source_type 和 retrieved_at

