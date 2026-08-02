## ADDED Requirements

### Requirement: Normalized Google results retain logical query lineage
Google search candidates SHALL include their originating logical query identity, canonical URL, provider rank, retrieval time, and normalized constraint audit.

#### Scenario: Batched Google search
- **WHEN** multiple logical queries are executed through Google
- **THEN** candidates from each response retain the corresponding logical query identity

### Requirement: Google credentials remain outside grounding evidence
Google API keys and search-engine credentials MUST NOT appear in search traces, candidates, constraint audits, warnings, ToolCall output, or evidence records.

#### Scenario: Search result is persisted
- **WHEN** a Google search result is normalized and ingested into the Evidence Ledger
- **THEN** no credential value is present in the persisted evidence
