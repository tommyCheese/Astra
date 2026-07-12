## 1. 工具契约与策略路由

- [x] 1.1 扩展 `ToolSpec`，加入 capabilities、permission set、risk、execution backend、resource profile 和 artifact behavior，并兼容映射旧 permission/side-effect 字段
- [x] 1.2 定义 `ToolResultEnvelope`、`ArtifactRef` 和 capability availability 数据结构及序列化契约
- [x] 1.3 实现可组合 Tool Registry builder，使 Web 与 chart provider 可独立注册
- [x] 1.4 将 `ToolRouter` 改为按注册、schema、Run policy、capability、权限、风险、预算和 backend availability 的顺序解析
- [x] 1.5 让 ContextAssembler 只向模型暴露当前 Run 实际可调用的 manifest，并记录 capability 不可用原因
- [x] 1.6 增加工具契约、旧字段兼容、组合注册、策略允许/拒绝和不可用 manifest 过滤单元测试
- [x] 1.7 定义并注入 `ToolExecutionContext`，迁移现有工具兼容签名，并测试 Run/ToolCall/Step/trace 与服务关联

## 2. Agent Runtime 与 Web 流程解耦

- [x] 2.1 定义可注册的 Tool result processor、domain evidence builder 和 validator 接口
- [x] 2.2 将 Web 候选过滤、去重、抓取来源聚合和 Evidence Pack 构建迁入 Web processor
- [x] 2.3 将 Web 来源完成验证迁入 Web validator，并接入通用 CompletionGate 汇总
- [x] 2.4 移除 AgentLoop 中按 `web_search`、`web_fetch` 分支的结果解释和 Web 临时状态
- [x] 2.5 将 `_step_for_tool` 改为使用 manifest/plan metadata 的通用步骤匹配
- [x] 2.6 将 model prompts 和 fallback planner 从固定 Web 工具规则改为基于当前 manifest 与 capability 描述
- [x] 2.7 保留 `_execute_web_query` 兼容开关，并增加 legacy/general 双路径等价回归测试
- [x] 2.8 验证无 Web 工具的通用问答和仅 chart 任务不会因缺少 Web 证据被阻塞

## 3. Artifact 数据模型与存储

- [x] 3.1 设计并创建数据库迁移，扩展 Artifact 的 MIME、size、checksum、storage key、preview key、security status、ToolCall/SandboxJob 关联和 provenance 字段
- [x] 3.2 实现 Artifact Store 协议及本地文件系统实现，确保 storage key 与真实路径隔离
- [x] 3.3 实现 Artifact collector 的路径规范化、允许目录、文件数量/大小、MIME sniffing、checksum 和类型校验
- [x] 3.4 实现 Artifact service，用于持久化元数据、关联输入输出 provenance 和生成 ArtifactRef
- [x] 3.5 实现按 Run/Workspace 授权的 Artifact 内容 API，隐藏 storage key 并支持受控响应或短期地址
- [x] 3.6 实现 retention、过期内容删除、失败 Job 临时目录清理和审计元数据保留
- [x] 3.7 增加路径穿越、symlink 逃逸、伪造 MIME、超限文件、未授权访问和 retention 测试

## 4. Sandbox Job 模型与状态机

- [x] 4.1 新增 SandboxJob 数据模型、Repository 和迁移，覆盖关联、runtime profile、资源限制、状态、时间、退出原因、日志摘要和 Artifact
- [x] 4.2 实现 `queued → preparing → running → collecting → terminal` 状态机与合法迁移校验
- [x] 4.3 定义 `SandboxProvider` 的 create、upload、execute、download、metrics 和 terminate 协议
- [x] 4.4 实现 Sandbox Supervisor，负责 Job 编排、超时、取消、异常恢复和 finally cleanup
- [x] 4.5 实现 `sandbox_unavailable`、`runtime_image_missing`、`sandbox_timeout`、`sandbox_oom`、`sandbox_policy_violation`、`artifact_limit_exceeded`、`invalid_artifact`、`render_failed` 错误映射
- [x] 4.6 实现 stdout/stderr 截断、脱敏和仅审计存储策略
- [x] 4.7 增加状态迁移、worker crash、取消、超时、重复收集和 cleanup 幂等性测试

## 5. OCI Container Executor（已完成原型，后由 E2B Provider 替代）

- [x] 5.1 实现不向上层泄露 Docker 对象的 OCI Container Executor adapter
- [x] 5.2 配置每 Job 一次性容器、非 root、只读 rootfs、drop capabilities、no-new-privileges 和 seccomp
- [x] 5.3 配置默认无网络、只读输入挂载、唯一可写输出挂载，并禁止宿主目录与 Docker socket 透传
- [x] 5.4 实施 wall time、CPU、内存、PID、打开文件和输出目录配额，并映射 OOM/timeout 退出原因
- [x] 5.5 支持本地 Docker runtime 配置及 Linux gVisor `runsc` 配置，记录实际 runtime 和 image digest
- [x] 5.6 在要求 gVisor 但不可用时拒绝 sandbox capability，禁止静默回退到较弱隔离
- [ ] 5.7 增加 E2B 可选集成测试，覆盖 secure、断网、TTL、资源指标、终止和 Sandbox 清理
- [x] 5.8 增加 Linux/gVisor runtime contract 测试入口和部署检查命令

## 6. 版本化绘图 Runtime Images

- [x] 6.1 创建 `astra-runtime-python` image，锁定 Python、NumPy、Pandas、SciPy、Matplotlib、Seaborn、Pillow、PyArrow 和 Astra runtime SDK
- [x] 6.2 在 Python image 中配置 `Agg` backend、中文字体、固定 locale/timezone、随机种子和非 root runtime 用户
- [x] 6.3 创建 `astra-runtime-echarts` image，锁定 Node.js、ECharts、Headless Chromium 和受控渲染程序
- [x] 6.4 为两个 image 生成版本与依赖清单，支持 digest 固定、SBOM 和漏洞扫描
- [x] 6.5 实现 runtime entrypoint 输入/输出协议，禁止运行时联网安装依赖和任意源码入口
- [x] 6.6 增加 E2B Template contract tests，验证 uv/npm lock、版本、字体、断网、输出目录和最小示例渲染

## 7. 声明式 chart.render 工具

- [x] 7.1 定义版本化 Chart Request schema，覆盖内联数据/Artifact 输入、chart type、encoding、style、尺寸、outputs 和 backend
- [x] 7.2 实现数据类型、行列规模、字符串长度、缺失值、尺寸、输出格式和禁止源码字段校验
- [x] 7.3 实现可测试的 `auto` backend 选择规则和选择原因记录
- [x] 7.4 实现受控 Matplotlib renderer，输出 PNG/SVG 与有效 chart spec
- [x] 7.5 实现受控 Seaborn renderer，支持已定义的统计图子集和 PNG/SVG 输出
- [x] 7.6 实现受控 ECharts spec renderer，以及隔离的 PNG/SVG/HTML 输出
- [x] 7.7 实现 `chart.render` Tool，将请求转换为 SandboxJob，并以 ToolResultEnvelope 返回 ArtifactRef 和 render metadata
- [x] 7.8 实现 Chart processor/validator，检查输出数量、MIME、可解析尺寸、warnings 和 runtime provenance
- [x] 7.9 增加非法源码、超限数据、backend 选择、中文字体、空文件、损坏文件和可复现性测试

## 8. API、前端与安全展示

- [x] 8.1 扩展 Run detail 和事件 API，返回 Tool capability、SandboxJob 摘要和 ArtifactRef/provenance
- [x] 8.2 在聊天消息中实现静态 PNG 和安全 SVG Artifact 卡片、预览、标题、尺寸与加载失败状态
- [x] 8.3 为 ECharts HTML 实现独立 Artifact origin 或严格 sandboxed iframe、固定 CSP、无 cookie 和无任意网络策略
- [x] 8.4 在审计面板展示 SandboxJob 状态、runtime/image digest、配额、错误类别和输入输出 Artifact 关系
- [x] 8.5 为数据集、chart spec 和截断 sandbox log 提供类型化查看入口
- [x] 8.6 增加前端组件、授权失败、恶意 HTML/SVG、CSP 和无障碍测试

## 9. 配置、可观测性与运维

- [x] 9.1 增加 chart capability、Provider、E2B Template/lock digest、Artifact Store、资源配额和 retention 配置
- [x] 9.2 增加启动时 capability 探测，确保 sandbox 不可用时工具不进入模型上下文
- [x] 9.3 增加 Sandbox Job 时长、排队时间、成功率、OOM/timeout、Artifact 字节数和 backend 分布指标
- [x] 9.4 为 Job、ToolCall、Run 和 Artifact 传播 trace ID，并确保日志不包含数据正文、凭据或宿主路径
- [x] 9.5 编写 E2B 配置、uv/npm lock、Template 构建和跨平台部署文档
- [x] 9.6 编写威胁模型、Template/lock 发布、key 轮换、漏洞响应、Artifact 清理和事故处置文档

## 10. 端到端验证与迁移收尾

- [x] 10.1 增加 mock executor 端到端测试，覆盖 Agent 选择 `chart.render`、Job 生命周期、Artifact 回传和最终答案展示
- [ ] 10.2 增加 E2B 端到端测试，分别验证 Matplotlib、Seaborn 和 ECharts 的静态输出
- [ ] 10.3 增加交互式 ECharts 安全展示端到端测试，并验证不能访问父页面、cookie 或公网
- [x] 10.4 运行全部已有 Web Agent、reasoning、error contract、API 和前端回归测试并修复兼容问题
- [x] 10.5 在通用路径达到等价覆盖后移除 `_execute_web_query` fallback、Web-only Registry 默认构造和硬编码 allowlist
- [x] 10.6 更新后端架构、企业实现指南、README 和工具扩展文档，正文使用中文并保留协议标识符
- [ ] 10.7 完成安全验收清单：无进程内代码执行、默认断网、无特权、资源限制、输出校验、授权交付和完整 provenance

## 11. E2B Provider 与 uv Template 迁移

- [x] 11.1 将 `SandboxExecutor` 重构为供应商无关 `SandboxProvider` 生命周期协议
- [x] 11.2 实现 E2B Firecracker Provider，覆盖 create/upload/execute/download/metrics/terminate 与错误映射
- [x] 11.3 实现 Mock Provider，并用单元测试验证生命周期、超时和 finally terminate
- [x] 11.4 创建版本化 `astra-data-viz` E2B Template，以 `uv.lock` 和 `package-lock.json` 固定 Python/Node 依赖
- [x] 11.5 将 `chart.render` 迁移到 E2B Provider，并记录 template ID、lock digest 和 provider provenance
- [x] 11.6 移除第一版 OCI/Docker/gVisor executor、Dockerfiles、检查脚本和本地 Docker 前置条件
- [x] 11.7 更新配置、capability 探测、错误、指标、运维和安全文档，覆盖 E2B key/template 发布与轮换
