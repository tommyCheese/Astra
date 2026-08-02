# agent-chat-ui Specification

## Purpose
TBD - created by archiving change implement-core-web-agent-loop. Update Purpose after archive.
## Requirements
### Requirement: Chat UI is the primary Agent interface
The system SHALL present the Agent frontend as a chat-style interface with user messages, Agent messages, tool events, reflections, source evidence, and final answers.

#### Scenario: User submits a message
- **WHEN** the user sends a task from the chat composer
- **THEN** the UI appends a user message to the conversation
- **THEN** the system creates a run and streams or polls Agent progress into the same conversation

#### Scenario: Agent returns final answer
- **WHEN** a run completes
- **THEN** the UI displays the final answer as an Agent message with findings, sources, caveats, and verification notes

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
系统 SHALL 仅在可信模式下提供推理强度、工具预算、规划和反思等详细对话策略控制，并 SHALL 在快速模式下保留模型与执行审批控制。

#### Scenario: 快速回答打开模型菜单
- **WHEN** standard 模式用户打开模型菜单
- **THEN** UI 允许选择模型
- **THEN** UI 不把详细可信策略表现为当前快速回答的生效设置

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

系统 SHALL 将聊天中的“思考”定义为 Astra 运行阶段和可公开审计摘要的展示，并 MUST NOT 使用模型思考开关或深度决定过程面板的产生、可见性、展开状态或事件内容来源。界面 MUST NOT 展示或依赖 Provider 隐藏思维链。

#### Scenario: 用户关闭模型思考
- **WHEN** 当前模型思考被关闭且 Run 正在执行
- **THEN** 界面仍根据运行事件显示“思考中”、阶段、工具活动和公开摘要
- **THEN** Run 结束后仍显示对应的完成状态

#### Scenario: 用户切换模型思考深度
- **WHEN** 用户从低深度切换到高深度或反向切换
- **THEN** 过程面板的显示规则、折叠偏好和事件协议保持不变
- **THEN** 界面不会把 Provider 隐藏推理 token 渲染为过程摘要

#### Scenario: 用户查看思考说明
- **WHEN** 用户查看模型思考控件或过程面板说明
- **THEN** 界面明确说明模型思考控制模型生成行为
- **THEN** 界面明确说明聊天中的“思考”是 Astra 的公开执行过程摘要

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
系统 SHALL 仅在 Run 存在规范 Plan DAG 时展示计划版本、节点与依赖，并 MUST NOT 为 standard Run 展示虚构的 Plan 版本或空 DAG 占位。

#### Scenario: 查看快速响应过程
- **WHEN** 用户展开已完成 standard Run 的过程
- **THEN** UI 展示真实决策和工具事件
- **THEN** UI 不展示 Plan 版本或 Plan 节点区域

#### Scenario: 查看可信执行过程
- **WHEN** 用户展开已完成 trusted Run 的过程
- **THEN** UI 展示真实 Plan 版本、节点状态和依赖

