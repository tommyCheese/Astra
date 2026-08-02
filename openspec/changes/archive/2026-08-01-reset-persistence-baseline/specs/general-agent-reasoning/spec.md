## ADDED Requirements

### Requirement: Persisted reasoning structures use only current schemas
The system SHALL deserialize Agent state, plan graphs, decisions, and final results only from their current schemas and SHALL NOT synthesize current fields from obsolete persisted shapes.

#### Scenario: Load a legacy Agent state or plan graph
- **WHEN** a persisted reasoning structure uses an earlier schema version or removed field
- **THEN** validation fails explicitly and no compatibility transformation is applied

