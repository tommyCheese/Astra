## ADDED Requirements

### Requirement: 自动选择搜索 provider
系统 SHALL 在 `WEB_SEARCH_PROVIDER=auto` 时根据可用凭据选择搜索路径，并且不得把缺少搜索凭据视为配置错误。

#### Scenario: 自动选择 Google
- **WHEN** `WEB_SEARCH_PROVIDER=auto` 且专用 Google API Key 与 Search Engine ID 均已配置
- **THEN** 系统使用 Google provider，并记录实际 provider 和自动选择模式

#### Scenario: 自动选择 Brave
- **WHEN** `WEB_SEARCH_PROVIDER=auto`、专用 Google 配置不完整且 `WEB_SEARCH_API_KEY` 已配置
- **THEN** 系统使用 Brave provider，并记录实际 provider 和自动选择模式

#### Scenario: 自动选择无密钥链路
- **WHEN** `WEB_SEARCH_PROVIDER=auto` 且没有完整的 Google 或 Brave 搜索凭据
- **THEN** 系统执行无密钥搜索链路，不返回 `missing_credentials`

### Requirement: 无密钥 provider 有限回退
系统 SHALL 在无密钥搜索链路中先执行 Bing RSS，并仅在 Bing 搜索失败或没有候选来源时执行 DuckDuckGo HTML。

#### Scenario: Bing 搜索成功
- **WHEN** Bing RSS 返回至少一个有效候选来源
- **THEN** 系统返回 Bing 结果，并且不请求 DuckDuckGo

#### Scenario: Bing 空结果后回退
- **WHEN** Bing RSS 请求成功但没有有效候选来源
- **THEN** 系统继续执行 DuckDuckGo，并记录空结果回退原因

#### Scenario: Bing 可恢复失败后回退
- **WHEN** Bing RSS 返回网络、超时、HTTP 或解析类搜索失败
- **THEN** 系统继续执行 DuckDuckGo，并记录安全的错误类别而不包含 secret

#### Scenario: 所有无密钥 provider 失败
- **WHEN** Bing 和 DuckDuckGo 都因 provider 错误无法完成搜索
- **THEN** 系统返回聚合 `search_failed`，其中只包含 provider 与脱敏错误类别

### Requirement: 无密钥搜索状态可审计
系统 SHALL 在自动搜索的成功输出中记录实际 provider、provider 尝试顺序、候选数量、回退结果和 degraded 状态。

#### Scenario: 首选 provider 成功的审计输出
- **WHEN** 自动选择的首个 provider 成功返回候选来源
- **THEN** 输出包含 `provider_mode=auto`、实际 `provider`、单项 `provider_attempts` 和对应候选数量

#### Scenario: 无密钥搜索标记降级
- **WHEN** 自动搜索使用 Bing 或 DuckDuckGo 公共入口
- **THEN** 输出包含 `degraded=true` 和说明无密钥公共搜索不保证生产 SLA 的 warning

#### Scenario: 回退搜索的审计输出
- **WHEN** 自动搜索从 Bing 回退到 DuckDuckGo
- **THEN** `provider_attempts` 按实际顺序记录两个 provider，并且 warning 描述回退原因

### Requirement: 显式 provider 不静默回退
系统 SHALL 将 `auto` 之外的 provider 配置视为显式选择，并保持该 provider 的严格执行语义。

#### Scenario: 显式 Google 缺少凭据
- **WHEN** `WEB_SEARCH_PROVIDER=google` 且 Google 凭据不完整
- **THEN** 工具返回 `missing_credentials`，并且不执行 Bing 或 DuckDuckGo

#### Scenario: 显式无密钥 provider 失败
- **WHEN** `WEB_SEARCH_PROVIDER=bing` 或 `duckduckgo` 且该 provider 执行失败
- **THEN** 工具返回该失败，并且不静默执行其他 provider

