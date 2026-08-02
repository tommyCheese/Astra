# extension-trust-and-delegation Specification

## Purpose
TBD - created by archiving change add-effect-aware-approvals-and-task-workspaces. Update Purpose after archive.
## Requirements
### Requirement: External tools and extensions have verified identities
The system SHALL inventory MCP servers, plugins, Skills, Hooks, custom Agents, and runtime components with source, provider identity, version, content or schema digest, trust level, and maximum permissions.

#### Scenario: MCP server annotation claims read-only
- **WHEN** an untrusted MCP server advertises a read-only or non-destructive annotation
- **THEN** the annotation is treated as a hint and does not independently authorize the tool

#### Scenario: Extension changes after trust
- **WHEN** a trusted Hook, Skill script, plugin, tool schema, or MCP server identity changes
- **THEN** execution is blocked until policy accepts the new identity or the component is reviewed again

### Requirement: Managed policy can restrict extension sources
Administrators SHALL be able to allowlist or deny MCP servers, plugin marketplaces, Hooks, Skills, custom Agents, and project-local configuration, and SHALL be able to require managed-only policy components.

#### Scenario: Untrusted project config
- **WHEN** a Task Workspace is not trusted for executable configuration
- **THEN** project-local extensions, Hooks, tool registrations, environment files, and auto-allow rules are not loaded

### Requirement: Delegated Agents receive attenuated permissions
Every subagent SHALL have an independent identity and SHALL receive only an explicit subset of its parent Agent's permissions, resources, credentials, tools, data, network, and budget.

#### Scenario: Subagent requests broader access
- **WHEN** a child Agent requests a capability not delegated by its parent
- **THEN** the request is denied or escalated to the original authorized user and cannot be self-approved

#### Scenario: Reviewer evaluates an action
- **WHEN** a reviewer Agent evaluates an approval request
- **THEN** it can inspect bounded approval context and return a decision but cannot execute the action or expand its permissions

### Requirement: Tool catalogs are frozen per Run
The system SHALL freeze a Tool Catalog Snapshot for each Run and SHALL fail closed if a provider identity, tool schema, permission annotation, or version changes incompatibly during execution.

#### Scenario: MCP server changes tool schema mid-Run
- **WHEN** the server exposes a materially different schema after the Run starts
- **THEN** pending invocations are not executed under the old authorization

