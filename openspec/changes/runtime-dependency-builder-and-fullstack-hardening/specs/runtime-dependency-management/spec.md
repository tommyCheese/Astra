## ADDED Requirements

### Requirement: Validated dependency declarations
系统 MUST 只接受规范包名与可选精确版本的 Python 依赖坐标；版本留空时安装构建时可获取的最新版本，并拒绝 URL、VCS、path、marker、pip 参数、重复包和受保护核心依赖覆盖。

#### Scenario: Reject unsafe requirement
- **WHEN** 用户提交 `pkg @ https://...` 或包含 shell/pip 参数的值
- **THEN** API 返回 `invalid_runtime_dependency` 且不创建构建

### Requirement: Auditable asynchronous runtime builds
系统 SHALL 创建可查询的 RuntimeBuild，记录状态、依赖摘要、image digest、脱敏日志、时间和错误，并限制同一 profile 同时只有一个构建。

#### Scenario: Build succeeds
- **WHEN** 合法依赖完成 Docker build 与 renderer smoke test
- **THEN** Build 标记 succeeded 并原子激活内容寻址 image

#### Scenario: Build fails
- **WHEN** 依赖解析、下载、构建或 smoke test 失败
- **THEN** Build 标记 failed，保留旧 active image 且不泄露宿主信息

### Requirement: Immutable job runtime activation
普通 Tool Job MUST 使用 profile 当前 active image、默认断网且禁止运行期包安装；后续调用自动使用最新成功激活版本。

#### Scenario: Next chart call uses custom dependency image
- **WHEN** 新 RuntimeBuild 成功激活
- **THEN** 下一次 `chart.render` 的 provenance 记录新 image digest 与 dependency digest

### Requirement: Runtime settings interface
设置页 SHALL 提供 Runtime tab，以列表查看当前 image 与依赖，支持单项或批量新增、编辑、删除依赖，触发构建并展示 queued/building/succeeded/failed 状态与可恢复错误。

核心依赖 SHALL 默认展示实际锁定版本，并在界面中禁用修改、选择和删除操作。

#### Scenario: User builds dependencies
- **WHEN** 用户添加 `numpy==2.2.6` 并点击构建
- **THEN** 界面禁用重复提交、持续更新状态并在成功后显示 active image
