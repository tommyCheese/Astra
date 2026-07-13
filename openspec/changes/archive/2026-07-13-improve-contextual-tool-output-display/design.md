## Context

当前 `FinalAnswer` 的 `Finding` 只有 `text` 与 `source_urls`。图表和文件由工具产生为 `ArtifactRecord`，已经具备稳定 ID、`tool_call_id`、`sandbox_job_id`、MIME、checksum、security status 和内容 URL，但 `FinalAnswer` 无法表达 Artifact 与结论的关系。前端 `FinalAnswer` 先渲染全部 findings，再把整个 `run.artifacts` 交给单个 `ArtifactGallery`，因此多个工具输出集中在答案下方。

变更横跨模型输出 schema、Agent Loop 最终化、后端引用验证、RunView、流式状态切换和 React 展示。设计需要保持旧 Run 可读、现有 Artifact 安全边界不变，并避免为了展示关系引入新的 Artifact 存储副本。

## Goals / Non-Goals

**Goals:**

- 建立 finding 到 Artifact 的稳定、结构化且可验证的关联。
- 将输出紧邻其支撑的结论展示，并保留未关联输出。
- 让 ProcessPanel 能从 ToolCall 定位其输出。
- 兼容没有新字段的历史 Run 和没有 Artifact 的普通回答。
- 保持 summary 流式渲染速度，并在终态进行稳定布局切换。

**Non-Goals:**

- 不改变工具执行、Sandbox 或 Artifact 文件存储协议。
- 不让模型直接指定 CSS、组件类型、坐标或任意前端布局。
- 不把 Artifact 二进制内容内嵌进 FinalAnswer JSON。
- 不实现通用富文本块编辑器或允许模型输出任意 UI schema。
- 不为展示关系新建数据库表；最终结果 JSON 足以保存关联。

## Decisions

### Finding 使用 `artifact_ids`，不引入任意展示 DSL

`Finding` 增加 `artifact_ids: list[str] = []`。模型只决定“这个结论引用哪些已知 Artifact”，前端仍根据服务端可信 MIME 和 Artifact metadata 选择图片、隔离 HTML 或文件组件。

选择稳定 ID 而不是数组下标，是因为 Artifact 顺序可能随查询和后续扩展变化；选择简单 ID 列表而不是 `{type, layout, width}`，是为了避免模型控制展示与伪造类型。考虑过在 Markdown 中插入特殊 token，但 token 容易被转义、复制或模型误写，且难以做跨 Run 权限校验，因此不采用。

### 后端在最终持久化前规范化引用

Agent Loop 生成 `FinalAnswer` 后、构造最终 result 前，新增纯确定性的引用规范化步骤。它加载当前 Run 的 Artifact，建立允许集合，只接受：

- `artifact.run_id` 等于当前 Run；
- `security_status == verified`；
- 存在 `storage_key`，从而可以通过受控内容 API 访问。

每个 finding 内按首次出现去重。无效 ID 被静默移除，对用户只产生安全 warning；日志和 verification note 记录无效引用数量，不回显其他 Run 的 Artifact 信息。规范化后的 `FinalAnswer` 才进入 Run result 和 final_answer Artifact。

考虑过只在前端过滤，但那会让 API 继续携带未经验证的关系，并使不同客户端行为不一致，因此后端必须是完整性边界。

### 模型通过现有 Observation 获得可引用 ID

Chart Tool output 已返回 `artifacts`，其中包含 ArtifactRef ID；这些内容进入 tool outputs 与 observations。最终化提示将明确要求：只有确实支撑 finding 时才能把上下文中出现的 Artifact ID 写入 `artifact_ids`，不能编造 ID。普通直接回答继续返回空列表。

不新增专门的“布局模型调用”，避免额外延迟和 Token。引用质量由现有 decision/finalize 调用承担，后端确定性校验兜底。

### 前端采用“首次引用消费”算法

`FinalAnswer` 渲染时先建立可见 Artifact map，再按 findings 顺序处理 `artifact_ids`：

1. 过滤不存在或不可见的 ID；
2. 从全局 `renderedArtifactIds` 中排除已经渲染的 Artifact；
3. 在当前 finding 后调用可复用的 ArtifactGallery 渲染剩余项；
4. 记录首次展示位置，用于 ProcessPanel 定位；
5. 所有 findings 完成后，将仍未消费的 verified Artifact 放入“其他输出”。

这保证 Artifact 主内容只渲染一次，避免同一大型图表在多个结论间重复。后续 finding 对同一 Artifact 的引用可显示轻量链接，滚动到首次展示位置。

考虑过每次引用都重复渲染，虽然局部语义直接，但会增加页面长度、iframe 数量和移动端性能成本，因此采用首次引用消费。

### ProcessPanel 通过 `tool_call_id` 建立定位

前端从 `run.artifacts` 按 `tool_call_id` 分组。某个 turn 的 ToolCall 有输出时，ProcessPanel 显示输出数量和“查看输出”入口。目标 DOM ID 使用 Artifact ID 生成，不使用文件名或 storage key。若 Artifact 最终位于某个 finding 下，滚动到该处；否则滚动到“其他输出”。

### 流式阶段不尝试增量布局 Artifact

现有 answer.delta 只流式提取 summary；完整 findings 与 artifact_ids 要在 JSON 完成、后端校验和 RunView 刷新后才能可信。前端继续显示临时流式 assistant bubble，收到终态快照后一次性替换为 ProcessPanel 与结构化 FinalAnswer。

这避免 Artifact 在多个 finding 间跳动，也无需改变 SSE 协议。`answer.settling` 继续表达“文本完成，正在结构化和验证”。

### 兼容性通过默认空列表和降级区域实现

Pydantic 与 TypeScript 都把 `artifact_ids` 视为可缺省空列表。历史 Run 不需要数据库 migration。旧模型或未更新 provider 返回的 findings 会被 normalize helper 补为空列表。所有未消费 Artifact 仍进入“其他输出”，因此升级不会隐藏旧结果。

## Risks / Trade-offs

- [模型不引用或错误引用 Artifact] → 后端过滤无效引用，前端以“其他输出”保证内容不丢失，并通过测试与提示提高关联率。
- [同一 Artifact 支撑多个 findings] → 只首次完整渲染，后续提供定位链接，减少重复和性能成本。
- [Artifact 查询顺序不稳定] → finding 内遵循 `artifact_ids` 顺序；未关联输出按 `created_at`、`id` 做确定性排序。
- [大量交互 HTML 增加资源消耗] → 保持现有 iframe sandbox，并只渲染一次；后续可增加懒加载但不作为本变更前置条件。
- [混合 Web 与 Chart 的最终验证仍以 Web Adapter 为主] → 引用完整性使用独立确定性验证，不依赖当前 Adapter 选择；图表内容完整性继续由 ChartTaskAdapter.process 与 ArtifactCollector 保证。
- [前后端版本短暂不一致] → 新字段可缺省，旧前端忽略额外字段，新前端对缺失字段降级，支持滚动部署。

## Migration Plan

1. 先增加后端 schema 默认字段、normalize helper 和引用验证测试，保持 API 向后兼容。
2. 更新模型提示与 MockModelClient，使有 Artifact 的答案能够返回引用。
3. 更新前端类型和 ArtifactGallery，使新前端能同时读取新旧 Run。
4. 启用 finding 就近展示、其他输出和 ProcessPanel 定位。
5. 完成后端、前端、移动端与流式切换回归测试后发布。

回滚时可先回滚前端，后端多出的 `artifact_ids` 会被旧前端忽略；再回滚后端，无数据库 schema 需要降级，已有 result JSON 中的额外字段不会影响旧 Pydantic/TypeScript 读取路径。

## Open Questions

- 后续 finding 再次引用已展示 Artifact 时，第一版使用文本定位链接还是小型缩略图；默认建议文本定位链接。
- “其他输出”默认展开还是折叠；默认建议数量不超过 2 时展开，更多时折叠。
- 是否在 VerificationReport 中新增结构化 `invalid_artifact_references` 数量，还是第一版仅写入 notes；默认建议增加结构化计数以便审计统计。
