## MODIFIED Requirements

### Requirement: Chat UI is the primary Agent interface
The system SHALL present the Agent frontend as a chat-style interface with user messages, a live auditable process, tool events, reflections, source evidence, and final answers.

#### Scenario: User submits a message
- **WHEN** the user sends a task from the chat composer
- **THEN** the UI appends a user message to the conversation
- **THEN** the UI immediately appends an active process entry before the first model decision completes
- **THEN** the system streams or polls Agent progress into the same conversation

#### Scenario: Agent returns final answer
- **WHEN** a run completes
- **THEN** the UI displays the final answer as an Agent message with findings, sources, caveats, and verification notes
- **THEN** the completed auditable process remains accessible immediately before the answer

### Requirement: Tool activity is visible but compact
The system SHALL display tool calls as compact live process rows inside the conversation, update their status while the Run is executing, and allow users to expand safe details.

#### Scenario: Web search event
- **WHEN** `web_search` starts or completes
- **THEN** the active process view shows tool name and current status without waiting for the final answer
- **THEN** the completed row can show candidate count and warnings if present

#### Scenario: Web fetch event
- **WHEN** `web_fetch` starts or completes
- **THEN** the active process view shows the current fetch state
- **THEN** the completed row can show source URL, extraction strategy, quality score, and warnings if present

#### Scenario: Run has no tool calls
- **WHEN** a Run completes without invoking a tool
- **THEN** the process summary omits the tool-call count instead of displaying zero calls

### Requirement: Conversation supports Web Agent run status
The system SHALL map run, phase, turn and tool statuses to a live user-readable process inside the conversation.

#### Scenario: Run is executing
- **WHEN** the Agent loop is planning, selecting an action, searching, reading, reflecting, verifying, or composing
- **THEN** the process panel summary displays “思考中” for the entire non-terminal Run, including gaps between internal process events
- **THEN** a compact animated three-dot indicator beside “思考中” continuously communicates activity without animating the outer panel
- **THEN** reduced-motion preferences receive the same indicator in a static form
- **THEN** the process panel displays the active state and completed prior entries in execution order

#### Scenario: Run reaches a terminal state
- **WHEN** the Run completes, fails, becomes blocked, waits for the user, or is cancelled
- **THEN** the process panel summary displays “思考完成”
- **THEN** the summary no longer displays “思考中”
- **THEN** the activity indicator is absent

#### Scenario: Active process panel displays internal progress
- **WHEN** a Run is active and its process panel is expanded
- **THEN** the process panel outer container remains static without a loading animation
- **THEN** completed internal steps use a transparent background rather than a solid green background
- **THEN** only the current running step uses a neutral loading-pane animation
- **THEN** reduced-motion preferences receive an equivalent static loading pane on the current step

#### Scenario: A decision produces reasoning and tool activity
- **WHEN** `selecting_action` starts for an Agent turn
- **THEN** the UI presents “正在分析下一步” as a distinct decision-group anchor rather than an ordinary flat row
- **THEN** the same turn's reasoning summary, tool calls, and reflections are indented beneath that anchor
- **THEN** a continuous alignment line and child connectors make the ownership visible at a glance

#### Scenario: A completed process is reopened
- **WHEN** the live process has reached a terminal RunView or the conversation is loaded from history
- **THEN** the UI reconstructs the same decision groups from stable turn and tool-call identifiers
- **THEN** process entries do not return to a flat timeline after completion or reload

#### Scenario: Tool completes before the next decision starts
- **WHEN** a ToolCall completes and the Run has not started its next phase or reached a terminal state
- **THEN** the completed tool row remains visible under its decision group
- **THEN** the same group immediately displays “正在评估执行结果” as the only running loading pane
- **THEN** the transition is removed and replaced by the next active phase when that phase arrives
- **THEN** the transition is removed rather than retained as a completed historical row when a terminal event arrives
- **THEN** the process panel never presents an active Run with only completed rows during this interval

#### Scenario: User controls the process panel
- **WHEN** the first process panel starts without a saved last-click preference
- **THEN** the UI displays it collapsed
- **WHEN** the user manually collapses or expands it
- **THEN** only that process panel changes state
- **THEN** existing process panels keep their own state
- **THEN** the chosen state becomes the initial state for the next newly created process panel

#### Scenario: Answer begins
- **WHEN** answer streaming begins
- **THEN** the current process panel preserves its own expansion state

#### Scenario: A new conversation begins
- **WHEN** the user starts a new conversation after manually expanding or collapsing another process panel
- **THEN** its first process panel uses that last-click state as its initial state
- **THEN** changing it does not retroactively change any existing process panel

#### Scenario: Run is blocked
- **WHEN** the run becomes blocked
- **THEN** the UI shows a clear blocked message with reason and any required user action

### Requirement: Conversation strategy persists across application starts
The system SHALL persist reasoning effort, tool-call limit, planning strategy, reflection enabled state, and reflection trigger in the database as the current conversation strategy preference.

#### Scenario: Application starts with a saved strategy
- **WHEN** the chat application starts and a saved conversation strategy exists
- **THEN** the UI restores all five strategy options from the database
- **THEN** newly created Runs use the restored strategy

#### Scenario: User reviews and adjusts the tool-call budget
- **WHEN** the conversation strategy menu is open
- **THEN** the UI explains that fast allows 0–5 tool calls, balanced allows 6–15, and deep allows 16–50
- **THEN** the tool-call control is constrained to the selected effort's range
- **THEN** changing reasoning effort replaces an out-of-range value with that effort's documented default
- **THEN** a manually selected value is persisted and included in the next Run's reasoning policy

#### Scenario: User changes a strategy option
- **WHEN** the user manually changes any conversation strategy option
- **THEN** the UI applies the option immediately
- **THEN** the complete current strategy is persisted to the database in change order
- **THEN** ordinary renders, Run completion, and service restarts do not overwrite it with defaults

#### Scenario: No saved strategy exists
- **WHEN** the application reads conversation strategy preferences for the first time
- **THEN** the backend creates and returns the documented default strategy
