## ADDED Requirements

### Requirement: Result processors emit canonical evidence fragments
Applicable result processors SHALL convert tool-specific output into schema-validated canonical evidence fragments without persisting directly or expanding permissions.

#### Scenario: Web read completes
- **WHEN** a Web read ToolResultEnvelope is processed
- **THEN** the processor emits source snapshot and passage fragments and the host supplies trusted invocation lineage before persistence

### Requirement: Host controls evidence persistence
Only host-managed EvidenceWriter code SHALL persist evidence fragments, and plugins MUST NOT receive unrestricted repository or database access for evidence ingestion.

#### Scenario: Plugin emits malformed evidence
- **WHEN** a result processor returns an evidence fragment that fails the canonical schema
- **THEN** the invocation fails safely before the fragment is persisted
