## ADDED Requirements

### Requirement: Approval is based on invocation effects
The runtime SHALL generate a backend-trusted, frozen ActionEffectPlan for every resolved tool invocation and SHALL use its required permissions, side effects, resource scopes, reversibility, and risk to decide whether the invocation may execute.

#### Scenario: Read-only web search
- **WHEN** `web_search` is permitted by platform policy and its invocation effect contains only bounded `network_read`
- **THEN** the invocation executes without interactive approval in all execution modes

#### Scenario: First persistent file creation
- **WHEN** any tool proposes creating a previously nonexistent file in the Task Workspace
- **THEN** the effect is classified as `workspace_write` and requires approval in request-approval mode

#### Scenario: Tool name does not determine approval
- **WHEN** two invocations of the same tool have different effects
- **THEN** the runtime independently decides approval from each frozen ActionEffectPlan

### Requirement: Approval grants are scoped by effect and resource
The system SHALL create backend-generated grants that constrain scope, effect kinds, resources, tool invocation attributes, and lifetime, and SHALL NOT allow a grant to expand the frozen or platform-permitted permissions.

#### Scenario: Default similar approval
- **WHEN** the user accepts a safe similar-action proposal without explicitly choosing Task scope
- **THEN** the grant applies only to matching actions in the current Run

#### Scenario: Explicit Task approval
- **WHEN** the UI clearly describes a Task-scoped grant and the user explicitly selects it
- **THEN** matching later Runs in the same Task may reuse the grant

#### Scenario: Write grant does not permit deletion
- **WHEN** a grant permits `workspace_write` under `reports/**`
- **THEN** a later `workspace_delete` action still requires a separate approval

### Requirement: Approval remains bound to the frozen action
The system MUST validate the frozen tool input, working directory, effect plan, analyzer version, and permission scope before executing an approved action.

#### Scenario: Effect plan changes after approval
- **WHEN** resumed execution no longer matches the approved effect plan or analyzer integrity data
- **THEN** the system refuses execution and creates a new analysis or auditable integrity error

