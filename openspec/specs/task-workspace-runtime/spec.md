# task-workspace-runtime Specification

## Purpose
TBD - created by archiving change add-effect-aware-approvals-and-task-workspaces. Update Purpose after archive.
## Requirements
### Requirement: A Task owns a persistent isolated Workspace
The system SHALL provide one isolated persistent Workspace per Task and SHALL make its current file state available across the Task's Runs according to enforced permissions.

#### Scenario: Later Run uses an earlier output
- **WHEN** a Run creates an approved file and a later Run in the same Task needs that file
- **THEN** the later Run can read the file without copying it through an unrelated one-time Sandbox output

#### Scenario: Another Task attempts access
- **WHEN** a tool invocation belongs to a different Task
- **THEN** it cannot mount, read, or modify the first Task's Workspace

### Requirement: Sandbox mounts enforce the effect plan
Each ToolCall Sandbox SHALL mount the Task Workspace as absent, read-only, or read-write according to its frozen ActionEffectPlan and platform policy.

#### Scenario: Read-only command
- **WHEN** a Bash invocation is classified as read-only
- **THEN** the Workspace is mounted read-only and filesystem mutation is prevented even if static analysis was incomplete

#### Scenario: Approved write
- **WHEN** a workspace-write action has a valid approval or applicable auto-approval decision
- **THEN** the Sandbox may receive the minimum required read-write Workspace access

#### Scenario: Tool requires no files
- **WHEN** a web search invocation does not need Task files
- **THEN** the Workspace is not mounted into its Sandbox

### Requirement: Run completion creates a Workspace checkpoint
The system SHALL persist a checkpoint reference and bounded Workspace manifest for each completed or interrupted Run that changed or observed Task file state.

#### Scenario: Service restart
- **WHEN** the service restarts between Runs
- **THEN** the Task Workspace and latest valid checkpoint remain available without depending on a still-running tool container

### Requirement: Workspace content is untrusted
The system MUST treat files, instructions, configuration, dependencies, and executable content in a Task Workspace as untrusted data and MUST NOT allow them to grant permissions, alter execution mode, forge approvals, or modify control-plane policy.

#### Scenario: Workspace contains policy override instructions
- **WHEN** a README, project instruction file, downloaded page, or generated document tells Astra to ignore approval or Sandbox restrictions
- **THEN** the content may be considered task context but cannot change platform policy, ActionEffectPlan requirements, or approval decisions

#### Scenario: Library script is restored
- **WHEN** an executable or script is copied from Library into a Task Workspace
- **THEN** it remains untrusted data and requires a new effect decision before execution

### Requirement: Control-plane components never load Workspace code
The orchestrator, Tool Router, effect analyzer, approval validator, and Workspace service MUST execute from verified read-only runtime code and MUST NOT import, preload, discover, or execute modules and plugins from a Task Workspace.

#### Scenario: Workspace shadows a runtime module
- **WHEN** the Workspace contains a binary or module with the same name as a runtime dependency
- **THEN** fixed runtime paths and sanitized module search paths prevent it from being loaded by control-plane or tool-runtime components

#### Scenario: Workspace contains startup configuration
- **WHEN** the Workspace contains shell rc files, Git configuration, language startup modules, hooks, or preload settings
- **THEN** routine Workspace access ignores them unless an explicitly analyzed and permitted action requires project-code execution

### Requirement: Workspace execution uses a sanitized environment
Every Sandbox that can access a Task Workspace SHALL use an explicit minimal environment, fixed trusted PATH, isolated HOME and configuration directories, no host credentials or control sockets, and bounded non-root execution.

#### Scenario: Malicious executable is added to PATH
- **WHEN** the Workspace contains a program named like a trusted system command
- **THEN** the trusted fixed PATH does not resolve the Workspace program implicitly

#### Scenario: Package lifecycle script
- **WHEN** dependency installation would trigger a project or package lifecycle script
- **THEN** scripts are disabled by default or represented as a separately analyzed executable effect before they may run

#### Scenario: Git hook or filter
- **WHEN** a Git operation encounters Workspace hooks, filters, external diff commands, pagers, or repository-controlled configuration
- **THEN** isolated Git configuration prevents implicit execution

### Requirement: Workspace paths and archives cannot escape containment
Workspace ingestion, restoration, extraction, scanning, preview, and delivery SHALL reject path traversal, unsafe links, special files, path ambiguity, archive bombs, and resources exceeding configured quotas.

#### Scenario: Symlink points outside the Workspace
- **WHEN** a Workspace file is or resolves through a symbolic or hard link outside the Task root
- **THEN** the operation is rejected without reading, writing, previewing, or delivering the external target

#### Scenario: Archive contains traversal entries
- **WHEN** an uploaded, downloaded, or Library archive contains absolute paths, `..`, link pivots, excessive expansion, or too many entries
- **THEN** extraction is rejected or safely bounded without modifying paths outside the Workspace

#### Scenario: Filename contains shell syntax
- **WHEN** a filename contains leading dashes, spaces, newlines, command substitutions, or shell metacharacters
- **THEN** non-shell tools pass it as structured data or argv and do not execute its contents as a command

#### Scenario: Workspace exceeds resource limits
- **WHEN** file count, bytes, depth, inode use, change count, or scan work exceeds policy
- **THEN** the system stops the operation with an auditable quota error before control-plane availability is affected

