## ADDED Requirements

### Requirement: Safe Astra process events have an AG-UI projection
The system SHALL project safe answer, process, reasoning-summary, tool, plan, verification, and multi-Agent facts into ordered AG-UI events while continuing to persist and stream the authoritative Astra events required for native replay and audit.

#### Scenario: Reasoning summary streams through AG-UI
- **WHEN** Astra emits an allowed incremental reasoning summary
- **THEN** the adapter emits a valid AG-UI reasoning-message lifecycle distinct from assistant answer content
- **THEN** hidden provider reasoning and internal scratchpads are not projected

#### Scenario: Internal-only event is committed
- **WHEN** an Astra event is required for audit or recovery but has no safe public interaction meaning
- **THEN** it remains available to authorized Astra projections and emits no AG-UI event

### Requirement: AG-UI process projection remains ordered under concurrency
The system SHALL preserve Run-level event order, stable Agent lineage, and terminal precedence when projecting concurrent Agent activity, and SHALL use bounded aggregation or authoritative Activity snapshots instead of flooding the client with every internal progress update.

#### Scenario: Multiple children update concurrently
- **WHEN** several Subagents emit progress and tool facts in a short interval
- **THEN** the client receives bounded Agent-tree Activity updates that preserve current aggregate and terminal state
- **THEN** a stale running update cannot overwrite a later completed state

