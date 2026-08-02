# google-web-search Specification

## Purpose
TBD - created by archiving change add-google-search-adaptive-crawler. Update Purpose after archive.
## Requirements
### Requirement: Google 搜索作为工具注册
系统 SHALL 将 Google 搜索能力作为 `web_search` 工具 provider 注册到统一 tool registry。

#### Scenario: 工具 manifest 可用
- **WHEN** 系统启动并加载 `web_search` 工具
- **THEN** tool registry 中存在 `web_search` 工具 manifest，包含名称、版本、描述、输入 schema、输出 schema、权限、副作用等级、超时和错误类别

#### Scenario: 通过 registry 执行
- **WHEN** runner 需要执行 Google 搜索
- **THEN** runner 通过 tool registry 调用 `web_search`，而不是直接调用 Google provider 实现

### Requirement: Google 搜索 provider
系统 SHALL 提供一个 Google `web_search` provider，通过 Google Programmable Search JSON API 获取真实搜索结果，并在显式选择 Google 时保持严格凭据校验。

#### Scenario: Google 搜索成功
- **WHEN** `WEB_SEARCH_PROVIDER=google` 且 Google API 配置有效，并且 runner 调用 `web_search`
- **THEN** 系统调用 Google 搜索 API，并返回结构化候选来源列表

#### Scenario: Google 配置缺失
- **WHEN** `WEB_SEARCH_PROVIDER=google` 但缺少 API Key 或 Search Engine ID
- **THEN** 系统记录 failed ToolCall，错误类别为配置错误，不暴露 secret 值，并且不静默切换到其他 provider

#### Scenario: 自动模式选择 Google
- **WHEN** `WEB_SEARCH_PROVIDER=auto` 且专用 Google API Key 与 Search Engine ID 均有效
- **THEN** 系统调用 Google 搜索 API，并在输出中记录自动选择模式和实际 Google provider

### Requirement: 搜索结果标准化
系统 SHALL 将 Google 搜索结果标准化为统一候选来源结构。

#### Scenario: 标准化候选来源
- **WHEN** Google API 返回搜索结果
- **THEN** 每个候选来源包含 URL、标题、摘要、排名、显示域名、provider metadata 和检索时间戳

#### Scenario: 空搜索结果
- **WHEN** Google API 返回空结果
- **THEN** 工具输出包含空候选列表，并记录可用于最终总结的证据不足 warning

### Requirement: 搜索参数可配置
系统 SHALL 支持通过后端配置控制 Google 搜索参数。

#### Scenario: 使用语言和地区配置
- **WHEN** 后端配置包含语言、地区、结果数量或安全搜索参数
- **THEN** Google 搜索请求使用这些参数，并在 ToolCall input 或 metadata 中记录非敏感参数

### Requirement: 搜索请求受审计
系统 SHALL 将 Google 搜索请求作为网络读取工具调用进行审计。

#### Scenario: 审计 Google 搜索
- **WHEN** Google `web_search` 被执行
- **THEN** ToolCall 记录包含 provider、查询、非敏感参数、候选数量、权限分类和只读副作用等级

### Requirement: 保留 mock 搜索
系统 SHALL 保留 mock 搜索 provider 作为确定性测试路径。

#### Scenario: mock 搜索仍可用
- **WHEN** `WEB_SEARCH_PROVIDER=mock`
- **THEN** `web_search` 返回确定性候选来源，并且不调用真实网络搜索 API

### Requirement: Normalized Google results retain logical query lineage
Google search candidates SHALL include their originating logical query identity, canonical URL, provider rank, retrieval time, and normalized constraint audit.

#### Scenario: Batched Google search
- **WHEN** multiple logical queries are executed through Google
- **THEN** candidates from each response retain the corresponding logical query identity

### Requirement: Google credentials remain outside grounding evidence
Google API keys and search-engine credentials MUST NOT appear in search traces, candidates, constraint audits, warnings, ToolCall output, or evidence records.

#### Scenario: Search result is persisted
- **WHEN** a Google search result is normalized and ingested into the Evidence Ledger
- **THEN** no credential value is present in the persisted evidence

