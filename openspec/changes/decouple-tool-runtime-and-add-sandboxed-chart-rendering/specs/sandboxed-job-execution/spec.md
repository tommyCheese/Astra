## ADDED Requirements

### Requirement: Sandbox jobs are isolated from the API process
所有计算型工具 SHALL 通过独立 Sandbox Job 执行，且不得在 API 或 Agent 主进程中运行 Python、JavaScript 或 shell 代码。

#### Scenario: Submit a chart render
- **WHEN** Agent 调用 `chart.render`
- **THEN** 系统创建关联 Run 与 ToolCall 的 SandboxJob，并由独立 worker 执行

### Requirement: Pluggable sandbox executor
Sandbox Supervisor SHALL 通过与容器厂商无关的 `SandboxExecutor` 协议管理准备、启动、等待、终止和收集阶段。

#### Scenario: Use the OCI executor locally
- **WHEN** 本地部署配置 Docker executor
- **THEN** Supervisor 通过 OCI Container Executor 运行一次性容器，而上层工具无需使用 Docker 特有 API

#### Scenario: Use gVisor in production
- **WHEN** Linux 生产部署要求 gVisor runtime
- **THEN** OCI executor 使用配置的 `runsc` runtime，并将实际 runtime 记录在 Job 审计信息中

### Requirement: Secure-by-default execution profile
Sandbox Job MUST 默认断网、以非 root 用户运行、使用只读根文件系统、丢弃 capabilities、禁止 privilege escalation，且只能访问本 Job 的只读输入和可写输出目录。

#### Scenario: Attempt outbound network access
- **WHEN** 沙箱内进程尝试访问公网
- **THEN** 网络请求失败，Job 不获得任何宿主网络凭据，并记录策略限制

#### Scenario: Attempt host filesystem access
- **WHEN** 沙箱内进程尝试读取未挂载的宿主路径或 Docker socket
- **THEN** 访问失败且 Job 不能逃逸其输入输出目录

### Requirement: Enforced resource limits
Executor MUST 强制 wall time、CPU、内存、PID、打开文件、输出文件数量和总输出字节配额。

#### Scenario: Job exceeds memory limit
- **WHEN** 渲染进程超过内存配额
- **THEN** Executor 终止进程并将 Job 标记为 `failed`，错误类别为 `sandbox_oom`

#### Scenario: Job exceeds wall time
- **WHEN** 渲染进程超过 wall time
- **THEN** Executor 终止并清理容器，将 Job 标记为 `timed_out`

### Requirement: Immutable runtime profiles
每个 Sandbox Job SHALL 引用版本固定的 runtime image digest，并记录运行时、依赖版本、locale、timezone 和可复现性参数；沙箱不得在执行期间联网安装依赖。

#### Scenario: Execute Python chart runtime
- **WHEN** Python 图表 Job 启动
- **THEN** 它使用配置的不可变 image digest、非 GUI Matplotlib backend 和预安装依赖

### Requirement: Auditable lifecycle and cleanup
SandboxJob SHALL 遵循 `queued`、`preparing`、`running`、`collecting` 和终态状态机，并在任何终态后销毁一次性执行环境和临时可写层。

#### Scenario: Worker crashes during collection
- **WHEN** Worker 在收集输出时崩溃
- **THEN** 系统恢复 Job 为明确失败状态、保留安全审计信息并清理残留执行环境

### Requirement: Stable sandbox error taxonomy
系统 SHALL 将 executor 和 runtime 故障映射为稳定、安全的错误类别，并截断、脱敏 stdout 与 stderr。

#### Scenario: Runtime image missing
- **WHEN** 配置的 image digest 无法取得
- **THEN** ToolCall 以 `runtime_image_missing` 失败，用户响应不泄露宿主路径或内部命令
