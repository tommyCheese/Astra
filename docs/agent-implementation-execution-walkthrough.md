# 跟着一次请求读懂 Astra Agent：从启动、思考、行动到交付

这篇文章只做一件事：跟随一个用户请求，按照代码真正发生的先后顺序，走完整个 Astra 后台。

假设用户在页面中输入：

> 帮我查一下 Astra 最近一次发布的主要变化，并给出来源。

我们不先讲“架构分层”，也不把模型、数据库、工具拆成互不相干的知识点。我们从进程启动的第一行开始，看这个请求怎样进入系统、怎样变成数据库记录、怎样让模型做决定、怎样调用 Web 工具、怎样形成证据，最后怎样回到用户界面。沿途遇到哪个模块、类或数据结构，就在它真正出现的位置解释它。

---

## 第一程：用户请求到来之前，服务先准备好运行环境

开发者通常先进入 `backend/`，按照 `pyproject.toml` 安装项目。这个文件规定 Python 版本、FastAPI、Pydantic、SQLAlchemy、Alembic、httpx、uvicorn 等运行依赖，也声明 pytest、pytest-asyncio、aiosqlite 和 Ruff 等开发依赖。setuptools 会把 `app*` 识别为 Python 包，因此 `app/__init__.py` 以及 `api`、`core`、`db`、`models`、`repositories`、`runner`、`schemas`、`tools` 下的 `__init__.py` 都是包边界标识；这些文件本身几乎不执行业务逻辑。

### 1. 数据库先通过 Alembic 成形

执行 `alembic upgrade head` 时，首先读取 `alembic.ini`。它只定义 migration 位置和日志，不保存真正的数据库地址。随后 `alembic/env.py` 导入 `get_settings()` 和 ORM 的 `Base`：

1. `get_url()` 从应用配置取得数据库 URL。
2. offline 模式由 `run_migrations_offline()` 生成 SQL。
3. online 模式由 `run_migrations_online()` 启动异步流程，`run_async_migrations()` 建立连接，`do_run_migrations()` 在同步桥接中执行 migration。

三个 migration 严格按顺序执行：

1. `0001_initial_run_model.py` 建立 Task、Run、Step、ToolCall、Artifact、RunEvent 六类基础记录。
2. `0002_agent_turns_memories.py` 加入 AgentTurn 和 Memory，系统从“固定任务流水线”演进为“逐轮可审计 Agent”。
3. `0003_general_reasoning_core.py` 给 Run 加入推理策略、任务契约、计划图、版本化状态、等待态和终止原因；同时给 AgentTurn 加入评估、反思补丁、版本、执行阶段和幂等键。

这些表在 `app/db/models.py` 中有对应 ORM 类。`Base` 是所有表的声明基类；`uuid_str()` 生成主键，`utc_now()` 生成 UTC 时间；`JsonType` 在 SQLite 使用 JSON，在 PostgreSQL 自动切换为 JSONB。

此时八个记录类已经准备好，但还没有任何用户数据：

- `TaskRecord` 是连续会话或稳定目标的容器。
- `RunRecord` 是一次具体执行，保存状态、模型配置、契约、计划、运行状态和最终结果。
- `StepRecord` 是计划中的一个步骤。
- `ToolCallRecord` 审计一次真实工具调用。
- `ArtifactRecord` 保存 Evidence Pack 或 Final Answer 等产物引用。
- `RunEventRecord` 是推送给前端的追加式时间线。
- `AgentTurnRecord` 保存一轮决策、观察、评估、反思和执行阶段。
- `MemoryRecord` 保存带来源与置信度的记忆。

`app/db/base.py` 只是把 `Base` 重导出给 Alembic。`app/db/session.py` 则在模块加载时，用配置创建 async engine 和 `SessionLocal`；HTTP 请求需要数据库时，`get_session()` 提供一段生命周期受控的 `AsyncSession`。

### 2. Settings 在所有组件之前被读取

uvicorn 导入 `app/main.py` 时，文件底部执行 `app = create_app()`。`create_app()` 首先调用 `app/core/config.py` 的 `get_settings()`。这个函数用 `lru_cache` 保证进程内只构造一次 `Settings`。

`Settings` 是整套实现的运行开关，依次为后续组件提供：日志级别、数据库 URL、模型 provider/name/key/base URL、Google 或 Brave 搜索配置、正文长度与质量阈值、Agent turn/tool/reflection 预算、Memory 开关、通用 Loop 开关、网络读取权限和 CORS。`cors_origin_list` 把逗号字符串变成列表；`model_policy` 生成允许写入 Run 的非密钥模型摘要。

### 3. FastAPI 应用最后完成装配

回到 `app/main.py`，`create_app()` 继续完成：

1. 初始化日志。
2. 创建 FastAPI。
3. 注册 CORS。
4. 挂载 `app/api/runs.py` 的 router。
5. 注册请求耗时 middleware。
6. 注册领域、参数、数据库和未知异常处理器。
7. 注册 `/api/health`。

异常处理器依赖 `app/core/errors.py`。正常错误数据用 `ErrorPayload` 表示，外层由 `ErrorEnvelope` 包装。`AstraError` 是可直接映射为 HTTP 响应的父异常；`ValidationError`、`ResourceError`、`StateError`、`InfrastructureError` 分别对应 422、404、409、503。`internal_error()` 给未知异常分配 trace ID 并隐藏技术细节；`run_error_from_exception()` 进一步识别模型配置错误、模型输出错误、httpx 网络错误、工具错误、SQLAlchemy/OS 错误，使后台任务也能使用同一安全错误协议。

到这里，服务才真正具备接收用户请求的条件。

---

## 第二程：用户点击发送，API 创建一条可追踪的 Run

前端调用 `POST /api/runs`，请求体首先由 `app/schemas/agent.py` 的 `CreateRunRequest` 解析。它包含 `goal`、可选 `task_id`、`reasoning_policy` 和可选单次模型配置。这里首次出现的 `RequestedReasoningPolicy` 又使用五组枚举：

- `ReasoningEffort`：fast、balanced、deep。
- `PlanningStrategy`：direct、adaptive、plan_first。
- `ReflectionTrigger`：failure_only、adaptive、every_turn。
- `ExecutionMode`：plan_only、request_approval、auto_approval。
- `VerificationLevel`：basic、standard、strict。

它们不是展示标签，而会决定后续预算和执行边界。

### 4. create_run 先校验请求，再冻结推理策略

`app/api/runs.py` 的 `create_run()` 按以下顺序执行：

1. 去掉 goal 首尾空格，空目标抛出 `ValidationError`。
2. 如果用户临时指定模型，校验 provider、模型名、API Key，并从全局 Settings 复制出只属于这次 Run 的 settings。
3. 调用 `app/runner/reasoning.py` 的 `PolicyCompiler.compile()`。

`PolicyCompiler` 先按 ReasoningEffort 选择 `RunBudgets`，其中包括最大计划深度、候选策略、模型调用、反思、重规划、turn、tool call 和验证覆盖数。然后施加安全下限：高风险任务必须 plan-first、请求批准、严格验证；高复杂度任务不能只做 direct planning。如果用户请求被提升，产生 `PolicyAdjustment`。最终用 `EffectiveReasoningPolicy` 和原始策略共同组成 `ReasoningPolicySnapshot`。这一步的意义是：Run 一旦创建，执行策略就被快照化，后续不会因为全局配置变化而悄悄改变。

### 5. Repository 创建 Task、Run 和第一条事件

API 构造 `RunRepository(session)`，调用 `create_task_run()`：

1. 有 `task_id` 就加载旧 Task，没有就新建 `TaskRecord`。
2. 创建 `RunRecord`，将 goal 放入 `model_policy.conversation_goal`，但不写 API Key。
3. flush 取得 ID。
4. 追加 `RunEventRecord(type="run.created")`。
5. commit。

`RunRepository` 是整个运行期间唯一的数据访问门面。后面 Step、ToolCall、Artifact、AgentTurn、Memory、Event 和状态版本更新都通过它完成。

策略编译产生的每个 `PolicyAdjustment` 也会被写成 `reasoning.policy_adjusted` 事件。API 随后用 `asyncio.create_task(start_run_in_process(...))` 启动后台执行，立即向调用方返回 `CreateRunResponse(task_id, run_id, status)`。

这是一个关键时间点：用户已经拿到 Run ID，但真正的 Agent 才刚刚开始工作。

---

## 第三程：RunEngine 接管 Run，先理解任务，再决定走哪条路

`start_run_in_process()` 位于 `app/runner/engine.py`。它先构造 `RunEngine`。构造函数做两件依赖注入：

1. `build_model_client(settings)` 选择模型客户端。
2. `build_web_registry(settings)` 构造工具注册表。

模型抽象定义在 `app/runner/model_client.py`。`ModelClient` 要求所有实现提供 `contract()`、`plan()`、`synthesize()`、`decide()`、`reflect()`、`finalize()` 和 `extract_memory_candidates()`。provider 为 mock 时使用 `MockModelClient`，它给测试和本地开发提供确定性输出；其他 provider 使用 `OpenAICompatibleModelClient`。缺少真实模型配置时抛出 `ModelConfigurationError`，结构无法解析时抛出 `ModelOutputError`。

工具抽象定义在 `app/tools/base.py`。每个工具都有 `ToolSpec`，声明名称、版本、输入/输出 schema、权限、副作用、超时、重试、错误类型和幂等性；抽象类 `Tool` 只要求实现 async `run()`；`ToolRegistry` 保存工具实例；`ToolExecutionError` 用稳定 category 表示工具失败。

### 6. Engine 打开独立数据库会话并恢复会话上下文

`RunEngine.run()` 使用 `SessionLocal` 打开后台专用 session，构造新的 `RunRepository`，再调用 `_run_with_repo()`。

它先加载当前 Run，并查询同一 Task 的历史 Run。最多取最近六次，将以前的 `conversation_goal` 与 summary 拼成 Conversation context，再附上本次请求。于是模型看到的 goal 可能不是一句孤立问题，而是同一 Task 的短期对话历史。

如果 Run 已有 `state_version` 和 `agent_state`，说明它可能从 `waiting_user` 恢复而来。Engine 会跳过重复规划，直接回到 executing，并重新进入 AgentLoop。这就是 Run 可续跑的第一层实现。

### 7. Engine 先生成 TaskContract

新 Run 被更新为 planning。启用 general runtime 时，Engine 调用 `model_client.contract(goal)`。

真实模型返回的数据被解析为 `TaskContract`。它包含：

- `deliverables`：要交付什么。
- `constraints` 和 `prohibited_actions`：不能越过什么边界。
- `TaskAssumption`：假设、置信度、来源和有效性。
- `SuccessCriterion`：每个成功准则的稳定 ID、验证方法、是否强制和 `CriterionStatus`。
- `VerificationRequirement`：必须调用哪类 validator。
- 风险和歧义状态。

`normalize_contract()` 补齐模型遗漏，但不会削弱契约；`validate_contract()` 要求 goal、交付物、成功准则和验证方法完整且 ID 唯一。模型输出不可用时，`build_default_contract()` 生成最低可用契约。

这里用到的 `CriterionStatus` 有 pending、satisfied、failed、waived。它会在后面的 Evaluation 和 CompletionGate 中真正决定 Run 能不能宣布成功。

### 8. Engine 再生成 PlanOutput 和 PlanGraph

接着调用 `model_client.plan(goal)`，得到 `PlanOutput`。其中每个 `PlanStep` 描述标题、意图、所需工具和成功标准；`required_tools` 汇总整份计划；`risk_level` 提供计划级风险。旧的 `ToolDecision` schema 仍保留为简单工具选择结构，但当前主循环使用更丰富的 `AgentDecision`。

Engine 用 `_persist_plan()` 把每个 PlanStep 创建为 `StepRecord`。随后 `build_plan_graph()` 将线性输出编译成 `PlanGraph`：每个 `PlanGraphStep` 有稳定 step ID、依赖、能力、成功准则引用、风险、证据引用，以及一个 `ExpectedObservation`。`PlanGraph.ready_steps()` 可以根据已完成依赖找出可运行步骤。

Engine 再构造 `AgentState`，其中放入 TaskContract、PlanGraph、policy version、`AcceptedFact`、观察、评估、失败记录和预算用量。`FailureFingerprint` schema 描述某类失败策略的次数与是否耗尽。Repository 的 `initialize_reasoning_state()` 将 contract、graph、state 与 state version 一起落库。

如果契约仍有歧义，Engine 不会猜测，而是调用 `set_waiting_state()` 进入 waiting_user；如果 execution mode 是 plan_only，则完成步骤并直接返回计划。只有清晰且允许执行的任务才继续。

---

## 第四程：AgentLoop 开始逐轮做决定，这才是 Agent 的核心

默认配置下，Engine 把 Run 更新为 executing，并构造 `app/runner/agent_loop.py` 的 `AgentLoop`。构造时同时准备四个确定性组件：

- `ToolRouter`：工具安全门。
- `WebTaskAdapter`：Web 领域适配器。
- `ObservationEvaluator`：观察评估器。
- `ReflectionGate` 与 `CompletionGate`：反思和完成闸门。

`WebTaskAdapter` 继承 `app/runner/adapters.py` 的抽象 `TaskAdapter`。TaskAdapter 要求领域实现负责工具结果规范化和结果验证。Web 实现允许的工具只有 web_search、web_fetch，还负责 URL 过滤、canonical URL、Evidence Pack 构造以及 Web 结果是否具备足够证据的判断。

### 9. 每一轮都先重新组装 Context

AgentLoop 创建 `ContextAssembler`、`MemoryManager` 和 `VerificationEngine`，然后从第一轮开始循环，最大轮数由 Settings 控制。

每轮开头，`ContextAssembler.assemble()` 都重新读取：

1. Run 内最近的 Memory。
2. 所有工具的 ToolSpec manifest。
3. 累积 Observation。
4. 当前 Evidence Pack。
5. reasoning policy、TaskContract、PlanGraph 和 AgentState。

这些数据组成模型能看到的上下文。数据库 Memory 与 API schema 中的 `MemoryRecord` 名字相同但职责不同：前者是 ORM，后者是模型输出/运行时传输结构；后者包含 scope、kind、content、structured_data、provenance、confidence 和过期时间。

### 10. 模型返回 AgentDecision

`model_client.decide()` 返回 `AgentDecision`，它不包含隐藏思维链，而是可审计字段：decision_type、简短 reasoning summary、工具名与输入、预期观察、停止条件、目标步骤、成功准则引用、风险、置信度和 fallback。

真实客户端 `OpenAICompatibleModelClient._chat_json()` 调用 `{base_url}/chat/completions`，要求 `response_format=json_object`，并读取 SSE token。`parse_json_object()` 能处理 code fence 和前导文本；`normalize_contract_payload()`、`normalize_plan_payload()`、`normalize_final_answer_payload()` 容忍部分模型简写；`normalize_goal_text()` 防止模型把包装后的 conversation context 错当作原始目标。

Mock 客户端则按已观察到的工具结果确定下一步：通常先 web_search，再逐个 web_fetch，最后 finalize。这让测试能够稳定复现完整 Agent 行为。

### 11. 每个决定先创建 AgentTurnRecord

无论决定是什么，Loop 都先调用 Repository 的 `create_agent_turn()`。运行时 schema `AgentTurn` 描述相同概念，而数据库使用 `AgentTurnRecord`。如果是工具调用，还会根据 run ID、turn index、工具名和输入生成 SHA-256 `idempotency_key`，把 phase 标为 prepared。

随后分支才开始：

- `finalize`：标记 turn 完成，退出循环。
- `ask_user`：写 observation，进入 waiting_user，退出循环。
- `blocked`：记录原因并退出循环。
- `reflect` 或 `replan` 等非工具决定：作为普通 observation 留痕，进入下一轮。
- `call_tool`：进入真正行动阶段。

模型决策无法解析时，不会直接崩溃。Loop 创建 `AgentObservation(kind="model_error")`，调用 `model_client.reflect()` 得到 `AgentReflection`，创建 reflect turn 和 `reflection.created` 事件，然后尝试下一轮。

### 12. ToolRouter 在行动前执行不可绕过的门控

对 `call_tool`，Loop 先检查全局工具调用预算和等价失败次数，然后让 `ToolRouter.resolve()` 依次检查：

1. 决策是否包含工具名。
2. 工具是否位于当前 adapter allowlist。
3. 工具是否已注册。
4. ToolSpec 的必填输入是否齐全。
5. permission 是否为 network_read。
6. side_effect_level 是否为 read_only。

这些字符串在 `app/models/domain.py` 中分别由 `Permission` 与 `SideEffectLevel` 枚举表达；`RunStatus`、`StepStatus`、`ToolCallStatus` 则描述持久化生命周期。当前代码实际还使用 waiting_user，虽然 `RunStatus` 枚举尚未纳入它。

只有通过门控，Loop 才找到对应 Step，把它更新为 running，创建 `ToolCallRecord(status="running")`，并把 AgentTurn phase 从 prepared 改为 executing。

---

## 第五程：Web 工具真正访问外部世界

工具实现在 `app/tools/web.py`。`build_web_registry()` 按顺序注册 `WebSearchTool` 和 `WebFetchTool`。

### 13. 第一次行动通常是 WebSearchTool

`WebSearchTool.run()` 先校验 query，再根据 Settings 选择 provider：

- Google 路径由 `_google_search()` 调用 Programmable Search JSON API；`normalize_google_items()` 把响应转成统一候选结构，同时不保留密钥。
- Brave 路径由 `_brave_search()` 调用 Brave Search API。
- provider 或凭据不合法时抛出 `ToolExecutionError`。

每个候选在 schema 中可由 `CandidateSource` 表示：URL、标题、摘要、provider、rank、展示域名、metadata 和 retrieved time。

搜索完成后，ToolCall 被标记 succeeded。`WebTaskAdapter.filter_candidates()` 立即过滤非法协议、二进制资源和重复 URL，去掉 utm/fbclid/gclid 等跟踪参数，并把去重统计写入 Step evidence。工具原始输出被规范化为 `AgentObservation(kind="tool_result")`。

### 14. ObservationEvaluator 判断结果是否符合预期

`ObservationEvaluator.evaluate()` 将当前 `AgentObservation` 与决策中的 `ExpectedObservation` 比较，生成 `Evaluation`。`EvaluationOutcome` 可能是 matched、partial、mismatch、conflict 或 inconclusive。匹配时，decision 引用的成功准则会得到 satisfied 更新建议。

Evaluation 被写入 AgentTurn 和 `reasoning.evaluation_created` 事件。注意：这一步不是让模型自称“成功”，而是确定性代码根据 observation 类型、状态和必填字段做判断。

### 15. MemoryManager 在每次有效观察后尝试写记忆

`MemoryManager.write_candidates()` 调用模型的 `extract_memory_candidates()`。模型返回运行时 `MemoryRecord` 列表，Repository 再创建 ORM `MemoryRecord`。每条记忆带 provenance 和 confidence；workspace/user 长期记忆缺少这些字段会被拒绝并产生 `memory.write_rejected`。当前主路径主要按 run_id 读取，所以它更接近可审计的 Run 内记忆，还不是语义向量记忆系统。

### 16. 下一轮通常执行 WebFetchTool

模型在下一轮看到搜索候选后，选择某个 URL 调用 web_fetch。`WebFetchTool.run()` 先检查 `allow_network_read` 和 URL，然后用 httpx 获取页面。抓取计划用 `CrawlerPlan` 表示，`validate_crawler_plan()` 只接受有限策略和安全 selector。

HTML 交给 `ContentExtractor`。它继承 Python `HTMLParser`，按开始标签、结束标签和文本事件收集：title、meta、main/article/section/p 等候选正文元素，并跳过 script/style/noscript。之后执行：

1. `choose_strategy()` 选择 readability、selector_extract、metadata_first 等策略。
2. `extract_content_by_strategy()` 提取正文。
3. `select_text()` 执行受限 tag/class/id selector。
4. 无正文时退回 description 或搜索 snippet。
5. `quality_warnings()` 检查正文长度和查询词重叠。
6. `build_fetch_output()` 计算质量分并推断 source type。

结果可由 `ExtractedSource` 表示；较旧、较简化的 `FetchOutput` schema 仍保留给只含 URL、状态、标题、正文和 metadata 的输出。最终多个候选与抓取结果会组成 `EvidencePack`。

抓取成功时，Loop 把来源加入 fetched_sources，更新 Step evidence，再重复 Observation → Evaluation → Memory 的过程。抓取失败时则进入另一条严格顺序：

1. ToolCall 标记 failed。
2. 计算 `failure_fingerprint()`。
3. 创建失败 `AgentObservation`。
4. 调用模型生成 `AgentReflection`。
5. 如果 reflection 带 `ReflectionPatch`，记录 patch。
6. 更新 AgentTurn 为 failed。
7. 写 `reflection.created` 和 `reasoning.failure_fingerprinted`。
8. 未耗尽预算则进入下一轮，耗尽则 blocked。

`ReflectionPatch` 可以修订工具输入、使假设失效、增加 AcceptedFact、更新 criterion、替换更高版本计划、增加验证要求或提出终止意图。`apply_reflection_patch()` 要求 state version 一致且 patch 确实 actionable，否则抛出 `StateVersionConflict` 或 ValueError。`ReflectionGate` 根据策略、触发信号和已用预算决定是否允许反思。当前 AgentLoop 已使用失败 fingerprint 和反思数据，但 `apply_reflection_patch` 的全部能力尚未贯穿每一个主循环分支。

---

## 第六程：循环停止后，系统才开始形成最终交付物

循环会因为 finalize、ask_user、blocked、重试耗尽、tool budget 或 turn budget 停止。停止不等于成功；接下来还有证据、答案和完成闸门。

### 17. 先构造 Evidence Pack Artifact

`WebTaskAdapter.build_evidence()` 汇总 query、去重候选、成功抓取、失败来源和 warning。Loop 再补上是否尝试过外部证据，并调用 Repository 的 `create_artifact(type="evidence_pack")`。Artifact 的 JSON 写入 `content_ref`，metadata 保存审计来源数和失败数。

这里涉及的证据 schema 按形成顺序是：搜索时的 `CandidateSource`，抓取计划 `CrawlerPlan`，成功抓取 `ExtractedSource`/`FetchOutput`，最后聚合为 `EvidencePack`。

### 18. 模型生成 FinalAnswer

如果 Loop 已确定 blocked/waiting 等覆盖状态，它生成一个只解释运行状态的 `FinalAnswer`；否则调用 `model_client.finalize()`。真实客户端最终复用 synthesize，把 evidence pack 发给模型；Mock 客户端从抓取正文构造 findings 和 source references。

`FinalAnswer` 内部使用：

- `Finding`：一条结论及支撑它的 URL。
- `SourceReference`：来源 URL、标题和抓取时间。
- failed_sources、source_quality、conflicts、caveats。
- verification_notes、memory_references 和 audit_refs。

这时 MemoryManager 再运行一次，把整次 Run 中值得保留的来源摘要写入 Memory。

### 19. VerificationEngine 生成展示型报告

`VerificationEngine.verify()` 检查：是否尝试外部证据、是否抓取成功、是否低质量、是否有失败来源、FinalAnswer 是否引用来源。输出 `VerificationReport`，包含 status、source/caveat 数量、低质量来源、失败来源、记忆引用和 notes。

### 20. WebTaskAdapter 给出领域完成判断

`WebTaskAdapter.validate()` 再从任务领域角度判断：

- 普通知识回答且根本不需要外部证据，可以 completed。
- 需要 Web 证据但没有已审计来源，blocked。
- 有证据但存在失败或低质量，completed_with_warnings。
- 来源充分，completed。

它返回 `CompletionDecision`，其中 state 使用 `TerminalState`：continue、completed、completed_with_warnings、waiting_user、blocked、failed。

### 21. CompletionGate 作最后一次不可跳过的判定

Loop 从数据库重新读取 `AgentState`。领域验证通过时，它先把强制 SuccessCriterion 标为 satisfied；然后 `CompletionGate.evaluate()` 同时检查：

1. 是否有运行时错误。
2. 是否仍需要用户输入。
3. 所有 mandatory criterion 是否 satisfied。
4. task adapter validator 是否通过。
5. 是否存在 warning。

只有契约和 validator 同时允许，状态才会是 completed。这个设计阻止模型仅凭一句“我完成了”自行终止。

Loop 把 FinalAnswer、VerificationReport、Evidence Pack、memory writes 和终态组合成结果返回 Engine。若有最终 turn，还会补写 artifact ID、memory writes 和状态。

---

## 第七程：Engine 收尾，Repository 把结果变成用户可见的时间线

AgentLoop 返回后，RunEngine 仍按顺序做三段收尾：

1. 将 Run 更新为 synthesizing，创建 `final_answer` Artifact，完成“综合答案”Step。
2. `_emit_answer_stream()` 先写 `answer.started`，再按每 12 个字符写多个 `answer.delta`，最后写 `answer.completed`。
3. 将 Run 更新为 verifying，完成“验证证据”Step，再把遗留 pending/running Step 标记完成，最终写 completed、completed_with_warnings、blocked 或其他状态。

`RunRepository.update_run_status()` 同时维护 started_at、completed_at、summary、result，并总是追加 `run.status_changed`。

如果关闭通用 Agent Loop，Engine 会走 `_execute_web_query()` 旧兼容路径：固定执行搜索、过滤、逐个抓取、Evidence Pack、synthesize 和 `_verify()`。这条路径复用相同的 ToolRegistry、Repository 和 Artifact，但没有逐轮模型决策。它存在的目的是回退，不是默认主设计。

---

## 第八程：前端怎样看到正在发生的一切

用户拿到 Run ID 后通常同时使用查询和事件接口。

### 22. SSE 按事件 ID 增量输出

`GET /api/runs/{run_id}/events` 进入 `stream_run_events()`。它先确认 Run 存在，然后用新的 `SessionLocal` 每 250ms 调用 `RunRepository.list_events(run_id, after_id)`。每条 `RunEventRecord` 变成 SSE data；客户端重连时可用 after_id 从上次位置继续。Run 进入终态且没有新事件后，发送最后 heartbeat 并关闭。

### 23. Run 快照被转换成一棵完整 View

`GET /api/runs/{run_id}` 调用 `RunRepository.get_run()`，用 selectinload 一次加载 Task、Steps、ToolCalls、Artifacts、Events、Turns 和 Memories。`run_to_view()` 把 ORM 转成 `RunView`。

这时 `app/schemas/agent.py` 中剩余的展示类按嵌套顺序全部登场：

1. `StepView` 展示每个步骤和 evidence。
2. `ToolCallView` 展示工具审计。
3. `ArtifactView` 展示产物。
4. `RunEventView` 展示时间线。
5. `AgentTurnView` 展示决策、观察、反思、评估、版本和幂等阶段。
6. `MemoryView` 展示记忆及来源。
7. `ChatMessageView` 把后台审计投影成用户、assistant、tool、reflection 消息。
8. `RunView` 聚合以上所有内容，以及 result、VerificationReport、reasoning policy、contract、plan graph、agent state 和 waiting state。

`build_chat_messages()` 负责这次投影：第一条总是用户 goal；call_tool turn 显示为 tool；reflect turn 显示为 reflection；finalize turn 显示为 assistant；如果没有 finalize turn，则根据终态结果补一条 assistant 消息；waiting_user 则追加澄清问题。

列表接口 `GET /api/runs` 使用同一套 RunView，只是按 created_at 倒序并限制数量。

---

## 第九程：如果 Agent 需要用户批准或澄清，它怎样继续原来的 Run

当决策是 ask_user，或 TaskContract 存在歧义时，Repository 的 `set_waiting_state()` 会：生成 continuation token、保存暂停节点和请求、把状态设为 waiting_user，并追加 `run.waiting_user`。

用户回复后调用 `POST /api/runs/{id}/resume`。请求由 `ContinueRunRequest` 校验，包含 content、可选 approved 和 continuation token。API 把回复规范化成 user_response 或 approval_result observation，然后调用 `resume_waiting_run()`：

1. 确认 Run 真在等待。
2. 校验 token。
3. 把用户回复追加到 AgentState observations。
4. 清除契约歧义。
5. 增加 state version。
6. 清空 waiting_state，恢复 executing。
7. 追加 `run.resumed`。
8. 再次 `asyncio.create_task(start_run_in_process(...))`。

Engine 看到已有 state_version 后跳过旧步骤，AgentLoop 从保存的 observations 继续。这解释了“恢复”不是创建新 Run，而是在同一个审计链上继续。

---

## 第十程：失败怎样被收口，而不是泄露成一段堆栈

失败可能出现在三个时间点。

第一，HTTP 请求阶段。FastAPI 的异常处理器将 RequestValidationError、AstraError、SQLAlchemyError 或未知 Exception 转成 ErrorEnvelope。

第二，Engine 初始化阶段。真实模型缺少凭据会触发 ModelConfigurationError，`start_run_in_process()` 把 Run 标为 blocked，并写 `run.error`。

第三，后台执行阶段。`RunEngine.run()` 捕获模型配置/输出/httpx 错误并标记 blocked；其他异常标记 failed。`error_result()` 生成稳定的空结果骨架。工具错误则尽可能在 AgentLoop 内成为 Observation 和 Reflection，而不是直接让整个 Run 崩溃。

因此用户最终只看到稳定 code、可重试标记、友好 message 和 trace ID；技术堆栈只进入服务端日志。

---

## 第十一程：runtime.py 描述了下一步要完全落实的严格状态机

到这里，当前真实主路径已经走完。还有一个容易误解的模块：`app/runner/runtime.py`。

它不是另一个并行 Agent，而是更严格的执行控制设计：

- `TRANSITIONS` 声明 compile_policy、build_contract、plan、select_action、policy_gate、execute、normalize_observation、evaluate、update_state、reflection_gate、apply_reflection、completion_gate、finalize_response 等节点允许走向哪里。
- `PATCH_AUTHORITIES` 声明每个节点能修改哪些状态字段。
- `ERROR_EXITS` 声明每类错误可从哪里退出。
- `NodeResult` 是节点返回的统一结构。
- `LoopOrchestrator` 校验转移、patch authority 和错误出口，并根据 phase、幂等性、结果是否已记录决定崩溃恢复动作。
- `InvalidTransition` 表示非法跳转。
- `ObservationNormalizer` 把 tool/user/approval/validator 输入统一为 AgentObservation。
- `NoProgressDetector` 连续比较证据、criterion、已完成步骤和 plan version，识别重复无进展。

当前 `AgentLoop` 已实现其中不少概念，但仍用手写 if/for 控制主循环，并没有让 `LoopOrchestrator` 成为唯一调度器。因此读代码时应把 runtime.py 理解为“已实现并测试的确定性运行时骨架”，而不是当前请求必经的实际函数调用。

---

## 第十二程：测试怎样从最小部件一路证明完整流程

最后按照测试执行范围由小到大看一遍，能帮助确认前面主线没有遗漏。

1. `tests/conftest.py` 为每个测试创建内存 SQLite 和 async session。
2. `tests/test_errors.py` 验证网络超时等异常被安全分类。
3. `tests/test_tools.py` 验证搜索配置、凭据、Google 归一化、WebFetch 权限、HTML selector、metadata 和质量 warning；其中局部 `FakeClient` 模拟 Google HTTP 失败，真实 Google 测试只有配置环境变量才运行。
4. `tests/test_model_client.py` 验证 Mock 输出、真实模型配置门槛、JSON 提取及 contract/plan/final answer 归一化。
5. `tests/test_reasoning.py` 验证策略下限、契约、PlanGraph、Evaluation、ReflectionPatch、状态版本、fingerprint、CompletionGate、LoopOrchestrator、NoProgressDetector 和恢复决策。
6. `tests/test_repository.py` 验证 Task/Run、follow-up、turn、memory、waiting/resume 和乐观版本冲突的持久化。
7. `tests/fake_web_tools.py` 提供 `FakeSearch`、`FakeFetch` 和 fake registry，替代真实网络。
8. `tests/test_agent_loop.py` 使用多个 MockModelClient 子类控制决策顺序：`ContinueDecisionClient`、`RecoveringDecisionClient`、`PatchingReflectionClient`、`ToolThenFinalizeClient`、`RepeatedToolClient`、`TwoToolsThenFinalizeClient`，覆盖 turn limit、模型输出恢复、带 patch 的反思、工具执行、重复策略耗尽和完整两工具流程。
9. `tests/test_engine.py` 的 `PlanningSpyClient` 观察规划调用，再用 fake tools 验证 Engine 的整体路径。
10. `tests/test_api.py` 从 HTTP 边界验证创建、查询、策略、恢复和安全错误信封。

测试中的这些客户端不是生产模块，它们的价值是把模型原本不确定的选择变成可预测顺序，从而精确验证 Agent 状态机。

---

## 把整套实现压缩成一条真正的顺序

读完所有细节后，只需要记住这条调用链：

```text
uvicorn 导入 main
→ Settings / DB Session / FastAPI 就绪
→ POST /api/runs 解析 CreateRunRequest
→ PolicyCompiler 冻结 ReasoningPolicySnapshot
→ RunRepository 创建 Task + Run + Event
→ asyncio.create_task 启动 RunEngine
→ ModelClient 生成 TaskContract
→ normalize/validate contract
→ ModelClient 生成 PlanOutput
→ 持久化 Step，编译 PlanGraph 和 AgentState
→ AgentLoop 每轮组装 Context
→ ModelClient 返回 AgentDecision
→ 创建 AgentTurn
→ ToolRouter 做权限门控
→ WebSearch/WebFetch 执行并记录 ToolCall
→ AgentObservation
→ ObservationEvaluator 生成 Evaluation
→ MemoryManager 写可审计 Memory
→ 失败则 Reflection + fingerprint + 有限重试
→ 汇总 EvidencePack Artifact
→ ModelClient 生成 FinalAnswer
→ VerificationEngine 生成 VerificationReport
→ WebTaskAdapter 进行领域验证
→ CompletionGate 检查强制成功准则
→ RunEngine 写 FinalAnswer、answer.delta 和终态
→ RunRepository.run_to_view 组装 RunView
→ REST/SSE 把结果和全过程交给前端
```

这也是 Astra 当前 Agent 实现最核心的设计思想：模型负责提出结构化意图和内容，确定性代码负责权限、执行、证据、状态、重试、验证和最终能否宣布完成；数据库则把每一步变成可以查询、恢复和审计的事实。
