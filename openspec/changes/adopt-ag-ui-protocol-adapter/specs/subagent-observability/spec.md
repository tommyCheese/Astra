## ADDED Requirements

### Requirement: Subagent lineage has a versioned AG-UI Activity projection
The system SHALL expose authorized Subagent lineage and aggregate execution state through an `astra.agent_tree` Activity snapshot and compatible deltas, while the persisted Astra event log and RunView remain authoritative.

#### Scenario: First child is created
- **WHEN** an AG-UI Run gains its first visible child Agent
- **THEN** the client receives an Agent-tree Activity snapshot containing stable child and parent identifiers, safe objectives, status, aggregate counts, and fallback text

#### Scenario: Child status changes
- **WHEN** a known child changes from running to waiting or terminal state on a compatible baseline
- **THEN** the system may emit a bounded Activity delta for that stable Agent identity

### Requirement: Agent-tree resynchronization preserves terminal precedence and privacy
The system SHALL replace the Agent-tree Activity with a sanitized authoritative snapshot after lineage gaps, incompatible revisions, or reconnects, and MUST NOT include hidden reasoning, secrets, private sibling context, unauthorized paths, or raw sensitive tool inputs.

#### Scenario: Client misses concurrent child events
- **WHEN** the client detects a revision gap while several children start and finish
- **THEN** it does not apply uncertain deltas and later replaces the tree from an authoritative snapshot
- **THEN** completed children are not reverted to running by stale updates

#### Scenario: Generic AG-UI client receives Agent activity
- **WHEN** a client lacks the Astra Agent-tree renderer
- **THEN** it can still display safe active, waiting, completed, and failed counts with a human-readable fallback summary
