## MODIFIED Requirements

### Requirement: Policy-driven tool resolution
Tool Router and the execution-time capability selector SHALL resolve tools according to semantic task capability, manifest security capability, permissions, risk, execution backend, frozen catalog, current Run policy, and budget rather than a Plan-level concrete tool name or hardcoded tool-name allowlist.

#### Scenario: Resolve an allowed sandboxed chart tool
- **WHEN** an active node requires `data.visualize`, `chart.render` declares that semantic task capability, and the Run allows its sandboxed compute and artifact effects
- **THEN** the selector offers the manifest and Router validates its concrete invocation

#### Scenario: Reject a disallowed capability
- **WHEN** a matching tool is registered but its security capability is not allowed by the Run policy
- **THEN** the tool is excluded or Router returns an auditable `tool_not_allowed` or `permission_denied`
- **THEN** the tool is not executed

#### Scenario: Resolve equivalent provider tools
- **WHEN** multiple eligible tools declare the semantic capability required by the active node
- **THEN** the runtime exposes all matching candidates without requiring a Plan change

### Requirement: Only eligible manifests enter model context
Context assembler SHALL expose only tool manifests that are present in the Run's frozen catalog, currently eligible under Run policy and backend availability, and matched by the active node's semantic requirements; it SHALL expose safe resolution metadata separately from tool inputs and secrets.

#### Scenario: Sandbox backend unavailable
- **WHEN** a visualization tool is configured but its Sandbox Executor is unavailable
- **THEN** the model context does not contain that tool
- **THEN** resolution metadata records a safe capability-unavailable reason

#### Scenario: Active node has multiple providers
- **WHEN** multiple healthy and allowed provider tools satisfy the same active semantic requirement
- **THEN** every matching manifest enters the execution decision context in deterministic order
- **THEN** no provider credential or secret configuration enters the resolution metadata
