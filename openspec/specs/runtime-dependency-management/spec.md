# runtime-dependency-management Specification

## Purpose
TBD - created by archiving change runtime-dependency-builder-and-fullstack-hardening. Update Purpose after archive.
## Requirements
### Requirement: Validated dependency declarations
系统 MUST 只接受规范包名与可选精确版本的 Python 依赖坐标；版本留空时安装构建时可获取的最新版本，并拒绝 URL、VCS、path、marker、pip 参数、重复包和受保护核心依赖覆盖。

#### Scenario: Reject unsafe requirement
- **WHEN** 用户提交 `pkg @ https://...` 或包含 shell/pip 参数的值
- **THEN** API 返回 `invalid_runtime_dependency` 且不创建构建

### Requirement: Auditable asynchronous runtime builds
系统 SHALL 创建可查询的 RuntimeBuild，记录状态、依赖摘要、image digest、脱敏日志、时间和错误，并限制同一 profile 同时只有一个构建。

构建期间系统 SHALL 持续更新阶段、进度和最新脱敏日志，并允许用户随时取消仍处于 queued 或 building 的构建。

#### Scenario: Build succeeds
- **WHEN** 合法依赖完成 Docker build 与 renderer smoke test
- **THEN** Build 标记 succeeded 并原子激活内容寻址 image

#### Scenario: Build fails
- **WHEN** 依赖解析、下载、构建或 smoke test 失败
- **THEN** Build 标记 failed，保留旧 active image 且不泄露宿主信息

#### Scenario: User cancels build
- **WHEN** 用户取消 queued 或 building 状态的构建
- **THEN** 系统终止构建子进程、标记 cancelled，并保留旧 active image

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

### Requirement: Managed runtime image lifecycle
系统 MUST 将候选镜像构建到唯一 staging tag，只有验证成功后才创建内容寻址 custom tag 并原子激活。系统 SHALL 持久化成功镜像清单及其依赖、digest 和激活时间，并通过 API 暴露当前保留策略。

系统 MUST 永远保护基础镜像和当前 active 镜像，默认额外保留最近 3 个 inactive 成功镜像，并清理超过 30 天或超出数量限制的其余 Astra custom 镜像。系统 MUST 在成功、失败和取消后尽力清理 staging tag，且只能定向删除严格匹配 Astra 管理命名空间的 tag，不得执行全局 Docker prune。

清理失败 MUST 保留镜像清单记录供后续构建重试，不得把已经成功的构建或激活回滚为失败。

#### Scenario: Successful build ages old images
- **WHEN** 新镜像成功激活且历史 inactive 镜像超过数量或时间策略
- **THEN** 系统保留基础镜像、active 镜像和受保护的最近成功镜像，只定向删除其余 Astra custom 镜像

#### Scenario: Build reaches a terminal state
- **WHEN** Runtime build 成功、失败或被用户取消
- **THEN** 系统尽力删除该 build 的 staging tag，且清理异常不覆盖原始构建终态

#### Scenario: Cleanup cannot remove an image
- **WHEN** Docker 因镜像仍被使用或暂时不可用而拒绝删除
- **THEN** 系统保留对应历史记录并在后续成功构建时再次尝试，不删除 active 或非 Astra 镜像

