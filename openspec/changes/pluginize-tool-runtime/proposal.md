## Why

Astra 的工具调用协议已经具备 Registry、manifest、权限和审计抽象，但默认装配、Effect 分析、结果处理、验证及设置 API 仍按 Web、Chart、Bash 工具名硬编码。即将进行的大幅工具系统改造需要先建立稳定的插件边界，使新增工具提供者无需修改 `AgentLoop` 或核心权限管线，同时保留现有的 fail-closed 安全属性。

## What Changes

- 引入可信的 Tool Provider Plugin 契约，使内建和外部提供者以声明式 manifest 贡献工具、执行后端、Effect Analyzer、Result Processor、Validator、审批展示器和配置 schema。
- 引入宿主控制的插件发现、校验、启停、健康检查和确定性 Catalog 组装流程；禁止从不可信 Task Workspace 自动加载可执行插件代码。
- 将工具调用重构为与工具名无关的阶段式 Invocation Pipeline：resolve、validate、analyze、authorize、execute、normalize、process、validate result、complete。
- 将 Web、Chart、Bash 迁移为内建插件，在兼容现有名称、输入输出、审批和审计记录的同时移除 `AgentLoop` 中的专用分支。
- 将工具设置 API 改为根据已加载插件和工具 manifest 动态生成，并持久化通用的 provider/tool 启用状态。
- 允许一个 Run 同时执行多个领域的 processor 和 validator，完成门控汇总所有适用结果，而不是在 Web 与 Chart 之间二选一。
- 冻结每个 Run 的插件、provider、tool schema、analyzer 和 validator 身份；恢复执行时若身份漂移则 fail closed。
- **BREAKING**：废弃通过 `Settings.tool_<name>_enabled`、静态 API 字段和直接修改 `build_tool_registry()` 注册新工具的扩展方式；保留一个版本周期的内建工具兼容适配层。

## Capabilities

### New Capabilities

- `tool-provider-plugin-system`: 定义插件 manifest、可信发现、生命周期、贡献点、Catalog 组装、配置和身份冻结行为。

### Modified Capabilities

- `policy-driven-tool-runtime`: 将工具解析、Effect 分析、执行、结果处理和验证改为贡献点驱动的通用 Invocation Pipeline，并移除具体工具名耦合。

## Impact

- 后端：`app/tools/`、`app/runner/agent_loop.py`、`app/runner/adapters.py`、`app/permissions/effects.py`、`app/runner/approvals.py`、工具设置 API、数据库模型和迁移。
- 内建运行时：Web、Chart、Bash 的 provider 包装、Sandbox backend 与 runtime 配置。
- 前端：工具/插件设置页面改为消费动态 Catalog 和配置 schema。
- 运维与安全：新增受管插件目录、provider allowlist、内容摘要、启动诊断和 Run 级插件快照。
- 测试：增加第三方示例插件、混合领域 Run、schema/analyzer 漂移、冲突注册、插件故障隔离和旧工具兼容测试。
