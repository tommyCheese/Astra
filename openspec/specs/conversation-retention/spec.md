# conversation-retention Specification

## Purpose
TBD - created by archiving change add-conversation-retention-aging. Update Purpose after archive.
## Requirements
### Requirement: Conversation retention is deployment-configurable
The system SHALL expose an explicit enable flag, retention age in days, sweep interval in seconds, and maximum batch size for background conversation aging, and SHALL perform no retention deletion when the enable flag is false.

#### Scenario: Retention is disabled
- **WHEN** the backend starts with conversation retention disabled
- **THEN** it does not select or delete aged conversations
- **THEN** it emits an observable disabled status

#### Scenario: Retention is enabled
- **WHEN** the backend starts with valid enabled retention settings
- **THEN** it performs one bounded sweep and schedules later sweeps at the configured interval

### Requirement: Only safe aged conversations are eligible
The system SHALL select only conversations whose last activity is at or before the retention cutoff, that contain at least one Run, whose Runs are all terminal, that are not pinned, and that have no active share.

#### Scenario: Old terminal conversation is eligible
- **WHEN** an unpinned and unshared conversation has only terminal Runs and its last activity is older than the cutoff
- **THEN** the next sweep may select it for deletion

#### Scenario: Protected conversation is excluded
- **WHEN** an aged conversation is pinned, actively shared, empty, or contains a non-terminal Run
- **THEN** the retention sweep does not delete it

#### Scenario: Recently active conversation is excluded
- **WHEN** a conversation's updated timestamp is newer than the retention cutoff
- **THEN** the retention sweep does not delete it

### Requirement: Aging is bounded and race-safe
The system SHALL select candidates oldest-first up to the configured batch size and SHALL reload and revalidate each candidate immediately before deletion.

#### Scenario: Backlog exceeds one batch
- **WHEN** eligible conversation count exceeds the configured batch size
- **THEN** one sweep deletes no more than the batch size
- **THEN** later sweeps can continue draining the backlog

#### Scenario: Candidate changes before deletion
- **WHEN** a selected conversation becomes protected, active, recent, or absent before deletion
- **THEN** the worker skips it without deleting protected state

### Requirement: Aging uses canonical lifecycle cleanup
The system SHALL use the same conversation lifecycle deletion service for user-requested and retention-requested deletion, including database-owned records, artifact content, task workspace content, and share records.

#### Scenario: Eligible conversation is deleted
- **WHEN** the worker deletes an eligible conversation
- **THEN** its conversation and owned database records are removed
- **THEN** its artifact content and task workspace are cleaned on a best-effort basis

#### Scenario: External cleanup fails
- **WHEN** database deletion succeeds but artifact or workspace cleanup fails
- **THEN** the worker records a warning and continues processing later candidates

### Requirement: Retention operation is observable and resilient
The system SHALL log aggregate selected, deleted, skipped, and failed counts for every enabled sweep and SHALL isolate per-conversation failures.

#### Scenario: One deletion fails
- **WHEN** deletion of one selected conversation raises an error
- **THEN** the failure is counted and logged
- **THEN** later candidates in the same batch are still evaluated

#### Scenario: Backend shuts down
- **WHEN** application shutdown begins
- **THEN** the periodic retention worker stops promptly and is awaited

