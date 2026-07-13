## Why

Astra 当前虽然具备通用工具注册接口，但默认注册、路由权限、结果后处理、Evidence Pack 和完成验证仍与 `web_search`、`web_fetch` 固定绑定，无法安全扩展为通用 Agent 工具平台。新增 Matplotlib、Seaborn 和 ECharts 绘图能力会引入计算执行、文件工件和不可信内容渲染，因此必须先建立可组合工具运行时、可审计 Artifact 管线和真正隔离的沙箱执行机制。

## What Changes

- 将 Web 工具从 Agent 主循环解耦：通用循环只处理统一的工具观察、Artifact 引用和完成决策，Web 候选过滤、来源证据与验证逻辑由 Web 领域组件负责。
- 将默认 Web-only Registry 和名称级 allowlist 改为可组合 Registry、能力声明与策略驱动路由，同时保留 schema、预算、权限、副作用和重试审计。
- 新增统一的 `SandboxJob` 与供应商无关 `SandboxProvider` 边界；首个生产实现使用 Docker Engine，测试使用 Mock Provider，本地与部署共用相同 OCI image 和安全参数。
- 新增版本化 OCI `astra-data-viz` image，使用 `uv.lock` 和 npm lock 在构建阶段固定 Python/Node 绘图生态；镜像构建后可完全离线执行。
- 新增隔离的 Python/ECharts renderer entrypoint，以安全方式生成静态 PNG/SVG 或交互式 HTML 图表工件。
- 新增声明式 `chart.render` 工具，由 Astra 根据图表请求选择 Matplotlib、Seaborn 或 ECharts；第一阶段不向 Agent 开放任意 `python.execute`。
- 扩展 Artifact 存储、元数据、校验、交付和前端展示，使工具输出通过 `ArtifactRef` 返回，并支持图片、SVG、交互式图表、图表 spec、数据集和沙箱日志。
- 为沙箱故障、超时、资源耗尽、非法输出和渲染失败建立稳定的错误分类、事件与审计记录。

## Capabilities

### New Capabilities

- `policy-driven-tool-runtime`: 可组合的工具注册、能力声明、策略路由、通用观察处理，以及与 Web 领域流程的解耦。
- `sandboxed-job-execution`: 基于 `SandboxJob` 和可替换 `SandboxProvider` 的隔离计算，首个实现覆盖 Docker Engine、网络策略、资源限制、生命周期、指标与审计要求。
- `chart-rendering`: 声明式 `chart.render` 工具及 Matplotlib、Seaborn、ECharts 后端选择、验证和标准化输出。
- `artifact-storage-and-delivery`: 工具生成文件的持久化、完整性元数据、访问控制、安全交付与前端预览。

### Modified Capabilities

无。当前仓库尚未建立主规格目录，本 change 将上述行为建立为新能力规格。

## Impact

- 后端：`backend/app/tools`、`backend/app/runner`、配置、数据库模型、Repository、API、事件与错误映射。
- Runtime：新增 Docker Provider、Mock Provider、版本化 OCI image 构建和本地 Sandbox 生命周期管理。
- 依赖：运行时要求 Docker CLI/Engine；`uv.lock` 固定 Python 数据/绘图库，npm lock 固定 Node.js、ECharts 和 Headless Chromium 依赖。
- 前端：新增 Artifact 卡片、静态图像/SVG 预览、sandboxed iframe 交互图表和审计信息展示。
- 数据模型/API：新增或扩展 Sandbox Job、Artifact、Tool manifest、Artifact delivery 和 Run detail 表达；迁移期间保持已有 Web 工具名称及调用兼容。
- 运维与安全：需要 OCI image 发布、image digest/lock digest 固定、rootless 模式、网络关闭、资源限制、输出清理和日志脱敏策略。
