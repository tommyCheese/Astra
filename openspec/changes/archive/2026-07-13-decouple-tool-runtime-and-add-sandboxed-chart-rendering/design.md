## Context

当前 `ToolRegistry` 和 `ToolSpec` 提供了基础抽象，但实际运行路径仍由 Web 领域主导：`RunEngine` 默认构造 Web Registry，`ToolRouter` 写死 Web allowlist 和 `network_read/read_only` 权限组合，`AgentLoop` 固定使用 `WebTaskAdapter`，并在循环内维护搜索候选、抓取来源和 Evidence Pack。旧 `_execute_web_query` 还保留固定搜索—抓取编排。

图表渲染将首次要求 Astra 执行计算型工作负载并交付文件。Matplotlib/Seaborn 依赖 Python 数据生态，ECharts 依赖 JavaScript 与浏览器渲染。它们不能在 FastAPI 主进程中运行，也不能依赖 Python `venv`、import allowlist 或异常捕获作为安全边界。系统需要隔离执行、资源控制、工件溯源和安全展示，同时保留现有 Web Agent 行为。

## Goals / Non-Goals

**Goals:**

- 让 Agent Kernel 与 Web 工具名称、结果格式、Evidence Pack 和验证规则解耦。
- 通过 capability、permission、risk、execution backend 和 policy 选择工具，而不是维护硬编码名称列表。
- 建立与具体供应商解耦的 `SandboxProvider` 协议和可审计 `SandboxJob` 生命周期。
- 以一次性 hardened Docker 容器提供默认断网、隔离文件系统、只读 rootfs 和资源限制。
- 提供声明式 `chart.render`，统一支持 Matplotlib、Seaborn 和 ECharts。
- 将大体积或可展示输出保存为 Artifact，并以 `ArtifactRef` 进入 Tool observation、最终答案和 UI。
- 保持 `web_search`、`web_fetch` 的现有名称、审计和通用问答能力兼容。

**Non-Goals:**

- 不开放任意 `python.execute`、shell、用户自定义包安装或运行时 `pip install`。
- 不允许沙箱直接访问公网；外部数据必须先由授权工具转为输入 Artifact。
- 不在本 change 中建设任意用户插件市场或第三方 runtime image 上传。
- 不保证第一版支持所有 Matplotlib、Seaborn 或 ECharts 原生参数；只支持声明式 schema 中稳定、可验证的子集。
- 不在第一版建设自托管虚拟化集群；仅保留 Provider 可替换边界。

## Decisions

### 1. 将 Agent Kernel 与领域处理器分层

`AgentLoop` 只负责决策、预算、工具执行、统一 observation、反思和完成门控。工具输出首先转换为统一 `ToolResultEnvelope`，包含状态、结构化数据、warnings、metrics 和 `ArtifactRef[]`。Web 候选过滤、Evidence Pack 聚合和来源验证迁入 Web capability processor；图表元数据和渲染验证由 Chart processor 负责。

任务完成由通用 `CompletionGate` 汇总领域 validator 的结论，不再假设所有任务都需要 Web 来源。旧 `_execute_web_query` 在兼容期开关后保留，待通用路径覆盖等价测试后删除。

替代方案是继续在 `AgentLoop` 中增加按工具名分支；该方案会令每种新能力修改核心循环，因此拒绝。

### 2. 使用策略驱动的 Tool Router

扩展 Tool manifest，使其声明 capabilities、permission set、risk、execution backend、resource profile 和 artifact behavior。Router 按以下顺序解析：已注册、run/workspace 已启用、输入 schema 合法、capability 获准、权限与风险策略获准、预算可用、执行 backend 可用。

现有 `permission` 与 `side_effect_level` 在迁移期保留并映射到新字段。Web 工具映射为 `network_read`；`chart.render` 映射为 `sandboxed_compute` 与 `artifact_write`。Context 只向模型暴露当前策略允许的 manifest，避免模型反复选择必然被拒绝的工具。

工具执行接口接收由 Agent Runtime 构造的 `ToolExecutionContext`，至少包含 `run_id`、`tool_call_id`、可选 `step_id`、`trace_id`、Artifact service 和 Sandbox service。工具不得通过全局数据库 session 或进程级可变状态获取这些关联。现有只读 Web 工具在迁移期可忽略 context，但所有产生 SandboxJob 或 Artifact 的工具必须使用它，以保持 `Run → ToolCall → SandboxJob → Artifact` 的完整 provenance。

### 3. 使用 SandboxJob + SandboxProvider，而非进程内执行

API/Agent 进程创建 `SandboxJob`，记录 run、tool call、runtime profile、输入 Artifact、资源限制、状态、timestamps、exit reason 和输出 Artifact。Sandbox Supervisor 通过 `SandboxProvider` 执行 Job。

首个生产 Provider 是 Docker Engine：macOS 本地和 Linux 部署共用相同 Dockerfile、image 与 hardening flags。每个 Job 创建一次性 OCI 容器，上传声明式输入，仅下载 `/output`，结束、超时或异常后都必须删除。Provider 接口不暴露 Docker SDK 对象，未来可用相同契约接入其他本地或远程实现。

生产策略默认：rootless、`--network none`、只读 rootfs、非 root 用户、drop capabilities、no-new-privileges、tmpfs 输入输出、固定 image digest、CPU/内存/PID/wall time/输出配额和资源指标采集。

仅依靠 `venv` 或 Python AST/import 过滤无法隔离文件系统、子进程和内核资源，因此不作为安全边界。

#### 2026-07 本地离线 Provider 决策

E2B 被确认是默认托管云服务，不满足本地离线目标，因此移除。Microsandbox 仍处 beta，Podman 虽支持 rootless，但团队最终选择 Docker Engine，以获得最成熟的本地、CI 与生产部署路径。完整 Python native wheels 进入固定 OCI image，镜像准备后 Job 无需公网；测试仍使用 Mock Provider。

先前 OCI/Docker/gVisor executor 不再属于第一版实现，也不作为本地前置条件。未来 BYOC 或自托管实现必须作为新的 Provider 接入，不得改变 Tool、SandboxJob 或 Artifact 契约。

### 4. Python 与 ECharts 使用版本化 OCI image

`astra-data-viz` image 包含 Python 3.12、uv、NumPy、Pandas、SciPy、Matplotlib、Seaborn、Pillow、PyArrow、中文字体、Node.js、ECharts、Headless Chromium 和 Astra renderer，并强制 Matplotlib `Agg` backend。

Job 记录 OCI image digest、uv/npm lock digest、runtime 版本、依赖版本、locale、timezone 和随机种子。正常 Job 禁止联网安装依赖；新增依赖必须生成新的 lock 并构建新 image。

### 5. 暴露声明式 chart.render，而非三个库级工具

模型调用单一 `chart.render`，输入包括数据或输入 Artifact、chart type、encoding、style、尺寸、期望 outputs 和可选 backend。`auto` backend 根据图表类型及输出选择：统计图优先 Seaborn，精细静态图优先 Matplotlib，交互图优先 ECharts。

工具先验证 schema、行列规模、数据类型、尺寸和输出格式，再生成受控渲染计划。第一版不执行模型提供的任意源码。静态输出支持 PNG/SVG；ECharts 可额外输出受控 HTML 和原始 chart spec。

### 6. Artifact 内容与数据库元数据分离

数据库保存 Artifact identity、type、MIME、size、checksum、storage key、preview key、run/tool call/job 关联、runtime provenance 和安全状态；文件内容进入 Artifact Store。开发环境可使用本地目录，生产可使用对象存储，但 API 始终通过 Artifact service 授权交付。

ToolCall output 只保存小型结构化结果和 `ArtifactRef`，不内嵌图片/base64 或大型 HTML。输出进入存储前进行路径归一化、文件数量/体积检查、MIME sniffing 和 checksum 计算。

### 7. 交互式 ECharts 使用隔离展示

PNG/SVG 可直接作为受控资源展示。交互式 HTML 必须由 Astra 模板生成，使用独立 origin 或严格 sandboxed iframe，禁止继承 Astra cookie、父页面 DOM 权限和任意网络访问，并应用固定 CSP。原始用户或模型 HTML 不得直接展示。

### 8. 采用明确的 Sandbox 状态机与错误分类

Job 状态为 `queued → preparing → running → collecting → succeeded|failed|timed_out|cancelled`。错误稳定映射为 `sandbox_unavailable`、`runtime_image_missing`、`sandbox_timeout`、`sandbox_oom`、`sandbox_policy_violation`、`artifact_limit_exceeded`、`invalid_artifact`、`render_failed`。stdout/stderr 截断和脱敏后仅供审计，不直接注入用户答案。

## Risks / Trade-offs

- [Risk] 容器共享 Linux 内核，隔离强度低于 microVM。→ 强制 rootless 与完整 hardening flags，不开放任意源码；未来高风险任意代码执行应新增 microVM Provider。
- [Risk] Docker daemon 拥有较高宿主权限。→ API 不挂载 Docker socket；Provider 应运行在受控 worker，Job 容器强制 hardening，生产限制 daemon 访问主体。
- [Risk] Headless Chromium 增加镜像体积与漏洞面。→ 与 Python runtime 分离、固定版本、定期扫描，仅在 ECharts job 中启动。
- [Risk] 声明式 schema 限制高级绘图表达能力。→ 第一版优先安全与确定性，保留版本化 schema 和后续受审批高级计算能力。
- [Risk] Web 解耦可能改变现有证据验证结果。→ 保留 Web processor 的输出契约，增加 legacy/general 双路径回归测试后再删除旧路径。
- [Risk] Artifact 文件导致磁盘或对象存储膨胀。→ 强制单 Job 配额、retention policy、按 run 清理和内容 checksum。
- [Risk] SVG/HTML 可携带脚本或外链。→ SVG 清洗或图片化；HTML 仅由受控模板生成，并使用隔离 origin、iframe sandbox 和 CSP。
- [Trade-off] macOS 仍需轻量 Linux VM 承载 OCI 容器。→ 这是完整原生 Python 生态与跨平台一致性的必要代价，但无需 Docker Desktop 或云端服务。

## Migration Plan

1. 扩展 Tool manifest 和 Router policy，同时提供旧字段兼容映射；Web 工具先迁移且保持 API 行为不变。
2. 引入通用 Tool result envelope、processor/validator 注册机制，将 Web 后处理从 AgentLoop 移出，并运行 legacy/general 对照测试。
3. 增加 Artifact 数据迁移和 storage service；先支持本地存储，再接入前端静态预览。
4. 增加 SandboxJob、Supervisor、Docker Provider、Mock Provider 和 `astra-data-viz` image contract tests；默认功能开关关闭。
5. 实现 `chart.render` 和 Artifact 展示，在本地 Docker 环境启用端到端测试。
6. 在 staging 验证 TTL、断网、资源指标、异常终止和恶意输出场景，再逐步启用生产功能开关。
7. 通用工具路径稳定后删除 `_execute_web_query` fallback 和 Web-only 配置命名。

回滚时关闭 chart/sandbox capability，保留数据库记录和 Artifact 只读访问；Web 工具继续走已验证的兼容路径。数据库迁移必须允许旧服务忽略新增 nullable 字段。

## Open Questions

- 生产首个 Artifact Store 使用本地持久卷还是对象存储，由部署环境在实现前确定；接口不依赖具体选择。
- ECharts 交互式 HTML 是否在第一阶段对所有部署启用，还是仅在具备独立 Artifact origin 时启用。
- 高风险任意代码执行阶段是否增加 microsandbox 或其他 microVM Provider；第一版不做静默降级。
