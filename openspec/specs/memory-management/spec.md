# memory-management Specification

## Purpose
TBD - created by archiving change implement-core-web-agent-loop. Update Purpose after archive.
## Requirements
### Requirement: Memory records are structured and scoped
The system SHALL store persistent Memory records with scope, kind, content, structured data, provenance, confidence, creation time, update time, and optional expiration time.

#### Scenario: Store workspace memory
- **WHEN** the Agent identifies a reusable workspace fact with sufficient provenance
- **THEN** the system stores it with `scope=workspace`, a kind, confidence, and provenance pointing to the source run, tool call, or artifact

#### Scenario: Store user preference memory
- **WHEN** the user explicitly states a durable preference
- **THEN** the system stores it with `scope=user`
- **THEN** the memory includes provenance indicating the originating task or message

### Requirement: Run memory is available during the loop
The system SHALL maintain run memory for current-goal facts, observations, failures, source summaries, and intermediate conclusions.

#### Scenario: Observation becomes run memory
- **WHEN** a tool returns a useful observation
- **THEN** the Agent may store a summarized run memory item linked to the turn and ToolCall
- **THEN** later turns can retrieve that item without re-reading the full tool output

### Requirement: Memory recall is explicit and auditable
The system SHALL record which memory items are recalled into an Agent context and expose memory reads in the run audit trail.

#### Scenario: Agent receives recalled memory
- **WHEN** the Agent loop assembles context for a decision
- **THEN** it retrieves memory items matching scope, kind, recency, confidence, and workspace or user identity
- **THEN** the turn records memory IDs or summaries used in the decision context

### Requirement: Persistent memory requires provenance
The system SHALL NOT write workspace or user memory unless the memory has provenance and a confidence value.

#### Scenario: Missing provenance
- **WHEN** the Agent proposes a workspace or user memory write without provenance
- **THEN** the system rejects the write
- **THEN** the rejection is recorded in the turn or run events

### Requirement: Memory writes are visible in the UI
The system SHALL expose proposed and committed memory writes in the run view so users can inspect what the Agent learned.

#### Scenario: Memory write shown in chat audit
- **WHEN** the Agent commits a memory item during a run
- **THEN** the chat UI shows a compact memory event
- **THEN** the detailed audit view shows scope, kind, content, confidence, and provenance

### Requirement: Memory does not replace evidence
The system SHALL treat memory as context, not as an unchecked factual source for final answers unless it includes auditable provenance.

#### Scenario: Final answer uses memory
- **WHEN** a final answer relies on recalled memory
- **THEN** the answer cites the memory provenance or includes a caveat that the memory is contextual

