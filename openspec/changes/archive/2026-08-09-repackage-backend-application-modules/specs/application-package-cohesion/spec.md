## ADDED Requirements

### Requirement: Application modules are grouped by owned capability
The backend SHALL place Agent Runtime and Run Management implementation modules in functional subpackages whose names identify the capability they own.

#### Scenario: Developer locates tool approval execution
- **WHEN** a developer navigates the Agent Runtime application package
- **THEN** approval, authorization, invocation, observation, and plugin execution modules are located under the tooling capability package rather than a flat services directory

#### Scenario: Developer locates conversation retention
- **WHEN** a developer navigates Run Management
- **THEN** conversation commands, context, lifecycle, and retention are located together under the conversations capability package

### Requirement: Canonical paths replace flat compatibility facades
The backend MUST update internal consumers to canonical functional package paths and MUST NOT retain old-path modules or re-export shims solely for source compatibility.

#### Scenario: Scan old imports
- **WHEN** production, tests, scripts, and benchmarks are scanned after migration
- **THEN** no import references an old flat Agent Runtime services or Run Management module path

### Requirement: Package dependency direction is enforced
Lower-level Agent Runtime packages SHALL NOT depend on the orchestration package, and package roots SHALL remain bounded so new peer-module sprawl is detected by architecture checks.

#### Scenario: Lower package imports execution
- **WHEN** context, tooling, decisions, completion, or shared code imports the Agent Runtime execution package
- **THEN** the architecture check fails

#### Scenario: Root-level file is added
- **WHEN** a new implementation module is added directly to either reorganized package root
- **THEN** the architecture check fails and requires placement in an owned functional package

### Requirement: Repackaging preserves behavior
The package migration SHALL preserve Fast and Trusted execution, tool approval, completion, recovery, run creation and continuation, conversation lifecycle, event projection, and dispatch behavior.

#### Scenario: Complete backend suite runs after migration
- **WHEN** the canonical module moves and import rewrites are complete
- **THEN** the complete backend suite passes without API, schema, migration, event, or runtime-policy changes
