## ADDED Requirements

### Requirement: Frozen Profile snapshots use only the current composition schema
The system SHALL persist and reconstruct Run Agent Profile snapshots only with the current composition schema and SHALL NOT infer roles or documents from legacy snapshots.

#### Scenario: Load an obsolete Profile snapshot
- **WHEN** a Run snapshot has an unsupported composition schema or legacy unversioned marker
- **THEN** reconstruction fails explicitly rather than substituting default documents or legacy role mappings

