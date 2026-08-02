## ADDED Requirements

### Requirement: Persisted permission records use the current identity model
The system SHALL read permission identities, grants, and audit records only from the current Run/Task identity schema and SHALL NOT expose compatibility projections for legacy single-lease or unscoped authorization data.

#### Scenario: Read obsolete authorization data
- **WHEN** a persisted grant or identity lacks the current binding and scope fields
- **THEN** the record is rejected or treated as unauthorized without constructing a compatibility view

