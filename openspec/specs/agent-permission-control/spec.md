# agent-permission-control Specification

## Purpose
TBD - created by archiving change add-effect-aware-approvals-and-task-workspaces. Update Purpose after archive.
## Requirements
### Requirement: All controlled actions use a unified permission decision
The system SHALL normalize controlled actions into PermissionRequests containing subject identity, action, resource, conditions, data labels, delegation chain, and frozen effect integrity, and SHALL return an auditable `allow`, `ask`, or `deny` decision.

#### Scenario: Non-tool permission request
- **WHEN** an Agent requests a credential, creates a subagent, exports sensitive data, connects an MCP server, or changes a security-relevant setting
- **THEN** the action is evaluated by the same Permission Engine even when it is not represented as a normal tool call

#### Scenario: Decision explanation
- **WHEN** the system allows, asks, or denies a request
- **THEN** it records the matched policy sources, reason code, enforced scope, subject, resource, and decision time

#### Scenario: Tool invocation uses the unified authorization entry
- **WHEN** a resolved tool invocation has a frozen ActionEffectPlan
- **THEN** ToolSpec attenuation, protected-resource policy, execution mode, Run or Task Grants, unattended Permission Bundles, and data-flow egress constraints are evaluated through one Permission Engine entry that returns the only actionable `allow`, `ask`, or `deny` result
- **AND** the Agent loop, tool adapter, and approval UI do not independently reinterpret or override that result

### Requirement: Higher-trust deny and ask policies cannot be overridden
The system MUST evaluate policy tiers from platform and managed policy through user, Task, Run, and one-time grants, MUST apply deny before ask before allow, and MUST prevent lower-trust sources from widening higher-trust policy.

#### Scenario: Task grant conflicts with managed deny
- **WHEN** a Task Grant permits an action denied by organization policy
- **THEN** the action is denied without offering an approval that could override the managed rule

#### Scenario: Workspace attempts to configure permissions
- **WHEN** Workspace content or project configuration declares a broader allow rule
- **THEN** it is ignored as an authorization source unless separately registered as trusted managed policy

### Requirement: Grants are revocable conditional leases
Permission Grants SHALL have explicit subject, action, resource, conditions, scope, provenance, expiry, usage limits, and revocation state rather than acting as permanent booleans.

#### Scenario: Tool version changes
- **WHEN** a Grant is constrained to a tool provider, version, schema, or analyzer digest and that identity changes
- **THEN** the Grant no longer authorizes the invocation

#### Scenario: Task grant is revoked
- **WHEN** a user or administrator revokes a Task Grant
- **THEN** pending and future matching invocations are denied or returned to ask immediately

### Requirement: Protected resources cannot be modified by ordinary grants
The system SHALL identify protected security, identity, credential, audit, policy, runtime, cross-Task, and control-plane resources that ordinary Run or Task Grants cannot authorize.

#### Scenario: Agent attempts to alter its own permission records
- **WHEN** a tool proposes modifying approval, grant, audit, Sandbox, credential, or policy state through the Task execution path
- **THEN** the action is denied regardless of execution mode

### Requirement: Unattended runs use predeclared permission bundles
Headless, scheduled, and background Runs SHALL execute only within an explicit Permission Bundle and SHALL fail closed when an action requires approval outside that bundle.

#### Scenario: Scheduled task needs a new network destination
- **WHEN** an unattended Run encounters an unapproved destination
- **THEN** it pauses or fails without automatically expanding the bundle

### Requirement: Permission state is inspectable and explainable
The system SHALL provide authorized users and administrators with current policies, grants, delegation chains, tool trust, credential usage, and explanations for permission decisions.

#### Scenario: User reviews Task permissions
- **WHEN** a user opens Task permission management
- **THEN** the UI lists active Run and Task Grants, their scopes, sources, expiry, last use, and revocation controls

#### Scenario: User reviews permissions without security expertise
- **WHEN** a user opens Task permission management
- **THEN** the UI first summarizes allowed actions, effective duration, usage, task files, and recent safety activity in human language, while identity chains, catalog digests, and raw policy events remain available under progressive technical disclosure

