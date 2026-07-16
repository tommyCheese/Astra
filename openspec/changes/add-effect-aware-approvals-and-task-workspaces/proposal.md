## Why

现有执行模式把权限控制近似等同于“是否批准工具调用”：`request_approval` 会拦截所有没有 Run Grant 的工具调用，`plan_only` 则完全不进入工具执行。这既不能准确表达用户授权，也不足以覆盖现代 Agent 的完整安全边界：

- 用户真正关心的是行为会不会产生危险副作用，而不是工具叫什么。`web_search`、文件读取、`git diff` 等查询行为不应反复审批；首次创建文件、覆盖、删除、外部写入等行为即使来自非 Bash 工具也必须审批。
- `plan_only` 无法使用只读证据和临时计算来形成可靠计划，输出容易退化为通用、僵硬的步骤描述。
- 工具权限只是权限系统的一部分。Agent 还可能读取敏感数据、向外部域传输内容、使用用户凭据、调用 MCP/插件、委托子 Agent、改变安全配置或在无人值守任务中扩大影响范围。
- 单次审批不能替代平台策略、身份授权、沙箱、网络控制、数据防泄漏、供应链信任、配额和审计。即使用户允许某个动作，平台仍必须保证最小权限和不可越过的禁止规则。

同时，当前每个 Sandbox Job 都创建并销毁一个独立容器，只上传本次输入并收集本次 `/output`。不同工具无法自然共享文件，后续 Run 也无法继续修改前一轮生成的图片或文档；任务结束时也缺少完整的文件创建、修改和删除清单。

## What Changes

- 将本 change 从“工具审批”提升为统一的 `Agent Permission Control`：对主体、资源、动作、条件、数据流和委托链进行确定性授权。
- 建立分层策略：平台/组织强制策略不可被用户、Workspace、插件或 Agent 覆盖；Task/Run 策略只能收窄权限；用户批准只能授予平台允许范围内的短期能力。
- 明确 `deny → ask → allow` 决策优先级，deny 永远优先；策略、Sandbox 或安全 reviewer 不可用时对受控行动 fail closed。
- 将审批对象从工具调用升级为后端生成并冻结的 `ActionEffectPlan`，按实际权限、副作用、资源范围、可逆性和风险决定直接执行、请求审批或拒绝。
- 所有工具统一参与行为分析，不再为 Bash 设立独立审批原则；工具名仅用于调用约束、审计和展示。
- 调整三种执行模式与工具权限的组合语义：
  - `plan_only` 可执行平台允许的只读查询和非持久临时计算，但不得执行任何持久工作区写入、删除、外部系统写入、敏感数据释放或其他副作用行为。
  - `request_approval` 自动执行平台允许的无副作用行为；副作用行为必须匹配有效 Grant 或获得用户批准。
  - `auto_approval` 可跳过交互审批，但仍受工具注册、平台权限、资源范围、网络策略、预算和 Sandbox 强制限制。
- 在隔离任务工作区中，首次创建文件也视为持久副作用并要求审批；覆盖、删除、权限变更和外部写入采用更高风险说明。
- 支持“允许本次”“允许当前 Run 内相似行为”和用户明确选择的“允许本任务内相似行为”。默认相似授权为 Run 级，Task 级授权必须由用户明确选择。
- 为每个 Agent、Run、子 Agent 和外部工具调用建立可审计身份与委托链；子 Agent 获得的权限必须是父级权限的严格子集，且不能自行创建更宽授权。
- 引入 Credential Broker：凭据不进入模型上下文、Workspace 或普通日志，只在执行时按目标服务、动作、资源、有效期和调用身份签发最小化临时凭据。
- 引入数据权限和外发控制：区分读取、持有、转换、持久化和外发；敏感数据与不可信内容进入 Run 后可提高后续网络写入、外部消息和跨系统操作的审批等级。
- 将 MCP、插件、Skill 脚本、Hook 和自定义 Agent 纳入供应链信任边界，支持来源、版本、签名/摘要、工具清单快照、管理员 allowlist 和变更后重新授权。
- 支持受保护资源与高影响操作策略，例如安全配置、权限配置、凭据、审批记录、审计日志、系统目录和其他 Task 永远不可由普通 Task Grant 修改；关键操作可要求 step-up 或双人复核。
- 提供权限中心：查看有效策略、当前 Grants、凭据授权、工具来源和委托链，并支持撤销 Task Grants、终止 Run、禁用工具和解释“为什么允许/拒绝/询问”。
- 无人值守和自动化 Run 不等待隐式用户确认：只能使用预先配置的权限包，超出范围时拒绝或暂停，不能自动扩大权限。
- 将相似授权从 Bash 命令前缀扩展为后端生成的能力、资源范围和调用约束组合；无法生成窄范围规则时不提供相似授权。
- 优化审批面板，使其优先展示将发生的行为、受影响文件或外部资源、工作目录、网络范围和危险原因，再展示工具与完整参数。
- 为每个 Task 建立持久隔离工作区；工具仍可使用短生命周期 Sandbox，但按 effect plan 以只读、读写或不挂载方式共享 Task Workspace。
- 将 Task Workspace 的全部内容视为不可信数据，防止用户上传、生成文件、Library 内容或前序 Run 通过脚本、启动配置、依赖钩子、恶意文件名和链接影响 Astra 控制面或扩大工具权限。
- 在每次 ToolCall 前后记录工作区 Manifest 差异，跟踪创建、修改和删除；Run 结束生成 checkpoint，Task 结束或对话展示时提供完整文件与图片等交付物清单。

## Capabilities

### New Capabilities

- `agent-permission-control`: 分层策略、主体身份、资源授权、deny/ask/allow 决策、权限租约、撤销、解释和审计。
- `effect-aware-action-authorization`: 工具无关的行为分析、冻结 effect plan、风险审批和 Run/Task 范围授权。
- `credential-and-data-boundary`: 临时凭据代理、敏感数据访问、数据流追踪、外发控制和保留约束。
- `extension-trust-and-delegation`: MCP/插件/Hook/Skill/自定义 Agent 的供应链信任及子 Agent 权限衰减。
- `task-workspace-runtime`: Task 级持久隔离工作区、短生命周期 Sandbox 共享、权限化挂载和 Run checkpoint。
- `untrusted-workspace-containment`: 不可信工作区的执行隔离、配置净化、链接与归档防护、资源限制和内容信任边界。
- `workspace-change-tracking`: ToolCall 级文件差异、删除记录、最终文件清单、安全预览和下载。

### Modified Capabilities

- `runtime-reasoning-policy-enforcement`: 执行模式必须与工具权限及行为副作用联合决策；`plan_only` 允许无副作用工具行动而禁止副作用行动。
- `policy-driven-tool-runtime`: Tool Router 解析工具后必须生成并强制执行 invocation 级权限与 effect plan，而不是仅依赖静态工具风险。
- `agent-chat-ui`: 审批面板展示行为与授权范围，并提供一次性、Run 级和明确的 Task 级决策。
- `artifact-storage-and-delivery`: 交付物从单次 Sandbox `/output` 扩展到 Task Workspace 中经过检查的文件和 Run 变更记录。

## Impact

- 后端 ToolSpec、Tool Router、AgentLoop、执行模式策略、ApprovalRequest/Grant、Run checkpoint 和恢复逻辑。
- 权限策略引擎、Agent identity、Delegation record、Credential Broker、data-flow state、managed policy 和权限解释 API。
- 工具级 effect analyzer，尤其是 Bash 语法与命令风险分析、文件操作工具、网络工具和产物生成工具。
- MCP/插件注册表、签名与版本锁定、外部工具 schema/annotation 信任、子 Agent 创建和自动化执行。
- Sandbox Provider、Task Workspace 生命周期、只读/读写挂载、配额、清理和崩溃恢复。
- 文件 Manifest、变更记录、Artifact 安全检查、RunView/ConversationView 和下载 API。
- 前端执行模式文案、审批面板、工作区文件变更和最终输出展示。
- 数据库迁移、安全测试、行为分类测试、授权匹配测试、跨工具文件共享和多 Run 恢复测试。
