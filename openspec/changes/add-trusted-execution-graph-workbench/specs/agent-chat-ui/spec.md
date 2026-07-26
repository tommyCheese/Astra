## MODIFIED Requirements

### Requirement: Chat UI is the primary Agent interface
The system SHALL present the Agent frontend as a chat-style interface with user messages, Agent messages, tool events, reflections, source evidence, and final answers. Trusted Runs with a canonical Plan SHALL use the trusted execution graph workbench as the primary process representation, while standard Runs SHALL retain the lightweight chronological process representation.

#### Scenario: User submits a standard message
- **WHEN** the user sends a task in standard mode
- **THEN** the UI appends a user message to the conversation
- **THEN** the system creates a Run and streams or polls Agent progress into the same conversation
- **THEN** the UI does not create a placeholder graph

#### Scenario: User submits a trusted message
- **WHEN** the user sends a task in trusted mode
- **THEN** the UI immediately shows the trusted planning state
- **THEN** the canonical DAG replaces the planning placeholder once the complete Plan is persisted
- **THEN** later node and version changes update the same graph workbench

#### Scenario: Agent returns final answer
- **WHEN** a Run completes
- **THEN** the UI displays the final answer as an Agent message with findings, sources, caveats, and verification notes
- **THEN** a trusted Run keeps its completed graph accessible immediately before the answer

### Requirement: Audit details remain accessible
The system SHALL preserve access to the Plan graph, version history, run timeline, Agent turns, tool calls, artifacts, Evidence Pack, memory reads, memory writes, and verification report from the chat UI without presenting hidden chain-of-thought.

#### Scenario: User expands trusted audit details
- **WHEN** the user opens the audit details of a trusted Run
- **THEN** the UI keeps the canonical Plan graph and version lineage in the independent conversation-level graph pane
- **THEN** the expanded audit details present the chronological Trace without duplicating the graph
- **THEN** selecting a Plan node scopes related turns, tools, artifacts, evaluations and evidence

#### Scenario: User expands standard audit details
- **WHEN** the user opens the audit details of a standard Run
- **THEN** the UI presents the real chronological Trace, artifacts, memory events and verification data
- **THEN** the UI does not present a Plan version or graph
