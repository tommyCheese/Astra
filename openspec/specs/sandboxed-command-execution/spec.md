# sandboxed-command-execution Specification

## Purpose
TBD - created by archiving change add-interactive-tool-approvals. Update Purpose after archive.
## Requirements
### Requirement: Bash commands execute only in an isolated sandbox
The system SHALL provide a versioned `bash_execute` tool that executes commands only through `sandbox.remote` with no host workspace mount, no Docker socket, no host environment inheritance, and no public network by default.

#### Scenario: Execute a simple approved command
- **WHEN** an approved `bash_execute` call contains a valid command and the sandbox is available
- **THEN** the command runs as a non-root user in a one-time container with a read-only root filesystem and bounded CPU, memory, PID, and wall time

#### Scenario: Sandbox unavailable
- **WHEN** the configured sandbox backend is unavailable
- **THEN** `bash_execute` is absent from eligible manifests or fails before command execution with an auditable backend-unavailable error

### Requirement: Command results are bounded and structured
`bash_execute` SHALL return the child command exit code and sanitized, length-bounded stdout and stderr without treating a non-zero child exit code as a sandbox infrastructure failure.

#### Scenario: Command exits non-zero
- **WHEN** the child Bash command exits with a non-zero status
- **THEN** the tool returns that exit code and bounded logs in a successful tool result envelope

#### Scenario: Command exceeds wall time
- **WHEN** execution exceeds the configured wall-time limit
- **THEN** the Sandbox Job is terminated and the tool returns a `sandbox_timeout` error

### Requirement: Command tool availability is explicitly controlled
The system SHALL register `bash_execute` only when its persisted tool switch is enabled and the sandbox backend is available.

#### Scenario: Command tool disabled
- **WHEN** the administrator disables the `bash_execute` tool setting
- **THEN** new Runs do not expose or execute the command tool

