## Context

Astra 当前只有一条生产执行路径：`POST /api/runs` 编译 `RequestedReasoningPolicy`，`RunEngine` 创建任务契约与规范计划，`AgentLoop` 执行行动循环，最后由 `VerificationEngine` 和 `CompletionGate` 决定终态。前端无条件发送持久化对话策略和 `verification_level=standard`，而运行时尚未用验证等级裁剪最终化链路，因此普通问答也承担完整契约与校验成本。

现有 `conversation_strategy_preferences` 是跨重启保存下一次 Run 偏好的单例记录；Run 的 `reasoning_policy` 是创建时不可变快照。新增模式必须沿用这一分离，不能让偏好修改回写历史 Run。现有 `RunRecord.mode` 仍携带历史 `web_agent` 语义，前端 optimistic Run 又使用 `general-agent`，不适合作为新的产品模式字段。

## Goals / Non-Goals

**Goals:**

- 默认提供低等待感的快速回答，并在输入区显著提供可信模式开关。
- 两种模式共享模型、权限、工具、Agent Loop、SSE、会话、Artifact、取消与分享能力。
- 可信模式应用持久化对话策略并执行完整契约和验证链路。
- 两种模式都保留不可关闭的权限、工具输入、执行安全、Artifact 引用和错误处理保障；快速模式不执行产品质量校验。
- 将首选模式与每次 Run 的不可变模式/profile 快照持久化，并保证续跑语义稳定。
- 让可信模式的校验状态在过程与结果中可见、可审计。

**Non-Goals:**

- 不创建第二套 Chatbot/Agent 后端或复制 AgentLoop。
- 不承诺可信模式输出绝对正确，也不引入外部验证服务或多模型交叉验证。
- 不改变工具协议、权限审批模式、模型供应商配置或会话分享协议的核心语义。
- 不让快速模式绕过安全门、工具限制或敏感信息处理。

## Decisions

### 1. 使用独立 AnswerMode 和 RunExecutionProfile

新增 `AnswerMode.standard | trusted`。请求携带 `answer_mode`，后端 `RunProfileResolver` 将模式、执行审批模式、可信策略偏好与系统下限解析为 `RunExecutionProfile`。profile 至少包含生效 reasoning policy、contract mode、assurance level 和 validator plan。

选择 profile 而不是散落 `if trusted`，可以让 `RunEngine`、`AgentLoop` 和工具保持单一路径，也为未来研究、低成本等档位保留扩展点。`answer_mode` 单独写入 Run，不复用语义不一致的旧 `mode`。

### 2. 快速回答使用固定轻量策略，可信模式使用用户策略

`standard` 固定解析为 `fast + direct + reflection disabled + failure_only + max_tool_calls=5 + basic verification`。执行审批模式仍由输入区现有控制独立选择。`trusted` 使用数据库保存的推理强度、工具预算、规划、反思和触发方式，并强制 `strict verification`。

默认快速策略由后端决定，前端只发送模式和执行审批，防止客户端伪造一个看似快速但实际较重的组合。可信策略仍通过现有偏好 API 编辑并持久化。

### 3. 极速回答和完整校验分层

共享硬边界包括权限门、工具与预算硬上限、工具输入 Schema、运行错误、取消、Artifact 引用清洗和敏感数据边界。它们不受模式控制。

可信 profile 启用模型 TaskContract、规范计划、mandatory requirements、TaskAdapter 领域 outcome、VerificationEngine 聚合和 CompletionGate 严格判断。快速 profile 直接进入共享 AgentLoop 的无计划分支：首轮模型立即选择 `finalize`、`call_tool`、`ask_user` 或 `blocked`，不创建 TaskContract、Plan、AgentState、Evidence Pack Artifact、Memory 写入、VerificationReport 或 CompletionDecision。

快速结果继续使用同一 RunResult 边界，但 `verification_report` 和 `completion_decision` 为 null，明确表示未执行可信校验。工具调用后只保留结果归一化与权限/执行安全路径，不执行 ObservationEvaluator、反思或任务完成准则覆盖。

### 4. 可信续跑沿用原 Run 快照

新 Run 将 `answer_mode` 和解析后的 profile 一起持久化。`waiting_user` 的 `/resume` 不读取当前开关或最新偏好，而是继续使用原 Run 快照。普通后续追问创建新 Run，可采用提交时当前模式。

### 5. 模式偏好持久化但新安装默认快速

在 `conversation_strategy_preferences` 增加 `preferred_answer_mode`，首次创建为 `standard`。前端沿用 touched guard 和顺序保存链，避免启动 GET 覆盖用户点击以及乱序 PUT。可信策略字段即使在快速模式下也保持原值，不被快速 profile 回写。

### 6. 可信开关位于 Composer，策略入口按模式渐进呈现

开关常驻输入区上沿/工具行，关闭显示“快速回答”，开启显示带盾牌语义的“可信模式”。快速模式下模型菜单隐藏详细对话策略并提示开启可信模式后配置；可信模式下显示现有推理、工具、规划和反思控制。

可信 Run 的过程或回答显示“已校验”“带校验警告”或“未通过完整校验”。产品文案不得使用“保证正确”。切换不影响活动 Run，只影响下一次新建 Run。

### 7. 数据迁移和兼容默认值

偏好表新增非空 `preferred_answer_mode`，默认 `standard`；Run 表新增非空 `answer_mode`，历史记录回填 `trusted`，因为历史执行路径实际使用完整策略和验证。API 读取缺失字段时采用兼容默认值，前端 RunView 对旧响应回退 `trusted`，避免历史审计被误标为快速回答。

## Risks / Trade-offs

- [快速模式裁剪过多后与可信运行时漂移] → 继续复用 AgentLoop、ToolRouter、模型客户端和事件协议，仅在 profile 边界跳过计划、状态评估与最终校验。
- [“可信”被理解为事实保证] → 文案统一为“更完整策略与结果校验”，并展示 warning/blocked，不宣传绝对正确。
- [轻量最终化意外绕过安全检查] → 将基础保障显式建模为不可裁剪的 validator 集合，并增加两种模式安全回归测试。
- [偏好与 Run 快照竞态] → 保留 touched guard、串行 PUT 和后端不可变快照；resume 只读原 Run。
- [历史 mode 语义混乱] → 使用独立 `answer_mode`，历史 Run 回填 trusted，不改变旧 `mode` 字段。
- [可信模式隐藏后策略难以发现] → 常驻开关，并在快速模式模型菜单提供简短入口说明。

## Migration Plan

1. 增加 AnswerMode/profile Schema、数据库字段与迁移，历史 Run 回填 trusted，偏好默认 standard。
2. 扩展偏好、CreateRun、RunView 和前端 API 类型，保持旧请求缺省为 standard。
3. 接入后端 profile 解析和 Run 快照，再按 assurance level 调整契约与最终校验。
4. 增加 Composer 开关、渐进策略菜单和可信状态展示。
5. 执行迁移、后端测试、前端测试、类型检查、生产构建和 OpenSpec 校验。
6. 回滚时先让所有新请求解析为 trusted，再回滚 UI 和代码；保留新增字段不会破坏旧版本读取。

## Open Questions

- 第一版不增加独立的多模型交叉验证；未来可作为 trusted profile 的新 validator，而无需新增模式分支。
- 第一版首选模式按用户全局持久化；若未来引入账户或工作区，偏好记录可迁移到对应作用域。
