# Astra

Astra 是一个 AI 原生的通用 Agent 平台。

我们的目标不是复制一个只会写代码的聊天助手，而是在前沿大模型之上构建一层通用任务操作系统：它能够理解用户目标、工作空间、知识环境和外部系统，规划可持续执行的任务，通过工具完成真实操作，验证结果，并随着时间沉淀长期记忆。

从这里开始：

- [Astra 文档中心](docs/README.md)
- [完整软件开发生命周期](docs/software-development-lifecycle/README.md)
- [跟着一次请求读懂 Astra Agent](docs/agent-implementation-execution-walkthrough.md)
- [维护者发布指南](docs/releasing.md)

## 使用 Release 安装

每个稳定版 GitHub Release 都提供 Compose 安装包、SHA-256 校验和、SPDX
SBOM，以及带构建来源证明的 `linux/amd64` / `linux/arm64` 容器镜像。

```bash
tar -xzf astra-v0.1.0.tar.gz
cd astra-v0.1.0
./install.sh
```

默认只监听 `127.0.0.1:8080`，并使用无需密钥的 mock 模型。配置真实模型或向
网络开放服务前，请先阅读安装包中的安全说明。

## 第一条纵向切片

当前实现已经从最初的 Web 数据查询纵向切片，演进为由 Python 后端支撑的通用 Agent Runtime。Web 搜索与读取仍是可用的原子能力，但不再决定整体任务规划结构。

工具系统采用策略驱动的通用 Runtime：计划描述目标、依赖、预期结果和 provider-neutral 的任务语义能力，具体工具只在执行回合根据当前需求、可用 manifest、策略与安全约束动态选择。Web 领域处理从 AgentLoop 解耦，计算型工具通过 `ToolExecutionContext`、`SandboxJob` 和 `ArtifactRef` 保留审计关联。声明式 `chart.render` 支持 Matplotlib、Seaborn 和 ECharts；绘图能力默认关闭，启用后通过 Docker Engine 执行，不在 API 进程内执行任意 Python 或 JavaScript。本地、CI 和 Linux 部署共用相同镜像与安全参数。部署要求见 [沙箱与图表 Runtime 运维指南](docs/sandbox-and-chart-runtime-operations.md)。

核心闭环：

```text
用户目标 -> 创建 Task/Run -> 工具无关规划 -> 动态候选解析 -> 工具执行 -> 结果综合 -> 证据验证 -> 时间线报告
```

### Agent Profile、Memory 与运行能力

Astra 的稳定身份与治理原则由后端包中的 `IDENTITY.md`、`SOUL.md`、`MEMORY.md` 和后台专用的 `AUTODREAM.md` 统一定义并随 Git 发布。`AUTODREAM.md` 只有在显式绑定 consolidation job 的专用模型操作中才可加载，文档本身不会启用调度或修改记忆。每个新 Run 会冻结所用 Profile 的完整不可变快照，历史接口只暴露安全的版本与哈希元数据；服务重启或后续 Profile 升级不会让已有 Run 静默切换人格。

Profile 文档不保存实际用户记忆，也不授予工具权限。真实 run/workspace/user Memory 继续存入数据库；本次实际可执行能力由 Tool Manifest、环境配置、持久化工具开关、基础设施状态、Run 权限、风险门控和剩余预算共同决定。

设置界面也遵循这条边界：“Agent”编辑行为指令，“运行时”管理隔离环境和依赖，“记忆”管理强制运行设置、真实 Memory、AutoDream 整理与审计，“实验功能 → Agent 改进”管理不会自动进入生产的改进候选。

当前默认执行路径是通用 Agent loop：

```text
用户消息 -> PlanNode 需求 -> 动态工具候选 -> Agent 决策 -> PolicyGate
        -> ToolCall -> Observation -> Reflection/继续执行
        -> Evidence/Artifact -> Final Answer -> Verification Report
```

`PlanNode.required_capabilities` 表达诸如 `information.search`、`information.read`、`data.visualize` 的任务需求，不保存具体工具、provider、permission 或 backend 名称。`CapabilityToolResolver` 在每个执行回合从当前合规 manifest 中生成有序候选；多个工具实现同一语义能力时可以替换，候选失效不需要改写 Plan。历史上绑定具体工具名的 Plan 仅通过显式兼容模式执行，新 Plan、PlanPatch 和 Plan revision 都拒绝这种绑定。

任务语义能力与安全 authority 是两个维度：`task_capabilities` 只说明工具能完成什么；`capabilities`、`permissions`、risk、backend 和具体 Effect Plan 决定本次调用是否允许。模型选定具体工具后，`ToolRouter`、Effect Analyzer、Permission Engine、审批、预算和结果验证仍按原顺序执行。任何非候选或未授权选择都会写入审计事件而不会运行；节点声明的全部语义需求未满足时，CompletionGate 会拒绝提前完成。

可信模式和未来可选的 Deep Research 子系统共享这套能力目录、候选解析、安全执行与证据层。Deep Research 可以增加研究规划、补搜循环和来源覆盖策略，但普通可信运行不导入或强制依赖 Deep Research 模块。

### 本地开发

后端：

```bash
cd backend
cp .env.example .env
# 配置 DATABASE_URL；默认模型 provider 为 mock，可先不填 MODEL_API_KEY
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload
```

前端：

```bash
cd frontend
npm install
npm run dev
```

默认前端运行在 `http://localhost:5173`，通过 Vite proxy 访问 `http://localhost:8000/api`。

### Runtime 依赖构建与运维

“设置 → 运行时”只接受规范的 Python 包名和可选精确版本；URL、VCS、路径、
pip 参数以及 Matplotlib、NumPy 等基础镜像核心包会被拒绝。构建由后台 Job
执行，先生成唯一 staging image，通过 Matplotlib、Seaborn 与 ECharts smoke
test 后才原子激活内容寻址 custom image。运行中的图表任务继续使用旧 active
image，因此构建失败或取消不会破坏现有任务。

部署主机需要 Docker Engine，并仅向后端容器挂载 Docker socket。该权限等同于
宿主级 Docker 管理权限，只应授予受信任的本机 Astra 实例；不要把 API 直接暴露
到公网。离线环境应预先缓存基础镜像和依赖源，构建失败时可在 Runtime 页面查看
脱敏日志并重试。回滚只需重新构建上一组依赖；系统始终保护基础镜像、当前 active
image 和最近 3 个成功 image，并默认清理 30 天前的额外 inactive image。清理只
匹配 `astra-data-viz:build-*` 与 `astra-data-viz:custom-*`，不会执行全局 prune。

完整容器栈使用 `deploy/compose.yaml`。Release 安装包会创建持久化 data 目录、执行
Alembic migration，并通过 `/api/ready` 健康检查后启动同源 Nginx proxy。升级前备份
data 目录；若新 Runtime 构建异常，保留旧 active image、检查 Docker 容量与依赖源，
再取消或重试构建。

### 真实模型配置

`.env` 中默认使用 mock provider，便于本地确定性验证：

```text
MODEL_PROVIDER=mock
MODEL_NAME=mock-web-query
```

接入真实 OpenAI-compatible API 时，配置：

```text
MODEL_PROVIDER=openai
MODEL_NAME=<model-name>
MODEL_API_KEY=<your-api-key>
MODEL_BASE_URL=https://api.openai.com/v1
```

启动后端后，可运行端到端问答延迟基准。它会预热连接，测量提交、
SSE ready、首个回答增量（TTFT）和完成时延，并输出 p50/p95；默认会清理
基准创建的会话：

```bash
cd backend
python -m app.benchmarks.qa_latency --runs 20 --warmup 2
```

使用 `--answer-mode trusted` 测量可信执行路径，或使用 `--keep-runs` 保留
基准运行记录。请使用真实模型配置采集供应商端到端指标；内置 `mock`
模型固定演练工具流程，不适合作为云模型延迟替代值。

需要隔离供应商网络和推理波动时，可在另一个终端启动确定性的
OpenAI-compatible 流式模型桩：

```bash
cd backend
python -m app.benchmarks.model_stub \
  --first-token-delay-ms 20 \
  --inter-chunk-delay-ms 1 \
  --chunk-chars 3
```

将后端的 `MODEL_BASE_URL` 指向 `http://127.0.0.1:8999/v1`。使用
`--response-order legacy` 可重放旧版“先决策元数据、后回答”协议，与默认的
`summary-first` 协议进行相同环境下的 TTFT 对比。

### Web 搜索/抓取配置

每个已支持工具都有独立开关；关闭后工具不会注册，也不会出现在模型可用工具清单中：

```text
TOOL_WEB_SEARCH_ENABLED=true
TOOL_WEB_FETCH_ENABLED=true
TOOL_CHART_RENDER_ENABLED=true
```

`web_fetch` 支持直接抓取公开 HTTP(S) URL；`web_search` 默认使用自动 provider 模式：

```text
WEB_SEARCH_PROVIDER=auto
```

`auto` 按以下规则选择搜索路径：

1. 同时存在专用 `GOOGLE_SEARCH_API_KEY` 和 `GOOGLE_SEARCH_ENGINE_ID` 时使用 Google；
2. 否则存在 `WEB_SEARCH_API_KEY` 时使用 Brave；
3. 否则进入无密钥链路，先请求 Bing RSS，失败或无结果时回退 DuckDuckGo HTML。

自动搜索输出会记录实际 provider、每次 provider 尝试、回退原因和 `degraded` 状态。Bing RSS 与 DuckDuckGo HTML 属于公共搜索入口，可能受到结构变化、限流和使用条款约束；无密钥模式定位为本地开发和个人部署的降级能力，不承诺商业生产环境的可用性、许可或结果质量 SLA。

显式配置 `google`、`brave`、`bing` 或 `duckduckgo` 时，系统只执行指定 provider，不会静默回退。这样可以保证部署配置与 ToolCall 审计结果一致。

如需接入 Google Programmable Search JSON API，配置：

```text
WEB_SEARCH_PROVIDER=google
GOOGLE_SEARCH_API_KEY=<your-google-api-key>
GOOGLE_SEARCH_ENGINE_ID=<your-programmable-search-engine-id>
GOOGLE_SEARCH_RESULT_COUNT=5
GOOGLE_SEARCH_LANGUAGE=lang_zh-CN
GOOGLE_SEARCH_REGION=
GOOGLE_SEARCH_SAFE=active
```

Google API Key 只用于后端工具调用，不会写入 ToolCall input/output。`auto` 只有在专用 Google Key 与 Search Engine ID 都存在时才自动选择 Google；显式 `WEB_SEARCH_PROVIDER=google` 继续兼容用 `WEB_SEARCH_API_KEY` 提供 Google Key 的旧配置。`web_fetch` 会按候选来源动态选择抓取策略，并将正文长度、质量评分、抓取 warning 和失败来源写入 Evidence Pack；最终总结只基于本次 run 中已审计的工具输出和 artifact。

接入 Brave Search 时配置：

```text
WEB_SEARCH_PROVIDER=brave
WEB_SEARCH_API_KEY=<your-search-api-key>
```

网络读取可以通过 `ALLOW_NETWORK_READ=false` 关闭。

抓取内容上限和低质量阈值可通过以下配置调整：

```text
CRAWLER_MAX_CONTENT_CHARS=12000
CRAWLER_MIN_QUALITY_CHARS=240
```

### Web 原子检索与证据共享层

`web_search` 继续兼容单个 `query`，也可以通过 `queries` 一次提交 1–4 个带独立目的的逻辑查询。语言、地区、结果数、时间范围、包含/排除域名和内容类型会被规范化；工具输出会区分 provider 已应用、查询语法模拟、结果后过滤和不支持的约束，避免把未生效的过滤条件误报为成功。

`web_fetch` 保留原工具名和有界 `content` 字段，同时输出规范 URL、不可变 source/snapshot ID、内容摘要、稳定 passage、页面链接、抽取信号和截断信息。搜索摘要只属于 `candidate_only` 候选证据，不能单独支撑关键声明；成功读取的 passage 才能绑定 claim 与 citation。

可信运行会把搜索轨迹、候选、快照和 passage 追加到 run 级 Evidence Ledger，并保留 Plan node、NodeExecution、ToolCall 与 Artifact 谱系。重复写入相同证据是幂等的，同一证据键出现不同内容会拒绝写入。`find_passages` 与 `open_passage` 是 Evidence Ledger 上的本地操作，不会再次访问网络，也不会让无状态 Web Runtime 直接访问数据库。历史结果缺少 `claims`、`citations` 或 grounding 审计字段时会按空列表/空引用兼容读取。

这一层是可信模式与未来 Deep Research 的共享基础，但本阶段不包含 `/deep-research`、研究规划器、补搜循环、来源数量/多样性策略或长报告 UI。普通可信运行不会选择或导入 Deep Research；只有本次运行实际存在 Web 证据或任务契约显式要求时才启用 grounding 校验。未来 Deep Research 应作为可选工作流模块复用该层，并通过自己的冻结验证配置增加覆盖率、新鲜度、多样性和冲突策略。

### Agent loop 配置

Agent loop 默认使用通用 Runtime：

```text
AGENT_MAX_TURNS=12
AGENT_MAX_TOOL_CALLS=8
AGENT_PARALLEL_EXECUTION_ENABLED=true
AGENT_MAX_PARALLEL_NODES=3
AGENT_MEMORY_WRITE_ENABLED=true
AGENT_MEMORY_CROSS_SESSION_ENABLED=false
AGENT_MEMORY_CROSS_SESSION_SHADOW=false
AGENT_MEMORY_AUTODREAM_ENABLED=false
AGENT_USE_GENERAL_RUNTIME=true
```

可信 DAG 默认最多并行执行 3 个无依赖、无资源冲突的只读幂等节点。每个节点使用独立数据库 session 和持久化 `NodeExecution` attempt；并发槽、provider/capability 上限、预算预留、资源租约、审批与 CompletionGate 共同形成背压。未知资源和非幂等副作用保持串行，超时也只自动重试已证明安全且幂等的行动。

紧急回滚时设置 `AGENT_PARALLEL_EXECUTION_ENABLED=false`，运行时会退回单槽顺序，已持久化 execution、审批、租约和历史图谱仍可读取。恢复器通过 heartbeat 查找过期 attempt：有已记录结果的幂等行动直接提交，未启动行动的 attempt 可恢复，结果未知的非幂等行动进入等待/阻塞而不会盲目重放。

可观测事件 `plan.parallel_execution.completed` 记录请求/实际并发度、完成/失败/恢复数量和耗时；图谱快照同时公开已清洗的活动数、槽位、等待阶段与租约摘要，不公开原始资源路径或工具输入。

### 通用推理与反思内核

每个新 Run 会冻结用户请求的推理策略，并由后端编译出受安全下限约束的生效策略。快速响应直接进入无 DAG 的轻量 AgentLoop；可信执行始终先生成完整 DAG，并可选择生成后直接执行或等待用户确认。快速/均衡/深入会改变计划深度、模型调用、反思、重规划和验证预算，但不会关闭权限门控、基础错误恢复或完成验证。

```text
PolicyCompiler → TaskContract → PlanGraph → Decision
  → PolicyGate → Tool → Observation → Evaluation
  → AgentState → ReflectionGate → CompletionGate
```

模型只能提出结构化行动和终止意图；运行时固定节点顺序，不能跳过权限、观察评估或完成闸门。反思分为局部、计划和目标层级，只有能够产生合法 `ReflectionPatch` 的反思才会改变状态。同一失败策略由 fingerprint 限制重试，无进展时停止继续消耗预算。

终态语义：

- `completed`：强制成功准则和任务验证全部通过。
- `completed_with_warnings`：允许交付，但存在明确的非关键缺口。
- `waiting_user`：需要澄清或批准，可使用同一个 Run 恢复。
- `blocked`：目标已理解，但安全、能力或预算内没有可行策略。
- `failed`：内部或基础设施错误导致无法受控继续。

Web 搜索通过 `WebTaskAdapter` 接入通用完成语义。将 `AGENT_USE_GENERAL_RUNTIME=false` 可回退到旧 Web 编排路径；新增审计字段保持只读兼容。外部行动在执行前保存 turn phase 和稳定幂等键，结果未知的非幂等行动不会被自动重试。

### 错误响应

所有 API 失败均返回安全的错误信封：`error.type`、稳定的 `error.code`、用户可见的 `error.message`、`error.retryable` 和 `error.trace_id`。验证错误使用 422，资源不存在使用 404，状态冲突使用 409，依赖或数据库不可用使用 503，未分类运行时错误使用 500。响应不会包含堆栈、连接字符串或密钥；请使用 trace ID 在服务端日志中定位技术问题。后台 Run 失败也在 `result.error` 与 `run.error` 事件中使用相同字段。

Memory 已支持 Run、Task、Workspace 和 user 显式命名空间、类型化生命周期、不可变版本、跨 Session 确定性召回、召回反馈与来源删除传播。跨 Session 注入和 AutoDream 调度默认关闭，可先用 shadow 模式采集审计指标；后台 consolidation 只生成可审查、可回滚的版本。Agent evolution candidate 可以附加离线评估并被人工批准，但生产晋升和执行保持故障闭合。首版不依赖 embedding、向量库或图数据库，完整配置与回滚说明见[深度记忆、AutoDream 与 Agent 自进化运维](docs/deep-memory-autodream-evolution.md)。

前端现在是聊天式 Agent 窗口：用户消息、工具调用、反思、来源卡片、memory 摘要和最终答案会聚合成对话流；“审计详情”抽屉保留 turns、tool calls、artifacts、Evidence Pack 与 verification report，便于调试和追溯。

历史对话默认永久保存，侧边栏的 100 条上限只是显示上限。部署方可以显式启用有保护条件、批量上限和审计日志的后台老化机制；详见[历史对话老化运维](docs/conversation-retention-operations.md)。
