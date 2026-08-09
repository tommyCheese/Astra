# agent-chat-ui Specification

## Purpose
TBD - created by archiving change implement-core-web-agent-loop. Update Purpose after archive.
## Requirements
### Requirement: Chat UI is the primary Agent interface
The system SHALL present both runtime kinds in one chat interface while projecting their actual capabilities: Fast Runs show user messages, compact tool activity, approvals and streamed answers; Trusted Runs additionally show plans, reflections, evidence and verification results.

#### Scenario: User submits a fast message
- **WHEN** the user sends a task with quick mode selected
- **THEN** the UI appends the user message and streams Fast Runtime events into the conversation
- **THEN** the UI does not create empty plan, reflection or verification placeholders

#### Scenario: Trusted Agent returns final answer
- **WHEN** a Trusted Run completes
- **THEN** the UI displays the final answer with its actual verification and evidence state

### Requirement: Tool activity is visible but compact
The system SHALL display Web tool calls as compact tool event rows inside the conversation and allow users to expand details.

#### Scenario: Web search event
- **WHEN** `web_search` starts or completes
- **THEN** the chat UI shows a tool event with tool name, status, candidate count, and warnings if present

#### Scenario: Web fetch event
- **WHEN** `web_fetch` completes
- **THEN** the chat UI shows source URL, extraction strategy, quality score, and warnings if present

### Requirement: Reflection is visible as an Agent process event
The system SHALL display reflection summaries when the Agent changes strategy due to failure, low confidence, insufficient evidence, or verification problems.

#### Scenario: Agent retries after reflection
- **WHEN** a reflection causes a retry or revised query
- **THEN** the chat UI shows the reflection summary and the next action

### Requirement: Audit details remain accessible
The system SHALL preserve access to run timeline, steps, tool calls, artifacts, Evidence Pack, memory reads, memory writes, and verification report from the chat UI.

#### Scenario: User expands audit details
- **WHEN** the user expands an Agent message or opens the audit drawer
- **THEN** the UI shows detailed timeline, Agent turns, tool calls, artifacts, memory events, and verification data for the run

### Requirement: Chat UI keeps liquid glass visual style
The system SHALL keep the existing modern liquid glass visual direction while adapting layout to a Gemini-like chat interface.

#### Scenario: Desktop layout
- **WHEN** the app is viewed on a desktop viewport
- **THEN** the conversation area, composer, and optional audit drawer fit without overlapping text or controls

#### Scenario: Mobile layout
- **WHEN** the app is viewed on a mobile viewport
- **THEN** messages, tool events, source cards, and composer remain readable and do not overlap

### Requirement: Conversation supports Web Agent run status
The system SHALL map run and turn statuses to user-readable chat states.

#### Scenario: Run is executing
- **WHEN** the Agent loop is executing
- **THEN** the UI shows the active Agent state such as searching, reading sources, reflecting, verifying, or composing

#### Scenario: Run is blocked
- **WHEN** the run becomes blocked
- **THEN** the UI shows a clear blocked message with reason and any required user action

### Requirement: Sidebar separates pinned and recent conversations
系统 SHALL 在侧栏将置顶 Conversation 显示在独立“置顶”区，将其他 Conversation 显示在“最近”区；置顶区为空时 SHALL 隐藏整个分区。

#### Scenario: Sidebar has pinned conversations
- **WHEN** 后端返回一个或多个置顶 Conversation
- **THEN** 侧栏在最近列表上方显示独立置顶区且 Conversation 不重复出现

#### Scenario: Sidebar has no pinned conversations
- **WHEN** 后端没有返回置顶 Conversation
- **THEN** 侧栏不显示空的置顶标题或占位内容

### Requirement: Conversation actions are available from an item menu
每个 Conversation 项 SHALL 在 hover 或键盘聚焦时提供操作菜单，菜单 SHALL 包含重命名、置顶或取消置顶、分享和删除；菜单操作不得误触发会话切换。

#### Scenario: Open conversation menu
- **WHEN** 用户点击 Conversation 行的更多操作按钮
- **THEN** UI 显示与该 Conversation 关联的操作菜单并保持当前会话选择不变

### Requirement: Destructive and text-editing actions use accessible dialogs
重命名、分享和删除 SHALL 使用支持键盘焦点与 Escape 关闭的对话框；删除对话框 MUST 明确说明不可撤销并要求用户再次确认。

#### Scenario: Confirm permanent deletion
- **WHEN** 用户从会话菜单选择删除
- **THEN** UI 显示包含会话标题、不可撤销说明、取消按钮和危险样式永久删除按钮的确认框

#### Scenario: Rename with keyboard
- **WHEN** 用户打开重命名对话框并输入有效标题后按 Enter
- **THEN** UI 保存标题、关闭对话框并更新侧栏显示

### Requirement: Share dialog exposes snapshot lifecycle
分享对话框 SHALL 显示公开范围提示、当前分享链接和复制操作；已有分享时 SHALL 提供更新快照与停止分享操作。

#### Scenario: Share is created
- **WHEN** 用户首次确认创建分享
- **THEN** UI 显示可复制链接并提示任何获得链接的人均可查看该只读快照

#### Scenario: Existing share is managed
- **WHEN** 用户打开已有分享的分享对话框
- **THEN** UI 显示原链接、快照更新时间、更新分享和停止分享操作

### Requirement: Chat composer 在活动 Run 期间提供终止控制
系统 SHALL 在消息已提交且 Run 尚未进入终态时，将发送按钮替换为可访问的终止按钮。

#### Scenario: Run 创建或执行中
- **WHEN** 创建请求正在进行或当前 Run 仍处于活动状态
- **THEN** composer 显示终止图标而不是发送箭头
- **THEN** 终止按钮具有明确的可访问名称且不会提交新消息

#### Scenario: 用户点击终止按钮
- **WHEN** 用户点击活动 Run 的终止按钮
- **THEN** UI 立即进入终止中状态并阻止重复请求
- **THEN** 取消收敛后 composer 恢复发送按钮

#### Scenario: 创建响应返回前点击终止
- **WHEN** 用户在创建 API 尚未返回 run id 时点击终止
- **THEN** UI 记录取消意图并在获得 run id 后立即请求取消

### Requirement: Chat composer presents pending tool approvals
The system SHALL render a recoverable approval card immediately above the chat input whenever the current Run has a pending tool approval.

#### Scenario: Display a pending approval
- **WHEN** a Run view or event reports a pending approval
- **THEN** the UI shows the tool name, safe command or input preview, requested permission, impact scope, and the available approval decisions

#### Scenario: Decide an approval
- **WHEN** the user selects `仅本次`, `允许类似命令`, or `拒绝`
- **THEN** the UI submits the corresponding decision once, disables duplicate actions while pending, and refreshes or resumes the Run from the server response

#### Scenario: Similar approval is unsafe
- **WHEN** the pending approval does not include a backend-generated similar matcher
- **THEN** the UI omits the `允许类似命令` action

#### Scenario: Approval card on a narrow viewport
- **WHEN** the chat is displayed on a mobile viewport
- **THEN** the approval summary and all available decisions remain readable and operable without overlapping the composer

### Requirement: 聊天输入区显著展示可信模式开关
系统 SHALL 在聊天 Composer 的常驻可见区域提供可信模式开关，并 SHALL 在不打开模型或设置菜单的情况下识别和切换当前模式。

#### Scenario: 快速回答状态
- **WHEN** 当前首选模式为 standard
- **THEN** 输入区显示“快速回答”以及关闭的可信开关
- **THEN** 控件不会与发送、附件、执行审批或模型选择重叠

#### Scenario: 可信模式状态
- **WHEN** 当前首选模式为 trusted
- **THEN** 输入区以克制但明确的视觉状态显示“可信模式”已开启
- **THEN** 控件提供可访问名称和键盘操作

### Requirement: 对话策略按回答模式渐进呈现
系统 SHALL 仅在可信模式下提供推理强度、工具预算、计划、反思和验证策略控制。快速模式 SHALL 只展示模型、执行审批及其独立 Fast Runtime 设置，并 MUST NOT 将可信策略保存或发送给 Fast Run。

#### Scenario: 快速回答打开模型菜单
- **WHEN** standard 模式用户打开模型菜单
- **THEN** UI 允许选择模型并展示适用的最小 Fast Runtime 设置
- **THEN** UI 不显示或提交可信推理、计划、反思和验证字段

#### Scenario: 可信模式打开模型菜单
- **WHEN** trusted 模式用户打开模型菜单
- **THEN** UI 展示并允许修改持久化可信对话策略

### Requirement: Astra provides a separate shared Skill management surface
The desktop experience SHALL provide a dedicated Skill route separate from chat that lists globally shared built-in and custom Skills by origin, lifecycle state, active revision, compatibility, and diagnostic state.

#### Scenario: Open the Skill library
- **WHEN** the administrator navigates to Skill management
- **THEN** the UI distinguishes immutable Astra built-ins from editable custom Skills
- **THEN** it does not present user ownership, tenant, workspace, sharing-scope, or Publisher controls

### Requirement: Custom Skills open in the authoring workbench
The Skill management surface SHALL open custom Skills in the multi-file authoring workbench and SHALL support create, import, edit, test, publish, disable, export, revision history, restore-to-Draft, and recoverable removal actions.

#### Scenario: Review a Draft before publication
- **WHEN** the administrator opens a changed custom Skill
- **THEN** the UI shows dirty and saved state, validation findings, requested tools, scripts, resource inventory, Draft-versus-Published Diff, and publish readiness

#### Scenario: View a built-in Skill
- **WHEN** the administrator opens a built-in Skill
- **THEN** files are read-only and the primary customization action creates a custom clone

### Requirement: Composer supports Skill selection in both modes
The chat Composer SHALL offer automatic Skill selection and explicit Skill selection for both quick response and trusted execution, and SHALL explain that Skill selection does not change the chosen answer mode.

#### Scenario: Select a Skill in quick mode
- **WHEN** the administrator explicitly selects a Skill while quick response is active
- **THEN** the Composer displays the selection without showing trusted Plan controls

#### Scenario: Select a Skill in trusted mode
- **WHEN** the administrator explicitly selects a Skill while trusted execution is active
- **THEN** the Composer indicates that the Skill will be resolved before TaskContract and Plan generation

### Requirement: Chat shows Skill activation and use
The chat timeline SHALL show compact events when a Skill is activated, rejected, conflicts, loads a resource, materially guides an action, or causes a trusted Plan revision, with details available in the audit view.

#### Scenario: Skill activates automatically
- **WHEN** the model activates a Skill based on its description
- **THEN** the timeline identifies the Skill origin and revision and explains that it was selected for the current task

#### Scenario: Skill-guided action needs approval
- **WHEN** a Skill-guided tool call pauses for approval
- **THEN** the approval UI attributes the recommendation to the Skill while explaining that Astra runtime policy, not the Skill, controls authorization

### Requirement: Skill diagnostics are actionable
The UI SHALL distinguish invalid format, incompatible runtime, Draft-only state, disabled or revoked revision, digest drift, missing tool, budget exhaustion, stale editor revision, publication conflict, and failed Draft test states and SHALL present a corrective action when one exists.

#### Scenario: Required tool is missing
- **WHEN** an active Skill cannot continue because its required tool is unavailable
- **THEN** the chat explains the capability gap without presenting the Skill as successfully executable

### Requirement: Historical Skill use remains inspectable
The audit view SHALL show the frozen Skill identities, origins, Published or Draft-test digests, activation initiators, relevant model operations, resource reads, attributed tool calls, Plan bindings, policy outcomes, and revocation events used by a historical Run.

#### Scenario: Custom Skill changed after completion
- **WHEN** the administrator inspects a completed Run after the custom Skill was republished
- **THEN** the audit view continues to identify the exact frozen revision used by that Run

### Requirement: Composer exposes a Skill slash command panel
The Chat Composer SHALL open a searchable Skill command panel when the user types `/` at a command boundary and SHALL list only enabled Skills with an active eligible Published Revision.

#### Scenario: Open the panel at a command boundary
- **WHEN** the user types `/` at the beginning of the Composer or after whitespace
- **THEN** the UI opens a Skill command panel anchored to the Composer
- **THEN** options identify each Skill by name, description, origin, and selected state

#### Scenario: Filter Skill options
- **WHEN** the user types characters after the slash without crossing a whitespace boundary
- **THEN** the UI filters options by name, description, and qualified identity
- **THEN** a no-results state is shown without modifying the current Skill selections

#### Scenario: Slash is ordinary text
- **WHEN** a slash occurs inside a URL, filesystem path, or non-command token
- **THEN** the Skill panel remains closed and the text is preserved unchanged

### Requirement: Slash Skill selection is keyboard and pointer accessible
The Skill command panel SHALL support pointer selection and listbox keyboard navigation, and SHALL preserve normal Composer submission behavior when the panel is closed.

#### Scenario: Select with the keyboard
- **WHEN** the panel is open and the user navigates with Arrow, Home, or End keys and presses Enter
- **THEN** the highlighted Skill is selected
- **THEN** Enter does not submit the Composer
- **THEN** focus returns to the message input

#### Scenario: Cancel slash selection
- **WHEN** the panel is open and the user presses Escape
- **THEN** the panel closes without changing selected Skills
- **THEN** the typed slash text remains available as ordinary message text

### Requirement: Selected Skills remain visibly highlighted in the Composer
The Composer SHALL render every selected Skill as a persistent high-contrast token outside the plain-text message value, and selected state MUST remain understandable without relying on color alone.

#### Scenario: Select a Skill
- **WHEN** the user chooses a Skill from the slash panel or existing attachment menu
- **THEN** the slash query range is removed from the message
- **THEN** a highlighted token with the Skill name, icon, selected semantics, and remove control appears in the Composer

#### Scenario: Remove a selected Skill
- **WHEN** the user activates a token's remove control
- **THEN** only that Skill is removed and the remaining message and Skill tokens are preserved

#### Scenario: Remove the last token with Backspace
- **WHEN** the message input is empty, its caret is at the beginning, and the user presses Backspace
- **THEN** the last selected Skill token is removed

#### Scenario: View selections on supported layouts
- **WHEN** selected Skill tokens are shown in light theme, dark theme, narrow view, or reduced-motion mode
- **THEN** each token, focus indicator, label, and remove action remains readable and operable

### Requirement: Skill tokens have a one-Run draft lifecycle
The Composer SHALL treat selected Skill tokens as part of the unsent message draft, SHALL consume them only after successful Run creation, and SHALL retain them when submission fails.

#### Scenario: Run creation succeeds
- **WHEN** a message with selected Skill tokens creates a Run successfully
- **THEN** the submitted user message contains only the intended message text
- **THEN** the Composer clears the message, slash state, and selected Skill tokens

#### Scenario: Run creation fails
- **WHEN** network, validation, or Skill activation failure prevents Run creation
- **THEN** the Composer retains the message and selected Skill tokens for correction and retry

#### Scenario: Start a new conversation
- **WHEN** the user explicitly starts a new conversation before submitting the draft
- **THEN** the previous conversation's selected Skill tokens are cleared

### Requirement: History limit copy distinguishes display from retention
The system SHALL describe the sidebar history limit as the maximum number of conversations currently displayed and SHALL NOT imply that exceeding the limit deletes persisted conversations.

#### Scenario: Sidebar renders history limit
- **WHEN** the conversation sidebar is displayed
- **THEN** its copy identifies the configured client limit as a display limit

### Requirement: 聊天框模型菜单展示模型级思考控制

系统 SHALL 在聊天框的模型选择菜单中根据当前模型能力展示思考开关与思考深度，并 SHALL 将这些控件与 Agent 的可信对话策略分区呈现。

#### Scenario: 当前模型支持开关和多个深度
- **WHEN** 用户在聊天框打开模型菜单且当前模型支持可选思考
- **THEN** 菜单显示思考开关
- **THEN** 思考开启时仅显示该模型支持的深度选项
- **THEN** 模型摘要显示当前选择

#### Scenario: 用户关闭思考
- **WHEN** 用户关闭支持关闭的模型思考开关
- **THEN** 深度控件隐藏或禁用
- **THEN** 下一次运行提交关闭状态

#### Scenario: 当前模型强制思考
- **WHEN** 当前模型声明思考始终开启
- **THEN** 菜单显示锁定的开启状态和原因说明
- **THEN** 用户仍可在模型支持时选择思考深度

#### Scenario: 当前模型不支持思考控制
- **WHEN** 当前模型能力为不可用
- **THEN** 菜单不显示可操作的开关或深度选择
- **THEN** 菜单显示简短的不支持说明

### Requirement: 模型思考控件可访问且适配不同视口

系统 SHALL 为思考开关和深度控件提供可访问名称、键盘操作、选中与禁用状态，并 MUST 在桌面和移动视口中保持可读且不重叠。

#### Scenario: 键盘调整思考设置
- **WHEN** 键盘用户聚焦模型思考控件
- **THEN** 用户可以操作开关并选择受支持的深度
- **THEN** 辅助技术可读取当前模型、状态、深度及受限原因

#### Scenario: 移动端打开模型菜单
- **WHEN** 用户在移动视口打开包含思考控制的模型菜单
- **THEN** 模型列表、思考控制和可信策略分区均保持可滚动和可点击

### Requirement: 界面思考过程独立于模型思考参数

系统 SHALL 将聊天中的 Astra“思考”定义为运行阶段和可公开审计摘要，并 SHALL 将供应商在用户开启模型思考后明确公开返回的思考正文显示为独立的“模型思考”条目。模型思考开关或深度 MUST NOT 决定 Astra 过程面板本身的产生、可见性或展开偏好；界面 MUST NOT 展示供应商未公开、加密或推断出的隐藏思维链。

#### Scenario: 用户关闭模型思考
- **WHEN** 当前模型思考被关闭且 Run 正在执行
- **THEN** 界面仍根据运行事件显示“思考中”、阶段、工具活动和公开摘要
- **THEN** 界面不显示模型思考正文条目，Run 结束后仍显示对应的 Astra 完成状态

#### Scenario: 用户开启模型思考且供应商返回正文
- **WHEN** 当前模型思考开启且供应商返回可见思考增量
- **THEN** 过程面板实时追加一个与 Astra 摘要分离的“模型思考”条目
- **THEN** 展开条目后按原始顺序、换行和完整性状态展示供应商公开返回的全部正文

#### Scenario: 用户切换模型思考深度
- **WHEN** 用户从低深度切换到高深度或反向切换
- **THEN** Astra 过程面板的显示规则、折叠偏好和公开摘要事件协议保持不变
- **THEN** 后续 Run 的模型思考条目只展示该 Run 实际收到的供应商可见内容

#### Scenario: 用户查看思考说明
- **WHEN** 用户查看模型思考控件或过程面板说明
- **THEN** 界面明确说明模型思考控制模型生成行为
- **THEN** 界面区分 Astra 的公开执行摘要、供应商公开思考正文或摘要，以及不可获得的隐藏思维链

#### Scenario: 用户展开正在生成的模型思考
- **WHEN** 用户展开一个仍在接收增量的模型思考条目
- **THEN** 正文区域以独立、清晰的流式卡片展示 Provider、操作和生成状态
- **THEN** 正文内部滚动位置随最新思考增量移动到底部，且不会强制改变已折叠条目或对话主区域的滚动位置

#### Scenario: 高频流式增量到达
- **WHEN** 回答或模型思考在短时间内连续收到多个增量
- **THEN** 界面按浏览器动画帧合并更新，同一思考流在单帧内只执行一次正文拼接，并避免重新渲染未发生变化的过程条目
- **THEN** 可见输出使用独立于网络分片频率的帧级缓冲平滑追赶最新内容，同时保持事件游标、去重、增量顺序与正文完整性
- **THEN** 跟随最新内容所需的滚动测量在绘制后合并执行，不阻塞当前文本帧

#### Scenario: Run 思考完成
- **WHEN** 一个 Run 已完成且后端提供了固定的处理耗时
- **THEN** 过程面板标题在“思考完成”后显示紧凑的“已处理 X 秒/分钟/小时”
- **THEN** 历史对话刷新后显示相同耗时，运行中的对话不显示未固定时长

### Requirement: 模型菜单提示回答模式中的思考影响

系统 SHALL 在不阻止或自动修改受支持选择的前提下，向用户说明同一模型思考深度在快速模式与可信模式中的主要延迟、用量和质量影响。影响提示 MUST NOT 被表述为模式预算或模式强制值。

#### Scenario: 快速模式选择高深度
- **WHEN** 用户在快速模式选择高思考深度
- **THEN** 菜单提示该选择可能显著增加首字和总响应延迟
- **THEN** 菜单不声称该选择会启用可信模式能力

#### Scenario: 可信模式选择高深度
- **WHEN** 用户在可信模式选择高思考深度
- **THEN** 菜单提示该设置会用于本次运行的多次模型调用并可能增加耗时与用量

#### Scenario: 可信模式关闭思考
- **WHEN** 用户在可信模式关闭可选模型思考
- **THEN** 菜单说明可信模式的计划、审批与验证仍保持启用

#### Scenario: 用户切换回答模式
- **WHEN** 用户在设置模型思考深度后切换快速或可信模式
- **THEN** 模型菜单保持用户选择
- **THEN** 菜单只更新当前模式下的影响说明

### Requirement: Composer displays live context-window status
The Chat Composer SHALL display the selected model's context capacity, estimated used and remaining Tokens, usage status, and active compression state inside the model selector, SHALL NOT render context status as a separate Composer row, and SHALL update those values when the conversation, model, draft, or context command result changes.

#### Scenario: View normal context usage
- **WHEN** a conversation and model are selected
- **THEN** a compact circular context indicator inside the model selector shows estimated usage against total capacity
- **THEN** assistive text exposes used, remaining, model, and estimate semantics

#### Scenario: View context capacity before the first message
- **WHEN** a model is selected but no conversation has been created
- **THEN** the model selector initializes a zero-usage circular indicator from that model's server-resolved capacity
- **THEN** the selector does not reserve an empty circular-indicator column while capacity is still loading or unavailable

#### Scenario: Keep the Composer input area compact
- **WHEN** context-window status is available
- **THEN** the UI does not add a separate context row above the message input
- **THEN** the input height and control-row layout remain unchanged

#### Scenario: Approach automatic compression
- **WHEN** usage reaches a warning or automatic compression threshold
- **THEN** the indicator changes status with text in addition to color
- **THEN** the user can discover `/compact` as a manual action

#### Scenario: Context was compacted or cleared
- **WHEN** a context command or automatic compression changes the projection
- **THEN** the Composer refreshes the indicator without requiring a page reload
- **THEN** the UI identifies the latest context action

#### Scenario: Inspect exact circular-indicator values
- **WHEN** the user hovers the circular context indicator or opens the selected model control
- **THEN** exact used, total, remaining, source, and latest-action details are available
- **THEN** the indicator does not rely on color alone to convey warning or critical state

#### Scenario: Hover outside the circular indicator
- **WHEN** the user hovers the model name, strategy summary, or empty area of the selected model control
- **THEN** the circular indicator tooltip remains hidden
- **THEN** assistive technology can still read the context status from the selected model control

#### Scenario: Inspect context inside the open model menu
- **WHEN** the model menu is open and already displays exact context values
- **THEN** hovering its compact circular indicator does not open a second tooltip over the menu
- **THEN** the exact used, total, and remaining values remain visible in the context detail row

#### Scenario: Keep model controls free of redundant warning cards
- **WHEN** the user changes model-thinking depth or trusted execution mode
- **THEN** the model menu does not insert a separate yellow explanatory warning card
- **THEN** the selected controls continue to communicate their current state directly

#### Scenario: Change the selected model configuration
- **WHEN** the selected model's effective context configuration changes
- **THEN** the circular indicator and context request update without a page reload
- **THEN** no standalone context row is introduced

#### Scenario: Present context details in user-facing language
- **WHEN** the Composer, model menu, or Model Settings displays context information
- **THEN** the UI describes the limit, remaining space, estimate, and latest action in user-facing language
- **THEN** internal catalog, fallback, verification, metadata, and command-registry labels are not exposed

### Requirement: Execution mode descriptions reflect effect policy
The chat UI SHALL explain execution modes in terms of side effects and approval behavior rather than whether tools are called.

#### Scenario: Plan-only description
- **WHEN** the user views the plan-only option
- **THEN** the UI explains that Astra may research and analyze with safe tools but will not perform any persistent or external side-effect action

### Requirement: Approval panels describe actions and scopes
The chat UI SHALL present pending approvals using a human-readable action summary, affected resources, risk reason, working directory, network scope, and available grant scopes.

#### Scenario: Approve a file creation
- **WHEN** a tool proposes creating `reports/summary.md`
- **THEN** the panel states that a persistent file will be created and offers allow-once plus any safe Run- or Task-scoped grant proposals

#### Scenario: Approval panel stays user-facing
- **WHEN** an approval has internal permissions, URIs, working-directory metadata, or a long command preview
- **THEN** the default panel shows the human-readable action, affected file or service, practical risk, approval scopes, and the exact Bash command when applicable, without exposing raw permission identifiers or internal resource URIs

#### Scenario: Explicit Task grant
- **WHEN** a Task-scoped proposal is available
- **THEN** its button clearly states that permission continues across later requests in the current Task

#### Scenario: Similar scope is unsafe
- **WHEN** the backend cannot produce a narrow resource and invocation matcher
- **THEN** the UI omits similar Run and Task actions and offers only allow-once and reject

### Requirement: Results show Workspace changes and deliverables
The chat UI SHALL show meaningful files created, modified, and deleted by the current Run and SHALL provide safe previews or downloads for eligible deliverables.

#### Scenario: Run produces multiple file types
- **WHEN** a Run creates source, Markdown, data, and image files
- **THEN** the result groups them coherently and previews supported files without hiding the rest of the change summary

### Requirement: 输入区只提供快速响应与可信执行选择
系统 SHALL 在 Composer 中以一个二元控件展示“快速响应”和“可信执行”，并 SHALL 不展示独立的仅规划产品模式。

#### Scenario: 快速响应状态
- **WHEN** 当前首选模式为 standard
- **THEN** 输入区显示快速响应语义
- **THEN** 用户无需理解规划策略即可预测该 Run 不创建 DAG

#### Scenario: 可信执行状态
- **WHEN** 当前首选模式为 trusted
- **THEN** 输入区显示先规划、再执行、再验证的可信执行语义
- **THEN** UI 不将可信描述为保证结果绝对正确

### Requirement: 可信设置不展示规划策略
系统 SHALL 从模型菜单和设置中删除自适应与先规划选择器，并 SHALL 将 trusted 的完整 DAG 规划表现为模式固有行为。

#### Scenario: 用户打开可信设置
- **WHEN** trusted 用户打开模型或策略菜单
- **THEN** UI 可以展示推理强度、工具预算和反思设置
- **THEN** UI 不展示规划策略字段

### Requirement: 可信模式提供计划执行确认控制
系统 SHALL 在 trusted 模式的模型/策略菜单内提供“计划生成后直接执行”控制，不得将其作为输入框旁的独立平铺按钮，并 SHALL 在需要确认时于完整计划生成后展示版本绑定的执行按钮。

#### Scenario: 可信执行菜单展示控制
- **WHEN** trusted 用户打开模型/策略菜单
- **THEN** 菜单展示“计划生成后直接执行”开关及当前行为说明
- **THEN** 输入框旁不额外平铺该开关

#### Scenario: 可信策略按功能分组
- **WHEN** trusted 用户查看模型/策略菜单
- **THEN** UI 将计划执行、推理资源和反思策略分别组织为功能组
- **THEN** 相邻功能组之间使用轻量横线分隔，同组字段不重复分隔

#### Scenario: 可信策略帮助集中展示
- **WHEN** trusted 用户查看模型/策略菜单
- **THEN** 各项设置不分别展示帮助按钮
- **THEN** UI 在全部可信策略控件之后展示唯一的“了解可信策略”入口，并用轻量分隔与设置区区分
- **THEN** 点击入口后按计划执行、推理资源和反思策略集中展示完整说明

#### Scenario: 集中帮助采用分组可读布局
- **WHEN** 用户打开可信策略帮助
- **THEN** 弹窗使用全宽单列排列功能组，并以组标题区分计划执行、推理资源和反思策略
- **THEN** 宽屏使用短标签与说明的双列条目，窄屏降为单列
- **THEN** 弹窗在视口内滚动，不产生狭窄内容列或大面积无效空白

#### Scenario: 用户选择直接执行
- **WHEN** trusted 用户开启“计划生成后直接执行”并提交任务
- **THEN** UI 将 `plan_execution=auto` 发送给后端
- **THEN** 计划生成后无需额外 Plan 确认即可开始节点调度

#### Scenario: 用户选择先查看计划
- **WHEN** trusted 用户关闭“计划生成后直接执行”并提交任务
- **THEN** UI 将 `plan_execution=confirm` 发送给后端
- **THEN** 完整 DAG 生成后 UI 展示计划和“执行计划”按钮

#### Scenario: 用户执行已展示计划
- **WHEN** 用户点击“执行计划”
- **THEN** UI 提交当前 continuation token 和预期 Plan 版本
- **THEN** UI 不把该点击表现为批准后续所有工具效果

#### Scenario: 快速响应隐藏控制
- **WHEN** 当前模式为 standard
- **THEN** UI 在输入区及模型/策略菜单中都不展示计划执行确认控制

### Requirement: 审批控件不包含仅规划
系统 SHALL 只在审批控件中展示请求批准和自动批准，并 SHALL 将其描述为权限交互行为。

#### Scenario: 用户打开审批菜单
- **WHEN** 用户查看审批行为选项
- **THEN** 菜单不包含“仅规划”
- **THEN** 菜单说明两种回答模式仍受平台硬性安全边界限制

### Requirement: 审计视图只展示真实 DAG
系统 SHALL 仅为实际持久化 Plan DAG 的 Trusted Run 展示图谱工作台。Fast Run SHALL 展示独立的简洁动作时间线，并 MUST NOT 根据工具事件推断或合成 DAG、验证或反思状态。

#### Scenario: 快速运行调用多个工具
- **WHEN** Fast Runtime 连续调用多个工具
- **THEN** UI 按时间顺序展示紧凑工具活动
- **THEN** UI 不创建 Plan node、可信执行图或“已校验”标记

#### Scenario: 可信运行存在 DAG
- **WHEN** Trusted Run 已持久化规范 Plan DAG
- **THEN** UI 展示对应图谱、节点执行与验证关联

### Requirement: Chat UI 清晰展示并发子 Agent 和 Join 状态
系统 SHALL 同时展示多个 child 的目标、父级、运行/等待/终态、Join 关系、预算和关键等待原因，并 SHALL 使用户能够区分并发执行与串行步骤而不暴露隐藏推理。

#### Scenario: 两个 child 同时运行
- **WHEN** Run snapshot 包含两个 running child
- **THEN** 子 Agent 面板和执行图谱同时显示两个活动分支及各自状态
- **THEN** 汇总计数、预算和等待信息与权威快照一致

#### Scenario: 一个 child 等待而另一个完成
- **WHEN** sibling child 分别处于 waiting_approval 和 completed
- **THEN** UI 分别展示等待原因和完成摘要
- **THEN** UI 不把整个 Run 错误显示为只能等待该 child

#### Scenario: Join 已 ready 但尚未消费
- **WHEN** child 已完成且 Join 处于 ready 或 merging
- **THEN** UI 将其显示为正在汇合而不是根任务已经完成

### Requirement: 工具设置展示并控制 Swarm
系统 SHALL 在工具设置界面展示 `swarm` 的名称、子 Agent 用途、当前开关状态、可用性和不可用原因，并 SHALL 通过与其他工具一致的键盘可操作 switch 保存用户选择。

工具设置 SHALL NOT 展示无法由用户操作解决的部署执行提示或解释既有子 Agent 生命周期的常驻说明；这些约束由运行时执行并记录在运维文档中。

#### Scenario: 用户在设置中关闭 Swarm
- **WHEN** 用户操作 `swarm` switch 从 enabled 变为 disabled 且保存成功
- **THEN** UI 通过 switch 本身展示关闭状态，不额外显示成功说明
- **THEN** 刷新设置后仍读取到 disabled 状态

#### Scenario: 工具设置保持面向用户操作
- **WHEN** 用户查看可用的 `swarm` 工具设置
- **THEN** UI 不显示“需要先启用受治理子 Agent 执行”提示
- **THEN** UI 不显示关闭开关对既有 child 生命周期的常驻说明
- **THEN** UI 不显示工具已启用、已停用或设置已保存的重复成功提示

### Requirement: 用户可以在 Chat UI 管理自动化
系统 SHALL 提供全局“已安排任务”管理入口，展示定时任务和唯一 heartbeat 的启用状态、结果对话、计划摘要、下一次运行、最近结果与历史。

#### Scenario: 查看自动化列表
- **WHEN** 用户打开自动化管理界面
- **THEN** UI 将普通定时任务与 heartbeat 分区、分别计数，并提供一个“新建”入口在配置页选择类型，同时显示各自的状态、时区、下一触发和最近结果

#### Scenario: 两种自动化保持独立语义
- **WHEN** 用户分别创建 heartbeat 和普通定时任务
- **THEN** heartbeat 仅配置固定检查间隔、活动时间窗与静默检查指令，普通定时任务配置 once、interval 或 cron 触发计划及正常执行指令
- **THEN** UI 不将 heartbeat 计入普通定时任务数量，也不向普通定时任务展示 `HEARTBEAT_OK` 语义

#### Scenario: 从统一入口选择创建类型
- **WHEN** 用户点击自动化管理页的“新建”按钮
- **THEN** 配置页先提供“定时任务”与“Heartbeat”类型选择，并根据所选类型显示对应且互不混用的字段

#### Scenario: 编辑计划
- **WHEN** 用户创建或编辑计划
- **THEN** UI 校验结果对话、计划类型、cron/间隔、时区、错过策略、重叠策略和权限包后提交版本化更新
- **THEN** 用户可选择已有结果对话，或创建新的专用对话并完成绑定

#### Scenario: 可视化配置重复计划
- **WHEN** 用户选择定时重复执行
- **THEN** UI 使用每天、工作日、每周或每月以及日期、星期、小时和分钟轮盘生成计划，不要求用户书写 cron 表达式
- **THEN** 对无法映射的旧版自定义 cron，UI 默认保留原计划，直到用户明确选择新的可视化重复方式

#### Scenario: 查看关联 Run
- **WHEN** 用户从运行历史选择一个已创建 Astra Run 的执行
- **THEN** 普通定时任务 UI 导航到绑定的结果对话及完整审计 timeline，生成文件同时进入现有 Artifact/资料库链路；heartbeat UI 导航到其目标对话

#### Scenario: 查看定时任务制品
- **WHEN** 用户打开已产生执行结果的定时任务详情
- **THEN** UI 在“制品”区域按执行展示最终结果文本和该次执行产生的可交付文件
- **THEN** 没有文件的简单输出仍显示为结果制品，并可导航到目标对话查看完整内容

#### Scenario: 查看扩展制品
- **WHEN** 一次执行生成 JSON、表格、图片、HTML 或完成外部写入
- **THEN** UI 区分结构化数据和操作回执，提供数据打开、图片预览、隔离 HTML 预览及安全外部链接
- **THEN** 操作回执不展示原始工具输入、凭据、完整输出或只读调试日志

#### Scenario: 资料库与任务详情展示同一制品
- **WHEN** 定时任务或 heartbeat 产生结果、文件、数据或操作回执
- **THEN** 资料库与对应任务详情从同一制品目录读取，并展示一致的制品 ID、来源、目标对话和内容地址
- **THEN** 资料库可按类型、时间或对话合理展示这些制品，任务详情仅展示属于该任务运行的制品

### Requirement: 用户可以配置低噪音 heartbeat
系统 SHALL 在 Chat UI 提供 heartbeat 启停、周期、活动时间窗、时区和 prompt 配置，并解释静默确认语义。

#### Scenario: 启用 heartbeat
- **WHEN** 用户保存有效 heartbeat 配置
- **THEN** UI 显示下一检查时间和 `HEARTBEAT_OK` 不会产生提醒的说明

#### Scenario: Heartbeat 周期越界
- **WHEN** 用户输入少于 5 分钟或超过 24 小时的检查间隔
- **THEN** UI 使用易读语言说明允许范围、提示如何调整，并在修正前禁用保存操作

#### Scenario: Heartbeat 被阻塞或延后
- **WHEN** 最近 heartbeat 因权限失效、非活动时间或会话繁忙未执行
- **THEN** UI 显示可区分的 blocked、skipped 或 deferred 状态及可操作原因

### Requirement: 快速 Subagent 使用紧凑过程呈现
系统 SHALL 在 standard Run 创建 child 后显示共享 Subagent 活动摘要和可折叠详情，并 MUST NOT 为 standard Run 创建、展示或占位可信执行 DAG。

#### Scenario: 快速 Subagent 正在运行
- **WHEN**standard Run 的 `subagent_summary.total` 大于零
- **THEN**聊天过程显示 running、waiting、completed 数量及关键等待原因
- **THEN**用户可以查看 child 目标、状态、预算摘要、结果或失败

#### Scenario: 快速 Subagent Run 完成
- **WHEN**standard Run 的 children 和 Join 已收敛并生成最终答案
- **THEN**紧凑 Subagent 记录保留在对应对话过程内
- **THEN**对话级可信 DAG 窗格保持隐藏

### Requirement: Chat UI 展示子 Agent 协作而不分裂主会话
系统 SHALL 保持 root Agent 为主对话发言者，并以紧凑、可展开的过程组件展示 children 的目标、状态、等待、预算、Artifacts 和结果。

#### Scenario: child 开始执行
- **WHEN** Run 创建一个或多个 child executions
- **THEN** Chat UI 在当前 Run 内显示子 Agent 汇总，而不创建伪造的独立用户会话或让 child 直接发布最终答案

#### Scenario: child 请求父级输入
- **WHEN** child 进入 waiting_parent
- **THEN** UI 默认显示父级正在处理该请求，只有父级将 Run 转为 waiting_user 时才向用户呈现澄清卡片

### Requirement: 用户可下钻子 Agent 审计和控制
系统 SHALL 允许用户从过程流或执行图查看 child lineage、委派契约摘要、能力/权限摘要、usage、工具和交付物，并在授权范围内取消目标 child。

#### Scenario: 查看 child 详情
- **WHEN** 用户展开一个 child execution
- **THEN** UI 显示经过清洗的结构化详情、父级关系、join policy 和取消影响，且不暴露隐藏 reasoning 或 secret

#### Scenario: 历史 Run 重放
- **WHEN** 用户打开已完成或中断的多 Agent Run
- **THEN** UI 从持久化快照重建相同的 Agent 树、关键时间线和终态，不依赖原 SSE 连接

