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

