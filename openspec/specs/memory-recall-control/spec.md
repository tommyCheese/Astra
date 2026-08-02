# memory-recall-control Specification

## Purpose
TBD - created by archiving change remove-shadow-memory-recall. Update Purpose after archive.
## Requirements
### Requirement: Persistent recall uses one explicit switch
The runtime SHALL expose persistent Memory recall through a boolean `recall_enabled` setting and SHALL reject the removed `cross_session_mode` field in new updates.

#### Scenario: Recall disabled
- **WHEN** `recall_enabled` is false
- **THEN** the Agent does not retrieve or inject persistent Memory

#### Scenario: Recall enabled
- **WHEN** `recall_enabled` is true
- **THEN** the Agent retrieves eligible Memory, records the decision, and injects the final selection as untrusted context

### Requirement: Legacy recall configuration fails closed
The runtime SHALL migrate legacy `on` to enabled and legacy `off` or `shadow` to disabled without failing startup.

#### Scenario: Restart with legacy shadow
- **WHEN** Astra starts with persisted `cross_session_mode=shadow`
- **THEN** `recall_enabled` is false and no persistent Memory is injected

### Requirement: Session Memory crosses Task boundaries
The system SHALL assign new Runs a client session identity and SHALL allow `scope=session` Memory to be recalled by Runs in other Tasks with the same identity.

#### Scenario: Two tasks in one session
- **WHEN** session Memory is created in one Task and another Task creates a Run with the same session identity
- **THEN** the second Run can retrieve the Memory when recall is enabled

#### Scenario: Different sessions are isolated
- **WHEN** two Runs have different session identities
- **THEN** session Memory from one is not eligible for the other

### Requirement: Workspace Memory is removed from production use
The system SHALL reject new `scope=workspace` writes, SHALL exclude workspace namespaces from recall, and SHALL migrate attributable workspace Memory to its source Task.

#### Scenario: New workspace write
- **WHEN** an Agent proposes workspace-scoped Memory
- **THEN** the write is rejected as unsupported

### Requirement: Historical audits remain readable
The system SHALL preserve historical shadow and workspace audit payloads while producing neither new shadow recalls nor new workspace Memory.

#### Scenario: Read historical audit
- **WHEN** a user inspects a historical event
- **THEN** its original markers remain readable

### Requirement: Documentation explains current boundaries
The Memory documentation SHALL explain that task scope follows one Task across Runs, session scope crosses Tasks within one browser session, and Task Workspace is only the Task's file and execution space.

#### Scenario: Compare task and session
- **WHEN** a user reads the scope documentation
- **THEN** the boundaries and examples for task and session are distinct

