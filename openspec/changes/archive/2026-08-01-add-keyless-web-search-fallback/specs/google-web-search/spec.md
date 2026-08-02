## MODIFIED Requirements

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

