## ADDED Requirements

### Requirement: Agent Runtime root exposes only the control-loop structure
The Agent Runtime package root SHALL contain only the canonical loop, its contracts, and capability composition; concrete tool execution, policies, and environment adapters SHALL live in named functional packages.

#### Scenario: Developer reads the complete core loop
- **WHEN** a developer opens the Agent Runtime package root
- **THEN** the execution flow can be followed through a small set of structural modules without traversing concrete repositories, model clients, sandboxes, or tools

### Requirement: Planning uses a typed Node execution boundary
Planning SHALL depend on a public typed Node execution contract and SHALL NOT import infrastructure bootstrap modules or require infrastructure to invoke executor-private methods.

#### Scenario: Node worker executes a planned node
- **WHEN** a planning worker delegates node execution to the configured runtime
- **THEN** it uses public typed operations with dependency direction from infrastructure to application contracts only

### Requirement: Runtime capability construction is typed
Standard and Trusted runtime composition SHALL construct the fixed capability slots from explicit typed dependencies and SHALL NOT pass collaborator or infrastructure bags typed as `dict[str, Any]`.

#### Scenario: Trusted runtime is assembled
- **WHEN** Trusted execution capabilities are composed
- **THEN** static inspection identifies every required dependency and no generic assembly dictionary or field-for-field runtime assembly model is required
