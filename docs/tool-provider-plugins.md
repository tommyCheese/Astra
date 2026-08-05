# Tool Provider Plugin 运行与治理

Astra 的工具扩展以 `PluginDescriptor` 和 `PluginContribution` 为边界。一次 Run 在模型看到工具清单前冻结完整的 Provider、Tool、Executor、Effect Analyzer、Result Adapter/Processor、Validator、Approval Presenter、Runtime Backend 与配置版本；恢复运行时只允许显示文案变化，任何行为身份漂移都会失败关闭。

## 信任与执行边界

- `builtin` Provider 由平台装配；当前内建 Provider 是 `astra.web`、`astra.chart`、`astra.shell` 和控制面的 `astra.builtin`。
- `managed_package` 只允许管理员明确配置的包入口和 digest。发现功能默认关闭，也不会扫描 Task Workspace。
- `isolated_descriptor` 只能声明工具清单，不能向 Host 注入可执行 Analyzer、Processor、Validator、Presenter 或 Backend。每个外部工具必须解析到 Host 管理的 `RuntimeBackend`。
- 隔离传输只发送版本化 JSON：不传 Artifact、Workspace、数据库、Sandbox、Credential Broker 或 Delegation service 对象。网络权限和凭据只以显式开关与 credential reference 传递。
- Host 对隔离执行强制并发、wall time、取消、响应体大小、协议身份及 Tool output schema。外部返回的未知 annotation 会被拒绝。

## 生命周期与设置

Provider 状态依次经过 discovered、verified、loaded、healthy、enabled；也可能进入 disabled、unhealthy 或 draining。设置接口由当前目录动态生成：

- `GET /api/tools`：工具与 Provider 的标签、版本、健康、可用性、Schema 和启用状态。
- `GET /api/tool-providers`：Provider 目录。
- `PUT /api/tools/{tool_name}/state`：启停单个工具。
- `PUT /api/tool-providers/{provider_id}/state`：启停 Provider。
- `PUT /api/tool-providers/{provider_id}/config`：按 Provider JSON Schema 更新配置。

标记为 `x-secret` 的配置字段只接受 `{ "credential_ref": "..." }`，读取接口仅返回是否已配置，不返回引用或 secret。每次状态和配置写入都有审计记录，配置 revision 会进入后续 Run 的目录快照。

旧的固定字段 `PUT /api/tools` 仅保留一个版本周期，并返回 `Deprecation`、`Sunset` 和 successor `Link` 响应头。新客户端不得依赖它。

## 发布与回滚

默认 `TOOL_PLUGIN_ROLLOUT_MODE=builtin_only`，即使进程注入了外部 discovery source 也会忽略。只有在 Provider 身份、digest allowlist、Host Backend 和部署测试全部就绪后，才可设置为 `configured`。`TOOL_MANAGED_PLUGIN_DISCOVERY_ENABLED` 和 `TOOL_EXTERNAL_PLUGIN_DISCOVERY_ENABLED` 默认均为 false。

回滚步骤：

1. 将 `TOOL_PLUGIN_ROLLOUT_MODE` 改回 `builtin_only` 并重启后端。
2. 不删除 `tool_catalog_snapshots`、Provider 配置或 ToolCall 历史；新版本目录格式仍需保留。
3. 已暂停且快照包含外部 Provider 的 Run 会因行为目录不匹配而保持失败关闭；不要强制改写快照。新建 Run 使用内建目录。
4. 检查 `plugin.*` 日志中的 verification、health、catalog conflict、invocation failure 类别，再处理 allowlist、Backend 或 Provider 部署。

## 诊断

启动和调用路径记录不含配置值或工具输出的安全维度：`provider_id`、`tool_name`、stage、state、category。内存指标累计发现、验证、加载、健康、冲突、目录装配、调用成功/失败次数及耗时，可由部署的 metrics adapter 导出。

常见故障：

- `provider_digest_changed`：部署内容与 allowlist 或已观察 digest 不一致。
- `runtime_backend_unavailable`：外部工具没有 Host 管理的 Backend，或绑定到了其他 Provider。
- `isolated_protocol_invalid` / `isolated_identity_forged`：响应协议、request/provider/tool 身份或字段不合法。
- `isolated_timeout` / `isolated_response_too_large`：Provider 超出部署资源边界。
- `invalid_result`：Provider 结果不符合冻结的 Tool output schema。
