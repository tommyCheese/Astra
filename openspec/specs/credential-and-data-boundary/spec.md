# credential-and-data-boundary Specification

## Purpose
TBD - created by archiving change add-effect-aware-approvals-and-task-workspaces. Update Purpose after archive.
## Requirements
### Requirement: Long-lived credentials are brokered outside model and Workspace context
The system SHALL keep long-lived credentials out of prompts, Workspace files, Artifacts, ordinary logs, and ToolResults, and SHALL provide execution-time credentials only through a controlled broker.

#### Scenario: External service invocation
- **WHEN** an approved tool needs service authorization
- **THEN** the broker provides a short-lived credential constrained to the service, tenant, action, resource, subject, and TTL

#### Scenario: Tool prints a credential
- **WHEN** a process attempts to write brokered credential material to stdout, Workspace, Artifact, or logs
- **THEN** the system redacts or blocks the output and records a security event

### Requirement: Sensitive data access is a distinct permission
Reading credentials, secrets, personal data, browser sessions, private documents, or protected memory SHALL require data-specific permission and SHALL NOT be implied by generic file or tool access.

#### Scenario: Generic read grant encounters a secret
- **WHEN** a Run has ordinary Workspace read permission but requests a secret-labelled file
- **THEN** the permission engine requires a separate sensitive-data decision or denies access

### Requirement: Data-flow state constrains later egress
The system SHALL track relevant trust and sensitivity labels of data observed by a Run and SHALL reevaluate later external writes, sharing, messaging, uploads, and network requests against that DataFlowState.

#### Scenario: Untrusted content precedes sensitive-data egress
- **WHEN** a Run has consumed untrusted instructions and sensitive data and then proposes sending content to an external destination
- **THEN** the system requires explicit destination- and payload-aware approval or denies the action

#### Scenario: Read-only search does not grant command networking
- **WHEN** Web Search is allowed for a Run
- **THEN** Bash subprocesses and unrelated tools do not inherit unrestricted network access

### Requirement: Data use, retention, and destination are policy-controlled
Data permissions SHALL distinguish reading, temporary processing, persistence, indexing, Library promotion, logging, and external disclosure.

#### Scenario: Temporary-use data
- **WHEN** data is authorized only for temporary analysis
- **THEN** it is excluded from persistent memory, Library, downloadable Artifacts, and retained logs

