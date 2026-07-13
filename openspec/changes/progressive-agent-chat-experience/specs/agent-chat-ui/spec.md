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
- **THEN** the process panel displays the active state and completed prior entries in execution order

#### Scenario: User controls the process panel
- **WHEN** a process first starts and the user has not changed its expansion state
- **THEN** the UI displays it expanded
- **WHEN** the user manually collapses or expands it
- **THEN** later ordinary deltas respect that choice

#### Scenario: Answer begins
- **WHEN** answer streaming begins and the user has not manually overridden process expansion
- **THEN** the process panel may collapse to prioritize answer reading while remaining accessible

#### Scenario: Run is blocked
- **WHEN** the run becomes blocked
- **THEN** the UI shows a clear blocked message with reason and any required user action
