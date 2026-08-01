## MODIFIED Requirements

### Requirement: Memory records are structured and scoped
The system SHALL store Memory records with `run`, `task`, `session`, or `user` scope, kind, content, structured data, provenance, confidence, creation time, update time, and optional expiration time; no workspace Memory fields or aliases SHALL exist.

#### Scenario: Store session memory
- **WHEN** the Agent identifies a reusable fact for the current browser session with sufficient provenance
- **THEN** the system stores it with `scope=session` and the Run's session identity

#### Scenario: Store user preference memory
- **WHEN** the user explicitly states a durable preference and a stable user identity exists
- **THEN** the system stores it with `scope=user`
- **THEN** the memory includes provenance indicating the originating Run

### Requirement: Memory recall is explicit and auditable
The system SHALL record which Memory items are recalled into an Agent context and expose selected and excluded reads in the Run audit trail without a shadow-mode field.

#### Scenario: Agent receives recalled memory
- **WHEN** the Agent loop assembles context for a decision with persistent recall enabled
- **THEN** it retrieves Memory items matching current run, task, session, or user namespaces and current eligibility rules
- **THEN** the recall event records selected and excluded Memory IDs and scores

### Requirement: Persistent memory requires provenance
The system SHALL NOT write task, session, or user Memory unless the Memory has valid provenance and a confidence value.

#### Scenario: Missing provenance
- **WHEN** the Agent proposes a task, session, or user Memory write without provenance
- **THEN** the system rejects the write
- **THEN** the rejection is recorded in the Run events

