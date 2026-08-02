# clean-start-persistence Specification

## Purpose
TBD - created by archiving change reset-persistence-baseline. Update Purpose after archive.
## Requirements
### Requirement: Astra has one current database baseline
The system SHALL create the complete current database schema from one baseline revision and SHALL NOT support upgrading databases created by earlier Astra revisions.

#### Scenario: Start with no database
- **WHEN** Astra starts without a database file
- **THEN** the current baseline creates every required table, index, constraint, and current field

#### Scenario: Start with an old database
- **WHEN** an existing database does not identify the current baseline
- **THEN** startup fails with an explicit reset-required error instead of mutating or backfilling the database

### Requirement: Persisted payloads use only current schemas
Runtime configuration, Run snapshots, Agent state, plan graphs, Memory records, permission records, and browser-persisted application data SHALL be accepted only in their current schema.

#### Scenario: Read obsolete persisted payload
- **WHEN** a persisted payload contains a removed schema version, field alias, or legacy shape
- **THEN** the system rejects or ignores the whole obsolete payload and does not convert it into current data

### Requirement: Clean start removes project database data
The development reset SHALL remove the active Astra SQLite database, its sidecar files, and project-local database backups after the database process has stopped.

#### Scenario: Complete clean reset
- **WHEN** the clean-start operation completes
- **THEN** none of the previous Tasks, Runs, memories, settings, audit records, shares, artifacts metadata, or schedules remain in a project database

